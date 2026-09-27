"""Conector de COFEPRIS (México): listados oficiales de registros sanitarios de medicamentos.

El buscador y el visor de registros de COFEPRIS y datos.gob.mx rechazan el acceso automatizado (403),
así que se usan los listados en PDF que COFEPRIS publica en gob.mx:
- «Registros sanitarios de medicamentos alopáticos expedidos <año>» (registro, titular, denominación
  distintiva y genérica, clasificación art. 226 LGS, forma farmacéutica y vigencia);
- registros revocados y cancelados.

Limitación: son registros *expedidos* cada año. No incluyen las renovaciones (prórrogas), así que no
cubren todo el mercado y, pasada la fecha de vigencia, no se puede afirmar si un registro sigue vigente.
Los listados no traen código ATC: la asignación a principios activos es por denominación genérica.
"""

import io
import logging
import re
from collections.abc import Callable
from datetime import date
from urllib.parse import urlparse

import httpx
import pdfplumber

from app.connectors.base import ClienteHTTP, ErrorConector, ResultadoProductos
from app.connectors.nombres import componentes, mejor_principio, puntuacion, variantes  # noqa: F401
from app.texto import normalizar

log = logging.getLogger(__name__)

URL_LISTADOS = "https://www.gob.mx/cofepris/documentos/registros-sanitarios-medicamentos"
ENLACE = re.compile(r"https://www\.gob\.mx/cms/uploads/attachment/file/\d+/([A-Za-z_]+?)[_-]?((?:19|20)\d{2})[^\"'\s<>]*?\.pdf", re.I)
PRIMER_ANIO = 2015  # desde este año el formato de los listados es tabular y homogéneo
MAX_BYTES = 30 * 1024 * 1024
REGISTRO = re.compile(r"^\d{1,5}M\d{2,4}$|^\d{4,6}$")
MESES = {"ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6, "jul": 7, "ago": 8, "sep": 9, "oct": 10, "nov": 11, "dic": 12}

# Artículo 226 de la Ley General de Salud: condiciones de venta.
FRACCIONES_226 = {
    "I": "Receta especial (estupefacientes)",
    "II": "Receta médica retenida (psicotrópicos)",
    "III": "Receta médica, surtible hasta tres veces (psicotrópicos)",
    "IV": "Receta médica",
    "V": "Sin receta, venta solo en farmacias",
    "VI": "Venta libre",
}
# Formas de acción local: no corresponden a los principios activos de uso sistémico del vademécum.
FORMAS_LOCALES = re.compile(
    r"crema|ung[uü]ento|pomada|\bgel\b|loci[oó]n|oft[aá]lmic|colirio|[oó]tic|nasal|vaginal|[oó]vulo|champ[uú]|"
    r"espuma|t[oó]pic|d[eé]rmic|cut[aá]ne|colutorio|enjuague|bucal|dent[ai]",
    re.I,
)
class CofeprisError(ErrorConector):
    pass


def enlaces(html: str) -> dict[str, list[tuple[int, str]]]:
    """{'expedidos'|'revocados'|'cancelados': [(año, url)]} a partir de la página de listados."""
    salida: dict[str, list[tuple[int, str]]] = {"expedidos": [], "revocados": [], "cancelados": []}
    for m in ENLACE.finditer(html):
        nombre, anio, url = m.group(1).lower(), int(m.group(2)), m.group(0)
        if urlparse(url).hostname != "www.gob.mx":
            continue
        if nombre.startswith("alop") or nombre.startswith("regalopa"):
            tipo = "expedidos"
        elif "revoc" in nombre:
            tipo = "revocados"
        elif "cancel" in nombre:
            tipo = "cancelados"
        else:
            continue
        if tipo == "expedidos" and anio < PRIMER_ANIO:
            continue
        if (anio, url) not in salida[tipo]:
            salida[tipo].append((anio, url))
    for lista in salida.values():
        lista.sort()
    return salida


def _limpio(celda) -> str:
    return " ".join(str(celda or "").split())


def _columna(cabecera: list[str], *claves: str) -> int | None:
    for i, c in enumerate(cabecera):
        n = normalizar(c)
        if all(k in n for k in claves):
            return i
    return None


def fecha_vigencia(texto: str) -> str | None:
    """'12-ene-2031' -> '2031-01-12'."""
    m = re.match(r"(\d{1,2})[-/ ]([a-záéíóú]{3})[a-z]*[-/ ](\d{4})", normalizar(texto))
    if not m or m.group(2)[:3] not in MESES:
        return None
    return date(int(m.group(3)), MESES[m.group(2)[:3]], int(m.group(1))).isoformat()


def leer_pdf(contenido: bytes) -> list[dict]:
    """Filas de un listado de COFEPRIS en PDF."""
    if not contenido.startswith(b"%PDF"):
        raise CofeprisError("El archivo no es un PDF")
    with pdfplumber.open(io.BytesIO(contenido)) as pdf:
        return filas_de_tablas(tabla for pagina in pdf.pages for tabla in pagina.extract_tables())


def filas_de_tablas(tablas) -> list[dict]:
    """Convierte las tablas extraídas en filas, identificando las columnas por su encabezado."""
    filas: list[dict] = []
    cols: dict[str, int | None] | None = None
    for tabla in tablas:
        for fila in tabla:
            celdas = [_limpio(c) for c in fila]
            if celdas and normalizar(celdas[0]).startswith("registro"):
                cols = {
                    "registro": 0,
                    "titular": _columna(celdas, "titular"),
                    "distintiva": _columna(celdas, "distintiva"),
                    "generica": _columna(celdas, "generica"),
                    "clasificacion": _columna(celdas, "226"),
                    "forma": _columna(celdas, "forma"),
                    "vigencia": _columna(celdas, "vigencia"),
                    "motivo": _columna(celdas, "motivo"),
                }
                continue
            if cols is None or not celdas or not REGISTRO.match(celdas[0].replace(" ", "")):
                continue
            filas.append({k: (celdas[i] if i is not None and i < len(celdas) else "") for k, i in cols.items()})
    return filas


def normalizar_registro(fila: dict, url: str, revocados: set[str], cancelados: set[str], hoy: date | None = None) -> dict:
    registro = fila["registro"].replace(" ", "").upper()
    distintiva, generica = fila["distintiva"], fila["generica"]
    es_generico = not distintiva or distintiva in ("-", "–", "N/A", "NA") or normalizar(distintiva) == normalizar(generica)
    vigencia = fecha_vigencia(fila.get("vigencia") or "")
    if registro in revocados:
        estado = "revocado"
    elif registro in cancelados:
        estado = "cancelado"
    elif vigencia and vigencia >= (hoy or date.today()).isoformat():
        estado = "vigente"
    else:
        estado = "vigencia_por_confirmar"
    fraccion = (fila.get("clasificacion") or "").strip().upper()
    nombre = generica if es_generico else distintiva
    return {
        "id_externo": registro,
        "nombre_comercial": nombre,
        "descripcion": " · ".join(x for x in [
            f"{distintiva} ({generica})" if not es_generico else generica,
            fila.get("forma"),
            f"vigencia {vigencia}" if vigencia else None,
        ] if x),
        "laboratorio": fila.get("titular") or None,
        "forma_farmaceutica": fila.get("forma") or None,
        "via": None,
        "composicion": generica,
        "tipo_producto": FRACCIONES_226.get(fraccion),
        "categoria_registro": f"Art. 226 LGS, fracción {fraccion}" if fraccion in FRACCIONES_226 else None,
        "n_registro": f"{registro} SSA",
        "es_generico": es_generico,
        "estado": estado,
        "url_fuente": url,
        "presentaciones": [],
    }


class CofeprisClient:
    def __init__(
        self,
        http: httpx.Client | None = None,
        pausa_segundos: float = 0.5,
        max_reintentos: int = 4,
        dormir: Callable[[float], None] | None = None,
    ):
        self.http = http or httpx.Client(timeout=120, follow_redirects=True)
        kwargs = {"dormir": dormir} if dormir else {}
        self.cliente = ClienteHTTP(self.http, pausa_segundos, max_reintentos, error=CofeprisError, **kwargs)

    def pagina_listados(self) -> str:
        resp = self.cliente.get(URL_LISTADOS)
        if resp is None:
            raise CofeprisError("La página de listados de COFEPRIS no está disponible")
        return resp.text

    def pdf(self, url: str) -> list[dict]:
        self.cliente.pausa()
        resp = self.cliente.get(url)
        if resp is None:
            raise CofeprisError(f"No se pudo descargar {url}")
        if len(resp.content) > MAX_BYTES:
            raise CofeprisError(f"{url} supera el tamaño máximo esperado")
        return leer_pdf(resp.content)


class ConectorCofepris:
    """Adaptador de los listados de COFEPRIS al servicio genérico de sincronización.

    `indice` = {principio_id: variantes de nombre}; hace falta conocer todos los principios activos para
    asignar cada registro al más específico. Los PDF se descargan una sola vez por instancia.
    """

    codigo_fuente = "cofepris_listados"
    datos_fuente = {
        "nombre": "COFEPRIS — listados de registros sanitarios de medicamentos (gob.mx)",
        "agencia": "COFEPRIS",
        "pais": "MX",
        "url": URL_LISTADOS,
        "licencia": "Documentos públicos oficiales del Gobierno de México",
    }

    def __init__(self, cliente: CofeprisClient, indice: dict[int, list[list[str]]], filtros: dict[int, str | None] | None = None):
        self.cliente = cliente
        self.indice = indice
        self.filtros = filtros or {}
        self._datos: tuple[list[dict], str] | None = None

    def _cargar(self) -> tuple[list[dict], str]:
        if self._datos is None:
            fuentes = enlaces(self.cliente.pagina_listados())
            if not fuentes["expedidos"]:
                raise CofeprisError("No se encontraron listados de registros expedidos en gob.mx")
            revocados = {f["registro"].replace(" ", "").upper() for _, u in fuentes["revocados"] for f in self.cliente.pdf(u)}
            cancelados = {f["registro"].replace(" ", "").upper() for _, u in fuentes["cancelados"] for f in self.cliente.pdf(u)}
            registros: dict[str, dict] = {}
            for anio, url in fuentes["expedidos"]:
                for fila in self.cliente.pdf(url):
                    datos = normalizar_registro(fila, url, revocados, cancelados)
                    datos["_principio"] = mejor_principio(fila["generica"], self.indice, datos["nombre_comercial"], self.filtros)
                    datos["_local"] = bool(FORMAS_LOCALES.search(fila.get("forma") or ""))
                    registros.setdefault(datos["id_externo"], datos)
            anios = [a for a, _ in fuentes["expedidos"]]
            self._datos = (list(registros.values()), f"registros expedidos {min(anios)}–{max(anios)}")
        return self._datos

    def buscar(self, principio) -> ResultadoProductos:
        registros, consulta = self._cargar()
        productos = [
            {k: v for k, v in r.items() if not k.startswith("_")}
            for r in registros if r["_principio"] == principio.id and not r["_local"]
        ]
        return ResultadoProductos(
            productos=productos, recibidos={r["id_externo"] for r in registros}, total=len(registros), consulta=consulta
        )

    def completar(self, datos: dict) -> dict:
        return datos
