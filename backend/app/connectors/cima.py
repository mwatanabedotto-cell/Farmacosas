"""Conector de CIMA (AEMPS): medicamentos autorizados en España y sus fichas técnicas.

Documentación: https://cima.aemps.es/cima/rest (API REST pública).
Los medicamentos se buscan por código ATC de 7 caracteres, que identifica el principio
activo (y su uso: p. ej. AAS antiagregante B01AC06 frente a analgésico N02BA01).
"""

import re
from collections.abc import Callable
from datetime import datetime, timezone

import httpx

from app.connectors.base import ClienteHTTP, ErrorConector, ResultadoProductos, html_a_texto, marca

BASE_URL = "https://cima.aemps.es/cima/rest"
URL_DETALLE = "https://cima.aemps.es/cima/publico/detalle.html?nregistro={}"
TIPO_FICHA_TECNICA = 1
MAX_PAGINAS = 20  # 200 por página: hasta 4000 medicamentos por código ATC

# Secciones de la ficha técnica (resumen de características del producto) que se importan.
PREFIJOS_FICHA = ("4", "5.1", "5.2")

class CimaError(ErrorConector):
    pass


def _fecha_ms(ms: int | None) -> str | None:
    if not ms:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).date().isoformat()


def estado(med: dict) -> str:
    est = med.get("estado") or {}
    if est.get("rev"):
        return "revocado"
    if est.get("susp"):
        return "suspendido"
    if not med.get("comerc"):
        return "no_comercializado"
    return "vigente"


def ficha_tecnica(med: dict) -> dict | None:
    return next((d for d in med.get("docs") or [] if d.get("tipo") == TIPO_FICHA_TECNICA), None)


def normalizar_medicamento(med: dict) -> dict:
    """Datos de la lista de /medicamentos (lo que se compara para detectar cambios)."""
    ft = ficha_tecnica(med)
    return {
        "id_externo": med["nregistro"],
        "nombre_comercial": marca(med["nombre"]),
        "descripcion": med["nombre"],
        "laboratorio": med.get("labtitular"),
        "forma_farmaceutica": (med.get("formaFarmaceutica") or {}).get("nombre"),
        "via": ", ".join(v["nombre"] for v in med.get("viasAdministracion") or [] if v.get("nombre")) or None,
        "composicion": med.get("dosis"),
        "tipo_producto": med.get("cpresc"),
        "categoria_registro": "EFG" if med.get("generico") else "Biosimilar" if med.get("biosimilar") else None,
        "n_registro": med["nregistro"],
        "es_generico": bool(med.get("generico")),
        "estado": estado(med),
        "problema_suministro": bool(med.get("psum")),
        "url_fuente": URL_DETALLE.format(med["nregistro"]),
        "url_ficha": (ft or {}).get("urlHtml") or (ft or {}).get("url"),
        # Solo se usa como ficha de referencia si CIMA la ofrece segmentada por secciones.
        "spl_set_id": med["nregistro"] if ft and ft.get("secc") else None,
    }


class CimaClient:
    def __init__(
        self,
        http: httpx.Client | None = None,
        pausa_segundos: float = 0.2,
        max_reintentos: int = 4,
        dormir: Callable[[float], None] | None = None,
    ):
        self.http = http or httpx.Client(base_url=BASE_URL, timeout=40, headers={"Accept": "application/json"})
        kwargs = {"dormir": dormir} if dormir else {}
        self.cliente = ClienteHTTP(self.http, pausa_segundos, max_reintentos, error=CimaError, **kwargs)

    def medicamentos_por_atc(self, atc: str) -> tuple[list[dict], int, bool]:
        """Devuelve (medicamentos, total, truncado)."""
        resultados: list[dict] = []
        total = 0
        for pagina in range(1, MAX_PAGINAS + 1):
            datos = self.cliente.get_json("/medicamentos", {"atc": atc, "pagina": pagina})
            if not datos:
                break
            lote = datos.get("resultados") or []
            total = datos.get("totalFilas", len(lote))
            resultados.extend(lote)
            if not lote or len(resultados) >= total:
                break
            self.cliente.pausa()
        return resultados, total, len(resultados) < total

    def medicamento(self, nregistro: str) -> dict | None:
        self.cliente.pausa()
        return self.cliente.get_json("/medicamento", {"nregistro": nregistro})

    def secciones_ficha(self, nregistro: str) -> list[dict]:
        self.cliente.pausa()
        return self.cliente.get_json(f"/docSegmentado/contenido/{TIPO_FICHA_TECNICA}", {"nregistro": nregistro}) or []


def codigos_atc(principio) -> list[str]:
    """Códigos ATC de 7 caracteres del principio activo (p. ej. 'J01XD01 / P01AB01')."""
    return [c for c in re.split(r"[\s/,;]+", principio.atc or "") if re.fullmatch(r"[A-Z]\d{2}[A-Z]{2}\d{2}", c)]


class ConectorCima:
    """Adaptador de CIMA al servicio genérico de sincronización de productos."""

    codigo_fuente = "cima_medicamentos"
    datos_fuente = {
        "nombre": "CIMA — Centro de Información online de Medicamentos (AEMPS)",
        "agencia": "AEMPS",
        "pais": "ES",
        "url": "https://cima.aemps.es/",
        "licencia": "Ver condiciones de uso en https://cima.aemps.es/",
    }

    def __init__(self, cliente: CimaClient):
        self.cliente = cliente

    def buscar(self, principio) -> ResultadoProductos:
        codigos = codigos_atc(principio)
        if not codigos:
            raise CimaError("El principio activo no tiene un código ATC de 7 caracteres")
        productos: dict[str, dict] = {}
        total, truncado = 0, False
        for codigo in codigos:
            meds, n, trunc = self.cliente.medicamentos_por_atc(codigo)
            total += n
            truncado = truncado or trunc
            for med in meds:
                if med.get("nregistro") and med.get("nombre"):
                    productos.setdefault(med["nregistro"], normalizar_medicamento(med))
        return ResultadoProductos(
            productos=list(productos.values()),
            recibidos=set(productos),
            total=total,
            truncado=truncado,
            consulta=" | ".join(f"atc={c}" for c in codigos),
        )

    def completar(self, datos: dict) -> dict:
        """Añade presentaciones (código nacional) y composición desde el detalle del medicamento."""
        detalle = self.cliente.medicamento(datos["id_externo"])
        if not detalle:
            return datos
        activos = sorted(detalle.get("principiosActivos") or [], key=lambda p: p.get("orden") or 0)
        composicion = "; ".join(
            " ".join(str(x) for x in (p.get("nombre"), p.get("cantidad"), p.get("unidad")) if x) for p in activos
        )
        return {
            **datos,
            "composicion": composicion or datos.get("composicion"),
            "presentaciones": sorted(
                (
                    {
                        "id_externo": p.get("cn"),
                        "descripcion": p.get("nombre"),
                        "fecha_inicio_comercializacion": _fecha_ms((p.get("estado") or {}).get("aut")),
                    }
                    for p in detalle.get("presentaciones") or []
                ),
                key=lambda p: p["id_externo"] or "",
            ),
        }


def normalizar_ficha(med: dict, secciones: list[dict]) -> dict:
    """Convierte el detalle del medicamento y las secciones de su ficha técnica al formato de FichaTecnica."""
    ft = ficha_tecnica(med) or {}
    fecha = _fecha_ms(ft.get("fecha"))
    importadas = []
    for orden, s in enumerate(sorted(secciones, key=lambda s: [int(x) for x in re.findall(r"\d+", s.get("seccion", ""))])):
        codigo = s.get("seccion") or ""
        if not any(codigo == p or codigo.startswith(p + ".") for p in PREFIJOS_FICHA):
            continue
        texto = html_a_texto(s.get("contenido"))
        if texto:
            importadas.append({"codigo": codigo, "titulo": (s.get("titulo") or "").strip() or None,
                               "orden": orden, "texto": texto})
    vtm = (med.get("vtm") or {}).get("nombre")
    nombre = marca(med["nombre"])
    return {
        "set_id": med["nregistro"],
        "spl_id": f"{med['nregistro']}:{ft.get('fecha')}",
        "version": fecha,
        "fecha_efectiva": fecha,
        "titulo": f"{nombre} ({vtm})" if vtm and vtm.lower() not in nombre.lower() else nombre,
        "laboratorio": med.get("labtitular"),
        "n_registro": med["nregistro"],
        "tipo_producto": med.get("cpresc"),
        "url": ft.get("urlHtml") or ft.get("url") or URL_DETALLE.format(med["nregistro"]),
        "secciones": importadas,
    }
