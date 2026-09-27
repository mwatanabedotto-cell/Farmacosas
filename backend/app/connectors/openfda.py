"""Conector del NDC Directory de openFDA (productos comercializados en EE. UU.).

Documentación: https://open.fda.gov/apis/drug/ndc/
"""

import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

import httpx

BASE_URL = "https://api.fda.gov"
TAMANO_PAGINA = 1000  # máximo permitido por openFDA
SKIP_MAXIMO = 25000  # openFDA no admite skip mayor
URL_DAILYMED = "https://dailymed.nlm.nih.gov/dailymed/lookup.cfm?setid={}"
URL_NDC = "https://api.fda.gov/drug/ndc.json?search=product_ndc:%22{}%22"

# Categorías de la FDA que corresponden a genéricos.
CATEGORIAS_GENERICO = {"ANDA", "NDA AUTHORIZED GENERIC"}
CATEGORIAS_MARCA = {"NDA", "BLA"}
SEPARADORES = set(" ,-(/")
MAX_SET_IDS_POR_CONSULTA = 10

# Secciones de la ficha (drug label) que se importan, en orden de lectura clínica.
# Las fichas antiguas usan "warnings"/"precautions" y "nursing_mothers" en lugar de las actuales.
SECCIONES_FICHA: dict[str, str] = {
    "boxed_warning": "Advertencia en recuadro (boxed warning)",
    "indications_and_usage": "Indicaciones y uso",
    "dosage_and_administration": "Posología y administración",
    "dosage_forms_and_strengths": "Formas farmacéuticas y concentraciones",
    "contraindications": "Contraindicaciones",
    "warnings_and_cautions": "Advertencias y precauciones",
    "warnings": "Advertencias",
    "precautions": "Precauciones",
    "drug_interactions": "Interacciones",
    "use_in_specific_populations": "Uso en poblaciones específicas",
    "pregnancy": "Embarazo",
    "lactation": "Lactancia",
    "nursing_mothers": "Lactancia",
    "pediatric_use": "Uso pediátrico",
    "geriatric_use": "Uso geriátrico",
    "adverse_reactions": "Reacciones adversas",
    "overdosage": "Sobredosis",
    "mechanism_of_action": "Mecanismo de acción",
    "pharmacodynamics": "Farmacodinamia",
    "pharmacokinetics": "Farmacocinética",
    "clinical_pharmacology": "Farmacología clínica",
}


class OpenFDAError(Exception):
    pass


@dataclass
class ResultadoBusqueda:
    resultados: list[dict]
    total: int
    truncado: bool


class OpenFDAClient:
    def __init__(
        self,
        api_key: str | None = None,
        http: httpx.Client | None = None,
        max_resultados: int = 5000,
        pausa_segundos: float = 0.3,
        max_reintentos: int = 4,
        dormir: Callable[[float], None] = time.sleep,
    ):
        self.api_key = api_key
        self.http = http or httpx.Client(base_url=BASE_URL, timeout=30)
        self.max_resultados = min(max_resultados, SKIP_MAXIMO + TAMANO_PAGINA)
        self.pausa_segundos = pausa_segundos
        self.max_reintentos = max_reintentos
        self.dormir = dormir

    def buscar_ndc(self, busqueda: str) -> ResultadoBusqueda:
        """Devuelve todos los productos que cumplen la búsqueda, paginando."""
        resultados: list[dict] = []
        total = 0
        skip = 0
        while True:
            limite = min(TAMANO_PAGINA, self.max_resultados - skip)
            datos = self._get("/drug/ndc.json", {"search": busqueda, "limit": limite, "skip": skip})
            if datos is None:
                break
            pagina = datos.get("results", [])
            total = datos.get("meta", {}).get("results", {}).get("total", len(pagina))
            resultados.extend(pagina)
            skip += len(pagina)
            if not pagina or skip >= total or skip >= self.max_resultados:
                break
            self.dormir(self.pausa_segundos)
        return ResultadoBusqueda(resultados=resultados, total=total, truncado=len(resultados) < total)

    def buscar_fichas(self, set_ids: list[str]) -> dict[str, dict]:
        """Devuelve las fichas técnicas (drug label) vigentes de los set_id indicados, por set_id."""
        set_ids = set_ids[:MAX_SET_IDS_POR_CONSULTA]
        if not set_ids:
            return {}
        busqueda = " ".join(f'set_id:"{s.replace(chr(34), "")}"' for s in set_ids)
        datos = self._get("/drug/label.json", {"search": busqueda, "limit": len(set_ids)})
        return {f["set_id"]: f for f in (datos or {}).get("results") or [] if f.get("set_id")}

    def _get(self, ruta: str, params: dict) -> dict | None:
        if self.api_key:
            params = {**params, "api_key": self.api_key}
        for intento in range(self.max_reintentos + 1):
            try:
                resp = self.http.get(ruta, params=params)
            except httpx.TransportError as e:
                error = f"Error de red: {e}"
            else:
                if resp.status_code == 200:
                    return resp.json()
                if resp.status_code == 404:
                    return None  # openFDA responde 404 cuando no hay coincidencias
                if resp.status_code != 429 and resp.status_code < 500:
                    raise OpenFDAError(f"openFDA respondió {resp.status_code}: {resp.text[:300]}")
                error = f"openFDA respondió {resp.status_code}"
            if intento < self.max_reintentos:
                self.dormir(2 ** (intento + 1))
        raise OpenFDAError(f"{error} (tras {self.max_reintentos} reintentos)")


def parsear_terminos(terminos: str | None) -> list[list[str]]:
    """'amoxicillin+clavulanate|clavulanic acid' -> [['amoxicillin'], ['clavulanate', 'clavulanic acid']]"""
    if not terminos:
        return []
    return [
        [alt.strip().lower() for alt in componente.split("|") if alt.strip()]
        for componente in terminos.split("+")
        if componente.strip()
    ]


def construir_busqueda(componentes: list[list[str]]) -> str:
    partes = []
    for alternativas in componentes:
        terminos = [f'active_ingredients.name:"{a.replace(chr(34), "")}"' for a in alternativas]
        partes.append(f"({' '.join(terminos)})" if len(terminos) > 1 else terminos[0])
    return " AND ".join(partes)


def ingrediente_coincide(nombre: str, termino: str) -> bool:
    """El ingrediente es el término, o el término seguido de sal/forma (p. ej. 'METOPROLOL TARTRATE')."""
    n = " ".join(nombre.lower().split())
    return n == termino or (n.startswith(termino) and n[len(termino)] in SEPARADORES)


def producto_coincide(ingredientes: list[str], componentes: list[list[str]]) -> bool:
    """El producto contiene exactamente los componentes del principio activo (sin otros fármacos)."""
    if not ingredientes or not componentes:
        return False
    cubiertos: set[int] = set()
    for ingrediente in ingredientes:
        indices = [
            i for i, alternativas in enumerate(componentes)
            if any(ingrediente_coincide(ingrediente, a) for a in alternativas)
        ]
        if not indices:
            return False
        cubiertos.update(indices)
    return len(cubiertos) == len(componentes)


def nombre_cumple_filtro(nombre: str, filtro: str | None) -> bool:
    """Aplica el filtro por nombre comercial: 'regex' debe coincidir; '!regex' no debe coincidir."""
    if not filtro:
        return True
    excluir = filtro.startswith("!")
    coincide = re.search(filtro[1:] if excluir else filtro, nombre, re.IGNORECASE) is not None
    return coincide != excluir


def es_excluido(producto: dict) -> bool:
    """Excluye principios activos a granel y productos homeopáticos."""
    if producto.get("finished") is False:
        return True
    return "HOMEOPATHIC" in (producto.get("marketing_category") or "").upper()


def fusionar_duplicados(productos: list[dict]) -> list[dict]:
    """Une registros con el mismo product_ndc.

    La FDA puede listar un mismo producto en varias fichas (SPL), cada una con sus
    propias presentaciones. Se conserva la ficha con menor spl_id (orden estable
    entre ejecuciones) y se suman las presentaciones de todas.
    """
    grupos: dict[str, list[dict]] = {}
    for p in productos:
        grupos.setdefault(p.get("product_ndc") or "", []).append(p)
    fusionados = []
    for ndc, grupo in grupos.items():
        grupo.sort(key=lambda p: p.get("spl_id") or "")
        if not ndc or len(grupo) == 1:
            fusionados.extend(grupo)
            continue
        envases = {}
        for p in grupo:
            for envase in p.get("packaging") or []:
                envases.setdefault(envase.get("package_ndc"), envase)
        fusionados.append({**grupo[0], "packaging": list(envases.values())})
    return fusionados


def _fecha_iso(valor: str | None) -> str | None:
    if not valor or len(valor) != 8 or not valor.isdigit():
        return None
    return f"{valor[:4]}-{valor[4:6]}-{valor[6:]}"


def estado_listado(producto: dict, hoy: date | None = None) -> str:
    vencimiento = _fecha_iso(producto.get("listing_expiration_date"))
    if vencimiento and vencimiento < (hoy or date.today()).isoformat():
        return "listado_vencido"
    return "vigente"


def es_generico(categoria: str | None) -> bool | None:
    categoria = (categoria or "").upper()
    if categoria in CATEGORIAS_GENERICO:
        return True
    if categoria in CATEGORIAS_MARCA:
        return False
    return None


def normalizar_producto(producto: dict, hoy: date | None = None) -> dict:
    """Convierte un registro del NDC Directory al formato de ProductoComercial."""
    ingredientes = producto.get("active_ingredients") or []
    set_ids = (producto.get("openfda") or {}).get("spl_set_id") or []
    categoria = producto.get("marketing_category")
    ndc = producto["product_ndc"]
    return {
        "id_externo": ndc,
        "nombre_comercial": (producto.get("brand_name") or producto.get("generic_name") or ndc).strip(),
        "laboratorio": producto.get("labeler_name"),
        "forma_farmaceutica": producto.get("dosage_form"),
        "via": ", ".join(producto.get("route") or []) or None,
        "composicion": "; ".join(
            f"{i.get('name', '')} {i.get('strength', '')}".strip() for i in ingredientes
        ) or None,
        "tipo_producto": producto.get("product_type"),
        "categoria_registro": categoria,
        "n_registro": producto.get("application_number"),
        "es_generico": es_generico(categoria),
        "estado": estado_listado(producto, hoy),
        "url_fuente": URL_NDC.format(ndc),
        "url_ficha": URL_DAILYMED.format(set_ids[0]) if set_ids else None,
        "spl_set_id": set_ids[0] if set_ids else None,
        "presentaciones": sorted(
            (
                {
                    "id_externo": p.get("package_ndc"),
                    "descripcion": p.get("description"),
                    "fecha_inicio_comercializacion": _fecha_iso(p.get("marketing_start_date")),
                }
                for p in producto.get("packaging") or []
            ),
            key=lambda p: p["id_externo"] or "",
        ),
    }

# Secciones "madre" que en openFDA incluyen el texto de sus subsecciones.
SUBSECCIONES = {
    "use_in_specific_populations": ("pregnancy", "lactation", "nursing_mothers", "pediatric_use", "geriatric_use"),
    "clinical_pharmacology": ("mechanism_of_action", "pharmacodynamics", "pharmacokinetics"),
}


def secciones_sin_duplicar(secciones: dict[str, str]) -> list[str]:
    """Códigos a mostrar: omite subsecciones cuyo texto ya está dentro de su sección madre."""
    omitir = set()
    for madre, hijas in SUBSECCIONES.items():
        texto_madre = secciones.get(madre)
        if texto_madre:
            omitir.update(h for h in hijas if secciones.get(h) and secciones[h] in texto_madre)
    return [c for c in secciones if c not in omitir]


def normalizar_ficha(ficha: dict) -> dict:
    """Convierte un registro de /drug/label al formato de FichaTecnica."""
    info = ficha.get("openfda") or {}
    marca = (info.get("brand_name") or [None])[0]
    if marca and marca.strip().upper() in {"N/A", "NA", "NONE"}:
        marca = None
    generico = (info.get("generic_name") or [None])[0]
    titulo = " ".join(x for x in [marca, f"({generico.lower()})" if generico and generico != marca else None] if x)
    secciones = []
    for orden, codigo in enumerate(SECCIONES_FICHA):
        partes = [p.strip() for p in ficha.get(codigo) or [] if p and p.strip()]
        if partes:
            secciones.append({"codigo": codigo, "orden": orden, "texto": "\n\n".join(partes)})
    return {
        "set_id": ficha["set_id"],
        "spl_id": ficha["id"],
        "version": ficha.get("version"),
        "fecha_efectiva": _fecha_iso(ficha.get("effective_time")),
        "titulo": titulo or generico or ficha["set_id"],
        "laboratorio": (info.get("manufacturer_name") or [None])[0],
        "n_registro": (info.get("application_number") or [None])[0],
        "tipo_producto": (info.get("product_type") or [None])[0],
        "url": URL_DAILYMED.format(ficha["set_id"]),
        "secciones": secciones,
    }
