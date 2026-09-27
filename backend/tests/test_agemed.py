import io

import httpx
import pytest
from openpyxl import Workbook
from sqlalchemy import select

from app.connectors.agemed import AgemedClient, AgemedError, elegir_liname, leer_liname
from app.models import ItemListaEsencial, PrincipioActivo
from app.services import listas_esenciales as le

CABECERA = ["Código", None, None, "Medicamento", "Forma Farmacéutica", "Concentración", "Classific. A.T.Q.", "Uso Restrin-gido"]
FILAS = [
    ["J", 1, 5, "Amoxicilina", "Comprimido", "1 g", "J01CA04"],
    ["J", 1, 12, "Cefazolina", "Inyectable", "1 g", "J01DE04"],           # ATC erróneo en la lista
    ["D", 6, 3, "Aciclovir", "crema dérmica ", 0.05, "D06BB03"],          # uso tópico: no es el sistémico
    ["J", 5, 4, "Aciclovir", "Comprimido", "400 mg", "J05AB01"],
    ["B", 1, 9, "Heparina de bajo peso molecular", "Inyectable", "Según disponibilidad", "B01AB**"],
    ["B", 2, 6, "Ácido Tranexámico", "Inyectable", "500 mg ", "B02AA02", "R"],
    ["N", 1, 3, "Fentanilo con conservante", "Inyectable", "0,05 mg/ml", "N01AH01"],
]


def excel(filas=FILAS, titulo="LINAME 2026-2027") -> bytes:
    libro = Workbook()
    hoja = libro.active
    hoja.title = titulo
    hoja.append([])
    hoja.append(["LISTA NACIONAL DE MEDICAMENTOS ESENCIALES"])
    hoja.append(CABECERA)
    for f in filas:
        hoja.append(f)
    aware = libro.create_sheet("CLASIFICAION AWaRe OMS")
    aware.append(["Clasific. ATQ", "Codigo LINAME", None, None, "Medicamento", "Forma", "Conc", "Clase", "Categoria"])
    aware.append(["J01CA04", "J", 1, 5, "Amoxicilina", "Comprimido", "1 g", "Penicilinas", "Acceso"])
    buffer = io.BytesIO()
    libro.save(buffer)
    return buffer.getvalue()


def test_elegir_liname_mas_reciente():
    html = ('href=\\"archivo_uso_racional\\/liname\\/LINAME2022-2024.xlsx\\" '
            'href="archivo_uso_racional/liname/LINAME_2026_2027_07-04-26.xlsx" '
            'href="archivo_uso_racional/liname/LINAME_2026_2027_15-01-26.xlsx"')
    assert elegir_liname(html) == "https://www.agemed.gob.bo/archivo_uso_racional/liname/LINAME_2026_2027_07-04-26.xlsx"
    with pytest.raises(AgemedError):
        elegir_liname("<html>sin enlaces</html>")


def test_leer_liname():
    nombre, entradas = leer_liname(excel())
    assert nombre == "LINAME 2026-2027" and len(entradas) == 7
    amox = entradas[0]
    assert (amox["codigo"], amox["aware"], amox["uso_restringido"]) == ("J.01.05", "Acceso", False)
    crema = entradas[2]
    assert (crema["forma_farmaceutica"], crema["concentracion"]) == ("Crema dérmica", "5 %")
    assert entradas[5]["uso_restringido"] is True and entradas[5]["concentracion"] == "500 mg"
    with pytest.raises(AgemedError):
        leer_liname(b"<html>no es excel</html>")


def test_asignacion_a_principios(db_sembrada):
    indices = le.asignar_principios(db_sembrada)
    id_de = lambda dci: db_sembrada.scalar(select(PrincipioActivo.id).where(PrincipioActivo.dci_es == dci))  # noqa: E731
    _, entradas = leer_liname(excel())
    asignados = [le.principio_de(e, indices) for e in entradas]
    assert asignados == [id_de("amoxicilina"), id_de("cefazolina"), None, id_de("aciclovir"), None,
                         id_de("ácido tranexámico"), id_de("fentanilo")]


class AgemedFalso:
    def __init__(self, filas):
        self.filas = filas

    def liname(self):
        return "https://www.agemed.gob.bo/x.xlsx", "LINAME 2026-2027", leer_liname(excel(self.filas))[1]


def test_sincronizar_y_resumen(db_sembrada):
    cliente = AgemedFalso(FILAS)
    sync = le.sincronizar_liname(db_sembrada, cliente)
    assert (sync.estado, sync.n_creados) == ("ok", 7)
    assert "7 entradas, 5 asignadas" in sync.mensaje

    fentanilo = db_sembrada.scalar(select(PrincipioActivo).where(PrincipioActivo.dci_es == "fentanilo"))
    tranex = db_sembrada.scalar(select(PrincipioActivo).where(PrincipioActivo.dci_es == "ácido tranexámico"))
    r = le.resumen(db_sembrada, tranex.id)[0]
    assert (r["pais"], r["lista"], r["uso_restringido"]) == ("BO", "LINAME 2026-2027", True)
    assert r["presentaciones"] == ["Inyectable 500 mg (uso restringido)"]
    assert le.resumen(db_sembrada, fentanilo.id)[0]["presentaciones"] == ["Inyectable 0,05 mg/ml (Fentanilo con conservante)"]

    # Nueva versión de la lista sin el ácido tranexámico: pasa a «excluido».
    cliente.filas = [f for f in FILAS if f[3] != "Ácido Tranexámico"]
    sync = le.sincronizar_liname(db_sembrada, cliente)
    assert (sync.n_sin_cambios, sync.n_no_listados) == (6, 1)
    assert le.resumen(db_sembrada, tranex.id) == []
    item = db_sembrada.scalar(select(ItemListaEsencial).where(ItemListaEsencial.medicamento == "Ácido Tranexámico"))
    assert item.estado == "excluido"


def test_cliente_real_simulado():
    contenido = excel()

    def manejador(req):
        if req.url.host == "apiwww.agemed.gob.bo":
            return httpx.Response(200, text='href="archivo_uso_racional/liname/LINAME_2026_2027_07-04-26.xlsx"')
        assert req.url.path == "/archivo_uso_racional/liname/LINAME_2026_2027_07-04-26.xlsx"
        return httpx.Response(200, content=contenido)

    cliente = AgemedClient(http=httpx.Client(transport=httpx.MockTransport(manejador)), pausa_segundos=0, dormir=lambda s: None)
    url, nombre, entradas = cliente.liname()
    assert url.startswith("https://www.agemed.gob.bo/") and nombre == "LINAME 2026-2027" and len(entradas) == 7


def test_api_listas_esenciales(cliente_api, db_sembrada):
    from app.api.admin import get_agemed_client
    from app.main import app

    app.dependency_overrides[get_agemed_client] = lambda: AgemedFalso(FILAS)
    r = cliente_api.post("/api/v1/admin/sincronizaciones/liname", headers={"X-Admin-Key": "secreta"})
    assert r.status_code == 202
    pid = db_sembrada.scalar(select(PrincipioActivo.id).where(PrincipioActivo.dci_es == "amoxicilina"))
    detalle = cliente_api.get(f"/api/v1/principios/{pid}").json()
    assert detalle["listas_esenciales"][0]["aware"] == "Acceso"
    items = cliente_api.get(f"/api/v1/principios/{pid}/listas-esenciales", params={"pais": "BO"}).json()
    assert [(i["codigo"], i["concentracion"], i["estado"]) for i in items] == [("J.01.05", "1 g", "incluido")]
