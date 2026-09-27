from datetime import date

import httpx
import pytest

from app.connectors.openfda import (
    OpenFDAClient,
    OpenFDAError,
    construir_busqueda,
    es_excluido,
    estado_listado,
    fusionar_duplicados,
    nombre_cumple_filtro,
    normalizar_producto,
    parsear_terminos,
    producto_coincide,
)
from tests.conftest import producto_ndc


def cliente_con(manejador, **kwargs) -> OpenFDAClient:
    http = httpx.Client(base_url="https://api.fda.gov", transport=httpx.MockTransport(manejador))
    return OpenFDAClient(http=http, pausa_segundos=0, dormir=lambda s: None, **kwargs)


def test_parsear_y_construir_busqueda():
    componentes = parsear_terminos("amoxicillin+clavulanate|clavulanic acid")
    assert componentes == [["amoxicillin"], ["clavulanate", "clavulanic acid"]]
    assert construir_busqueda(componentes) == (
        'active_ingredients.name:"amoxicillin" AND '
        '(active_ingredients.name:"clavulanate" active_ingredients.name:"clavulanic acid")'
    )


@pytest.mark.parametrize(
    ("ingredientes", "terminos", "esperado"),
    [
        (["METOPROLOL TARTRATE"], "metoprolol", True),
        (["NITROFURANTOIN MONOHYDRATE", "NITROFURANTOIN, MACROCRYSTALLINE"], "nitrofurantoin", True),
        (["INSULIN GLARGINE-YFGN"], "insulin glargine", True),
        (["AMOXICILLIN", "CLAVULANATE POTASSIUM"], "amoxicillin+clavulanate|clavulanic acid", True),
        # Combinaciones con otros fármacos no pertenecen al principio activo simple.
        (["AMOXICILLIN", "CLAVULANATE POTASSIUM"], "amoxicillin", False),
        (["LOSARTAN POTASSIUM", "HYDROCHLOROTHIAZIDE"], "losartan", False),
        # Falta un componente de la combinación.
        (["AMOXICILLIN"], "amoxicillin+clavulanate", False),
        # Prefijos que no son el mismo fármaco.
        (["PREDNISOLONE"], "prednisone", False),
        (["ESOMEPRAZOLE MAGNESIUM"], "omeprazole", False),
        (["ENALAPRILAT"], "enalapril", False),
        ([], "enalapril", False),
    ],
)
def test_producto_coincide(ingredientes, terminos, esperado):
    assert producto_coincide(ingredientes, parsear_terminos(terminos)) is esperado


def test_excluye_homeopaticos_y_graneles():
    assert es_excluido({"marketing_category": "UNAPPROVED HOMEOPATHIC"})
    assert es_excluido({"finished": False, "marketing_category": "BULK INGREDIENT"})
    assert not es_excluido({"finished": True, "marketing_category": "ANDA"})


def test_normalizar_producto():
    bruto = producto_ndc("0000-0001", [("ENOXAPARIN SODIUM", "40 mg/.4mL")], brand_name="Lovenox",
                         marketing_category="NDA", application_number="NDA020164")
    datos = normalizar_producto(bruto)
    assert datos["nombre_comercial"] == "Lovenox"
    assert datos["es_generico"] is False
    assert datos["composicion"] == "ENOXAPARIN SODIUM 40 mg/.4mL"
    assert datos["via"] == "SUBCUTANEOUS"
    assert datos["url_ficha"].endswith("setid=set-0000-0001")
    assert datos["presentaciones"][0]["fecha_inicio_comercializacion"] == "2020-01-15"


def test_estado_listado():
    assert estado_listado({"listing_expiration_date": "20251231"}, hoy=date(2026, 1, 1)) == "listado_vencido"
    assert estado_listado({"listing_expiration_date": "20261231"}, hoy=date(2026, 1, 1)) == "vigente"
    assert estado_listado({}, hoy=date(2026, 1, 1)) == "vigente"


def test_pagina_resultados():
    total = 2500
    peticiones = []

    def manejador(req: httpx.Request) -> httpx.Response:
        skip, limit = int(req.url.params["skip"]), int(req.url.params["limit"])
        peticiones.append((skip, limit))
        n = max(0, min(limit, total - skip))
        return httpx.Response(200, json={"meta": {"results": {"total": total}},
                                         "results": [{"product_ndc": str(skip + i)} for i in range(n)]})

    resultado = cliente_con(manejador).buscar_ndc('active_ingredients.name:"x"')
    assert len(resultado.resultados) == total and not resultado.truncado
    assert peticiones == [(0, 1000), (1000, 1000), (2000, 1000)]


def test_limite_maximo_marca_truncado():
    def manejador(req):
        return httpx.Response(200, json={"meta": {"results": {"total": 5000}},
                                         "results": [{}] * int(req.url.params["limit"])})

    resultado = cliente_con(manejador, max_resultados=1500).buscar_ndc("x")
    assert len(resultado.resultados) == 1500 and resultado.truncado


def test_sin_resultados_devuelve_vacio():
    resultado = cliente_con(lambda req: httpx.Response(404, json={"error": {"code": "NOT_FOUND"}})).buscar_ndc("x")
    assert resultado.resultados == [] and not resultado.truncado


def test_reintenta_ante_429_y_usa_api_key():
    respuestas = iter([httpx.Response(429), httpx.Response(200, json={"meta": {"results": {"total": 0}}, "results": []})])
    claves = []

    def manejador(req):
        claves.append(req.url.params.get("api_key"))
        return next(respuestas)

    assert cliente_con(manejador, api_key="k").buscar_ndc("x").resultados == []
    assert claves == ["k", "k"]


def test_error_tras_agotar_reintentos():
    with pytest.raises(OpenFDAError):
        cliente_con(lambda req: httpx.Response(503), max_reintentos=2).buscar_ndc("x")


def test_error_de_cliente_no_se_reintenta():
    llamadas = []

    def manejador(req):
        llamadas.append(1)
        return httpx.Response(400, text="bad query")

    with pytest.raises(OpenFDAError):
        cliente_con(manejador).buscar_ndc("x")
    assert len(llamadas) == 1


def test_fusiona_ndc_duplicados_en_varias_fichas():
    a = producto_ndc("63323-531", [("ENOXAPARIN SODIUM", "40 mg/.4mL")], spl_id="b",
                     packaging=[{"package_ndc": "63323-531-98"}])
    b = producto_ndc("63323-531", [("ENOXAPARIN SODIUM", "40 mg/.4mL")], spl_id="a",
                     packaging=[{"package_ndc": "63323-531-90"}, {"package_ndc": "63323-531-98"}])
    otro = producto_ndc("0000-0001", [("ENOXAPARIN SODIUM", "40 mg/.4mL")])
    fusionados = fusionar_duplicados([a, otro, b])
    assert [p["product_ndc"] for p in fusionados] == ["63323-531", "0000-0001"]
    assert fusionados[0]["spl_id"] == "a"
    # Mismo resultado sin importar el orden de llegada.
    assert fusionados == fusionar_duplicados([b, otro, a])
    presentaciones = normalizar_producto(fusionados[0])["presentaciones"]
    assert [p["id_externo"] for p in presentaciones] == ["63323-531-90", "63323-531-98"]


@pytest.mark.parametrize(
    ("nombre", "filtro", "esperado"),
    [
        ("Humulin R", r"!\bN\b|\d+/\d+", True),
        ("Humulin N", r"!\bN\b|\d+/\d+", False),
        ("Novolin 70/30", r"!\bN\b|\d+/\d+", False),
        ("Humulin N KwikPen", r"\bN\b", True),
        ("Novolin R", r"\bN\b", False),
        ("Cualquiera", None, True),
    ],
)
def test_filtro_por_nombre(nombre, filtro, esperado):
    assert nombre_cumple_filtro(nombre, filtro) is esperado


def test_buscar_fichas_por_set_id():
    busquedas = []

    def manejador(req):
        busquedas.append((req.url.params["search"], req.url.params["limit"]))
        return httpx.Response(200, json={"results": [{"set_id": "b", "id": "x"}]})

    assert cliente_con(manejador).buscar_fichas(["a", "b"]) == {"b": {"set_id": "b", "id": "x"}}
    assert busquedas == [('set_id:"a" set_id:"b"', "2")]
    assert cliente_con(lambda req: httpx.Response(404)).buscar_fichas(["a"]) == {}
