from sqlalchemy import select

from app.connectors.openfda import normalizar_ficha, secciones_sin_duplicar
from app.models import FichaTecnica, PrincipioActivo
from app.services.fichas import candidatos_ordenados, importar_ficha
from app.services.sync_openfda import sincronizar_principio
from tests.conftest import ficha_label, producto_ndc
from tests.test_sync_openfda import ClienteFalso


class ClienteFichas(ClienteFalso):
    def __init__(self, fichas: dict[str, dict]):
        super().__init__()
        self.fichas = fichas
        self.consultas: list[list[str]] = []

    def buscar_fichas(self, set_ids):
        self.consultas.append(list(set_ids))
        return {s: self.fichas[s] for s in set_ids if s in self.fichas}


def principio(db, dci="enoxaparina") -> PrincipioActivo:
    return db.scalar(select(PrincipioActivo).where(PrincipioActivo.dci_es == dci))


def producto(ndc, set_id, fecha="20200101", **extra):
    return producto_ndc(ndc, [("ENOXAPARIN SODIUM", "40 mg/.4mL")], openfda={"spl_set_id": [set_id]},
                        packaging=[{"package_ndc": f"{ndc}-01", "marketing_start_date": fecha}], **extra)


def test_normalizar_ficha_y_titulo():
    datos = normalizar_ficha(ficha_label("s1", indications_and_usage=" 1 INDICATIONS ", adverse_reactions="6 AR"))
    assert datos["titulo"] == "Lovenox (enoxaparin sodium)"
    assert datos["fecha_efectiva"] == "2026-05-28"
    assert datos["url"].endswith("setid=s1")
    assert [s["codigo"] for s in datos["secciones"]] == ["indications_and_usage", "adverse_reactions"]
    assert datos["secciones"][0]["texto"] == "1 INDICATIONS"
    sin_marca = ficha_label("s2")
    sin_marca["openfda"]["brand_name"] = ["N/A"]
    assert normalizar_ficha(sin_marca)["titulo"] == "(enoxaparin sodium)"


def test_secciones_sin_duplicar():
    textos = {"use_in_specific_populations": "8 USE ... 8.1 Pregnancy X ... 8.4 Pediatric Y",
              "pregnancy": "8.1 Pregnancy X", "pediatric_use": "otro texto", "contraindications": "4 C"}
    assert secciones_sin_duplicar(textos) == ["use_in_specific_populations", "pediatric_use", "contraindications"]


def test_candidatos_prefieren_via_sistemica_marca_y_original(db_sembrada):
    p = principio(db_sembrada)
    productos = [
        producto("1", "generico", "20100101", marketing_category="ANDA"),
        producto("2", "marca-nueva", "20230101", marketing_category="NDA"),
        producto("3", "marca-original", "19930101", marketing_category="NDA"),
        producto("4", "colirio", "19800101", marketing_category="NDA", route=["OPHTHALMIC"]),
        producto("5", "otc", "19700101", marketing_category="NDA", product_type="HUMAN OTC DRUG"),
    ]
    sincronizar_principio(db_sembrada, ClienteFalso(productos), p)
    assert candidatos_ordenados(db_sembrada, p) == ["marca-original", "marca-nueva", "otc", "generico", "colirio"]

    p.vias_openfda = "OPHTHALMIC"
    assert candidatos_ordenados(db_sembrada, p)[0] == "colirio"


def test_importar_ficha_fijada_con_respaldo(db_sembrada):
    p = principio(db_sembrada)
    sincronizar_principio(db_sembrada, ClienteFalso([producto("1", "auto", marketing_category="NDA")]), p)
    p.ficha_set_id_openfda = "retirada"
    cliente = ClienteFichas({"auto": ficha_label("auto", contraindications="4 C")})
    ficha, resultado = importar_ficha(db_sembrada, cliente, p)
    assert resultado == "creada" and ficha.set_id == "auto"
    assert cliente.consultas == [["retirada", "auto"]]

    ficha, resultado = importar_ficha(db_sembrada, cliente, p)
    assert resultado == "sin_cambios"

    cliente.fichas["auto"] = ficha_label("auto", spl_id="spl-2", version="2", contraindications="4 C nueva")
    ficha, resultado = importar_ficha(db_sembrada, cliente, p)
    assert resultado == "actualizada" and ficha.version == "2"
    assert [s.texto for s in ficha.secciones] == ["4 C nueva"]
    assert db_sembrada.query(FichaTecnica).count() == 1


def test_sin_productos_no_hay_ficha(db_sembrada):
    assert importar_ficha(db_sembrada, ClienteFichas({}), principio(db_sembrada, "metamizol")) == (None, "sin_ficha")
