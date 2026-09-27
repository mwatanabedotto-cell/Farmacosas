"""Conector de INVIMA (Colombia): Código Único de Medicamentos (CUM) en datos.gov.co.

Solo se usan los conjuntos oficiales publicados por Invima, y en cada ejecución se comprueba
su propietario: en datos.gov.co hay copias de terceros con la misma atribución.
Cada fila es registro sanitario (expediente) × presentación (CUM) × principio activo × rol.
"""

import re
from collections.abc import Callable
from datetime import datetime

import httpx

from app.connectors.base import ClienteHTTP, ErrorConector, ResultadoProductos, marca
from app.connectors.cima import codigos_atc
from app.texto import normalizar

BASE_URL = "https://www.datos.gov.co"
# Registros vigentes y en trámite de renovación (legalmente comercializables mientras se renuevan).
CONJUNTOS = {
    "i7cb-raxc": "CÓDIGO ÚNICO DE MEDICAMENTOS VIGENTES",
    "vgr4-gemg": "CÓDIGO ÚNICO DE MEDICAMENTOS EN TRÁMITE DE RENOVACIÓN",
}
PROPIETARIO = "invima"
TAMANO_PAGINA = 5000
MAX_PAGINAS = 20
URL_FUENTE = "https://www.datos.gov.co/resource/{}.json?expediente={}"
MARCA_REGISTRADA = re.compile(r"[®™]")


class InvimaError(ErrorConector):
    pass


def _fecha(valor: str | None) -> str | None:
    """'08/23/2012' (MM/DD/AAAA) o ISO -> '2012-08-23'."""
    if not valor:
        return None
    for formato in ("%m/%d/%Y", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%d"):
        try:
            return datetime.strptime(valor[:23] if "T" in valor else valor, formato).date().isoformat()
        except ValueError:
            continue
    return None


def _es_muestra(fila: dict) -> bool:
    return normalizar(fila.get("muestramedica") or "") in ("si", "s")


def es_generico(nombre: str, nombres_genericos: list[str]) -> bool:
    """Heurística (el CUM no lo indica): con ® o ™ es marca; si empieza por la denominación común, genérico."""
    if MARCA_REGISTRADA.search(nombre):
        return False
    compacto = normalizar(nombre).replace(" ", "")
    return any(compacto.startswith(g.split()[0]) for g in nombres_genericos if g.strip())


def agrupar_expedientes(filas: list[dict], conjunto: str, nombres_genericos: list[str]) -> dict[str, dict]:
    """Agrupa las filas por registro sanitario y las convierte al formato de ProductoComercial."""
    grupos: dict[str, list[dict]] = {}
    for fila in filas:
        if fila.get("expediente") and fila.get("producto"):
            grupos.setdefault(fila["expediente"], []).append(fila)

    productos = {}
    for expediente, grupo in grupos.items():
        primera = grupo[0]
        presentaciones: dict[str, dict] = {}
        activos: dict[str, str] = {}
        for fila in grupo:
            if _es_muestra(fila):
                continue
            pa = (fila.get("principioactivo") or "").strip()
            if pa and pa not in activos:
                cantidad = " ".join(x for x in (fila.get("cantidad"), fila.get("unidadmedida")) if x)
                activos[pa] = pa if re.search(r"\d", pa) or not cantidad else f"{pa} {cantidad}"
            if (fila.get("estadocum") or "").lower() != "activo":
                continue
            cum = f"{fila.get('expedientecum') or expediente}-{fila.get('consecutivocum')}"
            presentaciones.setdefault(cum, {
                "id_externo": cum,
                "descripcion": (fila.get("descripcioncomercial") or "").strip() or None,
                "fecha_inicio_comercializacion": _fecha(fila.get("fechaactivo")),
            })
        nombre = " ".join(primera["producto"].split())
        vias = sorted({f["viaadministracion"].strip() for f in grupo if f.get("viaadministracion")})
        productos[expediente] = {
            "id_externo": expediente,
            "nombre_comercial": marca(nombre),
            "descripcion": nombre,
            "laboratorio": primera.get("titular"),
            "forma_farmaceutica": primera.get("formafarmaceutica"),
            "via": ", ".join(vias) or None,
            "composicion": "; ".join(activos.values()) or None,
            "n_registro": primera.get("registrosanitario"),
            "es_generico": es_generico(nombre, nombres_genericos),
            # Registro vigente (o en renovación) con al menos un CUM activo que no sea muestra médica.
            "estado": "vigente" if presentaciones else "no_comercializado",
            "url_fuente": URL_FUENTE.format(conjunto, expediente),
            "presentaciones": sorted(presentaciones.values(), key=lambda p: p["id_externo"]),
        }
    return productos


class InvimaClient:
    def __init__(
        self,
        http: httpx.Client | None = None,
        app_token: str | None = None,
        pausa_segundos: float = 0.2,
        max_reintentos: int = 4,
        dormir: Callable[[float], None] | None = None,
    ):
        cabeceras = {"Accept": "application/json", **({"X-App-Token": app_token} if app_token else {})}
        self.http = http or httpx.Client(base_url=BASE_URL, timeout=60, headers=cabeceras)
        kwargs = {"dormir": dormir} if dormir else {}
        self.cliente = ClienteHTTP(self.http, pausa_segundos, max_reintentos, error=InvimaError, **kwargs)
        self._verificados: set[str] = set()

    def verificar_conjunto(self, conjunto: str) -> None:
        """Comprueba que el conjunto de datos lo publica Invima (una vez por cliente)."""
        if conjunto in self._verificados:
            return
        meta = self.cliente.get_json(f"/api/views/{conjunto}.json", {}) or {}
        propietario = ((meta.get("owner") or {}).get("displayName") or "").strip().lower()
        if propietario != PROPIETARIO:
            raise InvimaError(f"El conjunto {conjunto} no está publicado por Invima (propietario: {propietario or '?'})")
        self._verificados.add(conjunto)

    def filas_por_atc(self, conjunto: str, codigos: list[str]) -> tuple[list[dict], bool]:
        """Devuelve (filas, truncado)."""
        self.verificar_conjunto(conjunto)
        lista = ", ".join(f"'{c}'" for c in codigos if re.fullmatch(r"[A-Z]\d{2}[A-Z]{2}\d{2}", c))
        filas: list[dict] = []
        for pagina in range(MAX_PAGINAS):
            lote = self.cliente.get_json(f"/resource/{conjunto}.json", {
                "$where": f"atc in ({lista})",
                "$order": ":id",
                "$limit": TAMANO_PAGINA,
                "$offset": pagina * TAMANO_PAGINA,
            }) or []
            filas.extend(lote)
            if len(lote) < TAMANO_PAGINA:
                return filas, False
            self.cliente.pausa()
        return filas, True


class ConectorInvima:
    """Adaptador del CUM de INVIMA al servicio genérico de sincronización de productos."""

    codigo_fuente = "invima_cum"
    datos_fuente = {
        "nombre": "INVIMA — Código Único de Medicamentos (datos.gov.co)",
        "agencia": "INVIMA",
        "pais": "CO",
        "url": "https://www.datos.gov.co/Salud-y-Protecci-n-Social/C-DIGO-NICO-DE-MEDICAMENTOS-VIGENTES/i7cb-raxc",
        "licencia": "Datos abiertos del Gobierno de Colombia; ver condiciones en datos.gov.co",
    }

    def __init__(self, cliente: InvimaClient):
        self.cliente = cliente

    def buscar(self, principio) -> ResultadoProductos:
        codigos = codigos_atc(principio)
        if not codigos:
            raise InvimaError("El principio activo no tiene un código ATC de 7 caracteres")
        genericos = [normalizar(t) for t in (principio.texto_busqueda or "").split("|")]
        productos: dict[str, dict] = {}
        truncado = False
        n_filas = 0
        for conjunto in CONJUNTOS:
            filas, trunc = self.cliente.filas_por_atc(conjunto, codigos)
            truncado = truncado or trunc
            n_filas += len(filas)
            for expediente, datos in agrupar_expedientes(filas, conjunto, genericos).items():
                productos.setdefault(expediente, datos)
        return ResultadoProductos(
            productos=list(productos.values()),
            recibidos=set(productos),
            total=n_filas,
            truncado=truncado,
            consulta=f"atc in ({', '.join(codigos)})",
        )

    def completar(self, datos: dict) -> dict:
        return datos
