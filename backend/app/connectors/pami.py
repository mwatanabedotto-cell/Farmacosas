"""Conector de Argentina: listado oficial de medicamentos que cubre PAMI (INSSJP), en datos.gob.ar.

ANMAT no ofrece una fuente abierta y actualizada del Vademécum Nacional de Medicamentos: el conjunto en
datos.gob.ar es de 2018 y la web del VNM no responde por HTTPS desde este entorno. PAMI publica cada semana
el listado de medicamentos que cubre (droga, marca, presentación, laboratorio y código AlfaBeta), que es una
señal fiable de comercialización actual, aunque no incluye los productos que PAMI no cubre ni el número de
certificado de ANMAT. Los precios se ignoran a propósito (cambian cada semana y no son objetivo del vademécum).
"""

import csv
import hashlib
import io
import re
from collections.abc import Callable
from urllib.parse import urlparse

import httpx

from app.connectors.base import ClienteHTTP, ErrorConector, ResultadoProductos
from app.connectors.nombres import mejor_principio
from app.texto import normalizar

CONJUNTO = "medicamentos-para-entidades"
URL_API = "https://datos.gob.ar/api/3/action/package_show"
URL_CONJUNTO = f"https://datos.gob.ar/dataset/{CONJUNTO}"
HOST_RECURSOS = "datos.pami.org.ar"
MAX_BYTES = 30 * 1024 * 1024
# Presentaciones de acción local (abreviaturas habituales del listado: «sol.oft.», «ung.», «gts.nas.»...).
LOCAL = re.compile(
    r"crema|\bung\b|unguento|pomada|\bgel\b|oft|colirio|\botic|\bnas\b|nasal|\bvag|ovulo|champ|locion|"
    r"\btop\b|topic|\bderm|enj\b|enjuague|bucal|dental"
)


class PamiError(ErrorConector):
    pass


def es_local(presentacion: str) -> bool:
    return bool(LOCAL.search(normalizar(presentacion).replace(".", " ")))


def _columna(cabecera: list[str], clave: str) -> int:
    for i, c in enumerate(cabecera):
        if clave in normalizar(c):
            return i
    raise PamiError(f"El listado de PAMI no tiene la columna «{clave}»")


def leer_listado(contenido: bytes) -> list[dict]:
    """Filas del CSV de PAMI (latin-1, separado por «;»)."""
    try:
        texto = contenido.decode("utf-8")
    except UnicodeDecodeError:
        texto = contenido.decode("latin-1")
    lector = csv.reader(io.StringIO(texto), delimiter=";")
    cabecera = next(lector, None)
    if not cabecera:
        raise PamiError("El listado de PAMI está vacío")
    cols = {
        "alfabeta": _columna(cabecera, "alfabeta"),
        "principio": _columna(cabecera, "principio"),
        "marca": _columna(cabecera, "marca"),
        "presentacion": _columna(cabecera, "presentacion"),
        "laboratorio": _columna(cabecera, "laboratorio"),
        "cobertura": _columna(cabecera, "cobertura"),
    }
    filas = []
    for fila in lector:
        if len(fila) <= max(cols.values()):
            continue
        datos = {k: " ".join(fila[i].split()) for k, i in cols.items()}
        if datos["alfabeta"] and datos["principio"] and datos["marca"]:
            filas.append(datos)
    return filas


def _clave(marca: str, laboratorio: str, principio: str) -> str:
    base = "|".join(normalizar(x) for x in (marca, laboratorio, principio))
    return "pami:" + hashlib.sha1(base.encode()).hexdigest()[:20]


def agrupar(filas: list[dict]) -> dict[str, dict]:
    """Agrupa presentaciones por (marca, laboratorio, principio activo) y descarta las de uso local."""
    productos: dict[str, dict] = {}
    for f in filas:
        if es_local(f["presentacion"]):
            continue
        clave = _clave(f["marca"], f["laboratorio"], f["principio"])
        p = productos.setdefault(clave, {
            "id_externo": clave,
            "nombre_comercial": f["marca"],
            "descripcion": f"{f['marca']} ({f['principio']})",
            "laboratorio": f["laboratorio"],
            "via": None,
            "composicion": f["principio"],
            "es_generico": normalizar(f["marca"]).startswith(normalizar(f["principio"]).split(" ")[0]),
            "estado": "vigente",
            "url_fuente": URL_CONJUNTO,
            "_principio": f["principio"],
            "presentaciones": [],
        })
        p["presentaciones"].append({
            "id_externo": f"alfabeta:{f['alfabeta']}",
            "descripcion": f["presentacion"] + (f" · cobertura PAMI {f['cobertura']}" if f["cobertura"] else ""),
            "fecha_inicio_comercializacion": None,
        })
    for p in productos.values():
        p["presentaciones"].sort(key=lambda x: x["id_externo"])
    return productos


class PamiClient:
    def __init__(
        self,
        http: httpx.Client | None = None,
        pausa_segundos: float = 0.3,
        max_reintentos: int = 4,
        dormir: Callable[[float], None] | None = None,
    ):
        self.http = http or httpx.Client(timeout=120, follow_redirects=True)
        kwargs = {"dormir": dormir} if dormir else {}
        self.cliente = ClienteHTTP(self.http, pausa_segundos, max_reintentos, error=PamiError, **kwargs)

    def listado(self) -> tuple[str, list[dict]]:
        """Devuelve (url del CSV vigente, filas), comprobando que el conjunto lo publica PAMI."""
        meta = self.cliente.get_json(URL_API, {"id": CONJUNTO}) or {}
        conjunto = meta.get("result") or {}
        organizacion = normalizar((conjunto.get("organization") or {}).get("title") or "")
        if "pami" not in organizacion:
            raise PamiError(f"El conjunto {CONJUNTO} no está publicado por PAMI ({organizacion or '?'})")
        recursos = [r for r in conjunto.get("resources") or [] if (r.get("format") or "").upper() == "CSV"]
        if not recursos:
            raise PamiError("El conjunto de PAMI no tiene un recurso CSV")
        url = recursos[0]["url"].replace("http://", "https://", 1)
        if urlparse(url).hostname != HOST_RECURSOS:
            raise PamiError(f"Recurso fuera de {HOST_RECURSOS}: {url}")
        self.cliente.pausa()
        resp = self.cliente.get(url)
        if resp is None:
            raise PamiError(f"No se pudo descargar {url}")
        if len(resp.content) > MAX_BYTES:
            raise PamiError("El listado de PAMI supera el tamaño máximo esperado")
        return url, leer_listado(resp.content)


class ConectorPami:
    """Adaptador del listado de PAMI al servicio genérico de sincronización (descarga una vez por instancia)."""

    codigo_fuente = "pami_medicamentos"
    datos_fuente = {
        "nombre": "PAMI (INSSJP) — listado de medicamentos cubiertos (datos.gob.ar)",
        "agencia": "PAMI (INSSJP)",
        "pais": "AR",
        "url": URL_CONJUNTO,
        "licencia": "Datos abiertos del Gobierno de Argentina (datos.gob.ar)",
    }

    def __init__(self, cliente: PamiClient, indice: dict[int, list[list[str]]], filtros: dict[int, str | None] | None = None):
        self.cliente = cliente
        self.indice = indice
        self.filtros = filtros or {}
        self._datos: tuple[list[dict], str] | None = None

    def _cargar(self) -> tuple[list[dict], str]:
        if self._datos is None:
            url, filas = self.cliente.listado()
            productos = list(agrupar(filas).values())
            for p in productos:
                p["_asignado"] = mejor_principio(p["_principio"], self.indice, p["nombre_comercial"], self.filtros)
            self._datos = (productos, url)
        return self._datos

    def buscar(self, principio) -> ResultadoProductos:
        productos, url = self._cargar()
        return ResultadoProductos(
            productos=[{k: v for k, v in p.items() if not k.startswith("_")} for p in productos if p["_asignado"] == principio.id],
            recibidos={p["id_externo"] for p in productos},
            total=len(productos),
            consulta=url,
        )

    def completar(self, datos: dict) -> dict:
        return datos
