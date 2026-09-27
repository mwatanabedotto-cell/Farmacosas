"""Piezas comunes a los conectores de agencias."""

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from html.parser import HTMLParser

import httpx


class ErrorConector(Exception):
    pass


@dataclass
class ResultadoProductos:
    """Productos de un principio activo devueltos por un conector, ya filtrados y normalizados."""

    productos: list[dict]
    # Identificadores de todo lo recibido (coincida o no): permite distinguir productos que dejaron
    # de cumplir los criterios (se desvinculan) de los que ya no existen en la fuente (no_listado).
    recibidos: set[str] = field(default_factory=set)
    total: int = 0
    truncado: bool = False
    consulta: str | None = None


class ClienteHTTP:
    """GET con reintentos ante errores de red, 429 y 5xx, y pausa entre peticiones."""

    def __init__(
        self,
        http: httpx.Client,
        pausa_segundos: float = 0.2,
        max_reintentos: int = 4,
        dormir: Callable[[float], None] = time.sleep,
        error: type[ErrorConector] = ErrorConector,
    ):
        self.http = http
        self.pausa_segundos = pausa_segundos
        self.max_reintentos = max_reintentos
        self.dormir = dormir
        self.error = error

    def get_json(self, ruta: str, params: dict) -> dict | list | None:
        """Devuelve el JSON, o None si la fuente responde 404 (sin resultados)."""
        resp = self.get(ruta, params)
        return resp.json() if resp is not None and resp.content.strip() else None

    def get(self, ruta: str, params: dict | None = None) -> httpx.Response | None:
        """Respuesta 200, o None si la fuente responde 404."""
        for intento in range(self.max_reintentos + 1):
            try:
                resp = self.http.get(ruta, params=params)
            except httpx.TransportError as e:
                motivo = f"Error de red: {e}"
            else:
                if resp.status_code == 200:
                    return resp
                if resp.status_code == 404:
                    return None
                if resp.status_code != 429 and resp.status_code < 500:
                    raise self.error(f"{self.http.base_url} respondió {resp.status_code}: {resp.text[:300]}")
                motivo = f"{self.http.base_url} respondió {resp.status_code}"
            if intento < self.max_reintentos:
                self.dormir(2 ** (intento + 1))
        raise self.error(f"{motivo} (tras {self.max_reintentos} reintentos)")

    def pausa(self) -> None:
        if self.pausa_segundos:
            self.dormir(self.pausa_segundos)


class _TextoPlano(HTMLParser):
    BLOQUES = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "table", "ul", "ol"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.partes: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in self.BLOQUES:
            self.partes.append("\n")
        if tag == "li":
            self.partes.append("• ")
        elif tag in ("td", "th"):
            self.partes.append(" | ")

    def handle_endtag(self, tag):
        if tag in self.BLOQUES:
            self.partes.append("\n")

    def handle_data(self, data):
        self.partes.append(data)


def html_a_texto(html: str | None) -> str:
    """Convierte HTML a texto plano con un párrafo por línea (no se guarda HTML: evita XSS)."""
    if not html:
        return ""
    parser = _TextoPlano()
    parser.feed(html)
    lineas = (" ".join(linea.replace("\xa0", " ").split()) for linea in "".join(parser.partes).split("\n"))
    return "\n".join(linea for linea in lineas if linea and linea != "|").strip()


# Palabras que marcan el fin de la marca en el nombre de un medicamento.
FORMAS = {
    "COMPRIMIDO", "COMPRIMIDOS", "CAPSULA", "CAPSULAS", "CÁPSULA", "CÁPSULAS", "SOLUCION", "SOLUCIÓN",
    "SUSPENSION", "SUSPENSIÓN", "POLVO", "JARABE", "CREMA", "POMADA", "GEL", "PARCHE", "PARCHES",
    "INYECTABLE", "GRANULADO", "GRANULOS", "GRÁNULOS", "SOBRES", "SUPOSITORIOS", "COLIRIO", "AEROSOL",
    "EMULSION", "EMULSIÓN", "CONCENTRADO", "LIOFILIZADO", "GOTAS", "PASTILLAS", "INHALADOR",
    "TABLETA", "TABLETAS", "GRAGEA", "GRAGEAS", "AMPOLLA", "AMPOLLAS", "JERINGA", "JERINGAS", "VIAL",
    "VIALES", "ELIXIR", "OVULOS", "ÓVULOS",
}
# Palabras que quedan colgando al cortar antes de la concentración ("SULFATO DE MAGNESIO AL 20 %").
CONECTORES_FINALES = {"AL", "EN", "DE", "X", "POR", "CON", "Y", "+", "/"}
MARCAS_REGISTRADAS = str.maketrans({"®": " ", "™": " ", "©": " "})


def marca(nombre: str) -> str:
    """'CLEXANE 4.000 UI (40 mg)/0,4 ml SOLUCION...' -> 'CLEXANE'; 'WOSULIN ® R 100UI/ML' -> 'WOSULIN R'."""
    palabras = []
    for palabra in nombre.translate(MARCAS_REGISTRADAS).split():
        limpia = palabra.strip(",;()").upper()
        if palabras and (any(c.isdigit() for c in palabra) or limpia in FORMAS):
            break
        palabras.append(palabra)
    while len(palabras) > 1 and palabras[-1].upper() in CONECTORES_FINALES:
        palabras.pop()
    return " ".join(palabras).strip(" ,") or nombre.strip()
