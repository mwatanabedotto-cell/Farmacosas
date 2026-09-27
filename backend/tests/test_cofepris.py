from datetime import date

import pytest
from sqlalchemy import select

from app.connectors.cofepris import (
    CofeprisError,
    ConectorCofepris,
    enlaces,
    fecha_vigencia,
    filas_de_tablas,
    leer_pdf,
    mejor_principio,
    normalizar_registro,
    puntuacion,
    variantes,
)
from app.models import PrincipioActivo
from app.services import cofepris

CABECERA = ["Registro\nsanitario /\nSSA", "Titular", "Denominación\ndistintiva", "Denominación\ngenérica",
            "Clasificación\nArtículo 226\nLGS", "Forma\nfarmacéutica", "Vigencia"]


def test_enlaces():
    html = """
    <a href="https://www.gob.mx/cms/uploads/attachment/file/145554/regalopa2001.pdf">2001</a>
    <a href="https://www.gob.mx/cms/uploads/attachment/file/1096704/Alop_ticos_2026.pdf">2026</a>
    <a href="https://www.gob.mx/cms/uploads/attachment/file/969390/Alop_ticos_2024.pdf">2024</a>
    <a href="https://www.gob.mx/cms/uploads/attachment/file/969391/Herbolarios_2024.pdf">h</a>
    <a href="https://www.gob.mx/cms/uploads/attachment/file/998235/Registros_Revocados_2025.pdf">r</a>
    <a href="https://www.gob.mx/cms/uploads/attachment/file/998238/Registros_Cancelados_2025.pdf">c</a>"""
    e = enlaces(html)
    assert [a for a, _ in e["expedidos"]] == [2024, 2026]  # 2001: formato antiguo, herbolarios: fuera de alcance
    assert len(e["revocados"]) == 1 and len(e["cancelados"]) == 1


def test_fecha_vigencia():
    assert fecha_vigencia("12-ene-2031") == "2031-01-12"
    assert fecha_vigencia("3-Dic-2027") == "2027-12-03"
    assert fecha_vigencia("") is None and fecha_vigencia("indefinida") is None


def test_filas_de_tablas():
    tablas = [
        [["COMISIÓN...", None, None, None, None, None, None], CABECERA,
         ["001M2026", "Grupo Imperiales,\nS.A. de C.V.", "ROTVAC", "Vacuna rotavirus", "IV", "Suspensión", "12-ene-2031"]],
        [["002M2026", "Genbio, S.A.", "VIMUGEN", "Inmunoglobulina", "IV", "Solución", "12-ene-2031"],
         ["Total", "", "", "", "", "", ""]],
    ]
    filas = filas_de_tablas(tablas)
    assert [f["registro"] for f in filas] == ["001M2026", "002M2026"]
    assert filas[0]["titular"] == "Grupo Imperiales, S.A. de C.V." and filas[0]["vigencia"] == "12-ene-2031"
    with pytest.raises(CofeprisError):
        leer_pdf(b"<html>")


@pytest.mark.parametrize(("generica", "terminos", "positivo"), [
    ("Enoxaparina sódica", "enoxaparina", True),
    ("Clorhidrato de tramadol", "tramadol", True),
    ("Amoxicilina / Ácido clavulánico", "amoxicilina + ácido clavulánico", True),
    ("Trimetoprima / Sulfametoxazol", "sulfametoxazol + trimetoprima", True),
    ("Paracetamol / Fenilefrina / Dextrometorfano", "paracetamol", False),
    ("Esomeprazol", "omeprazol", False),
    ("Sulfato de magnesio", "sulfato de magnesio", True),
])
def test_puntuacion(generica, terminos, positivo):
    assert (puntuacion(generica, variantes(terminos)[0]) > 0) is positivo


def test_mejor_principio_elige_el_mas_especifico():
    indice = {1: variantes("insulina humana regular | insulina humana"), 2: variantes("insulina humana nph | insulina humana isofana")}
    assert mejor_principio("Insulina humana isófana", indice) == 2
    assert mejor_principio("Insulina humana", indice) == 1
    assert mejor_principio("Insulina glargina", indice) is None


def test_normalizar_registro_estados():
    fila = {"registro": "001M2020", "titular": "Lab", "distintiva": "DOLOFIN", "generica": "Paracetamol",
            "clasificacion": "VI", "forma": "Tableta", "vigencia": "12-ene-2025"}
    hoy = date(2026, 9, 27)
    d = normalizar_registro(fila, "https://x/pdf", set(), set(), hoy)
    assert (d["nombre_comercial"], d["es_generico"], d["estado"], d["tipo_producto"]) == \
        ("DOLOFIN", False, "vigencia_por_confirmar", "Venta libre")
    assert d["n_registro"] == "001M2020 SSA" and "vigencia 2025-01-12" in d["descripcion"]
    assert normalizar_registro({**fila, "vigencia": "12-ene-2031"}, "u", set(), set(), hoy)["estado"] == "vigente"
    assert normalizar_registro(fila, "u", {"001M2020"}, set(), hoy)["estado"] == "revocado"
    generico = normalizar_registro({**fila, "distintiva": ""}, "u", set(), set(), hoy)
    assert (generico["nombre_comercial"], generico["es_generico"]) == ("Paracetamol", True)


class CofeprisFalso:
    def __init__(self, listados: dict[str, list[dict]]):
        self.listados = listados
        self.descargas = 0

    def pagina_listados(self):
        return " ".join(f'href="https://www.gob.mx/cms/uploads/attachment/file/1/{n}.pdf"' for n in self.listados)

    def pdf(self, url):
        self.descargas += 1
        return self.listados[url.rsplit("/", 1)[1][:-4]]


def fila(reg, distintiva, generica, forma="Solución", vigencia="12-ene-2031"):
    return {"registro": reg, "titular": "Lab", "distintiva": distintiva, "generica": generica,
            "clasificacion": "IV", "forma": forma, "vigencia": vigencia}


def test_sincronizacion_cofepris(db_sembrada):
    cliente = CofeprisFalso({
        "Alop_ticos_2025": [fila("1M2025", "ROTIZONEX", "Enoxaparina sódica"),
                            fila("2M2025", "UNGUENTO X", "Hidrocortisona", forma="Crema"),
                            fila("3M2025", "PARAX", "Paracetamol")],
        "Alop_ticos_2026": [fila("4M2026", "", "Enoxaparina sódica")],
        "Registros_Revocados_2025": [{"registro": "3M2025", "generica": "Paracetamol"}],
    })
    conector = cofepris.conector(db_sembrada, cliente)
    enox = db_sembrada.scalar(select(PrincipioActivo).where(PrincipioActivo.dci_es == "enoxaparina"))
    hidro = db_sembrada.scalar(select(PrincipioActivo).where(PrincipioActivo.dci_es == "hidrocortisona"))
    para = db_sembrada.scalar(select(PrincipioActivo).where(PrincipioActivo.dci_es == "paracetamol"))
    from app.services import sincronizacion
    for p in (enox, hidro, para):
        sincronizacion.sincronizar_principio(db_sembrada, conector, p)
    assert cliente.descargas == 3  # los PDF se descargan una sola vez
    assert sorted((x.nombre_comercial, x.es_generico, x.pais) for x in enox.productos) == \
        [("Enoxaparina sódica", True, "MX"), ("ROTIZONEX", False, "MX")]
    assert hidro.productos == []  # crema: uso local
    assert [x.estado for x in para.productos] == ["revocado"]


def test_endpoint_admin_cofepris(cliente_api, db_sembrada):
    from app.api.admin import get_cofepris_client
    from app.main import app

    app.dependency_overrides[get_cofepris_client] = lambda: CofeprisFalso(
        {"Alop_ticos_2026": [fila("4M2026", "ROTIZONEX", "Enoxaparina sódica")]})
    enox = db_sembrada.scalar(select(PrincipioActivo).where(PrincipioActivo.dci_es == "enoxaparina"))
    r = cliente_api.post("/api/v1/admin/sincronizaciones/cofepris", headers={"X-Admin-Key": "secreta"},
                         json={"principio_ids": [enox.id]})
    assert r.status_code == 202
    assert cliente_api.get("/api/v1/buscar", params={"q": "rotizonex"}).json()["resultados"][0]["pais"] == "MX"
