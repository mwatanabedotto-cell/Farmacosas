import httpx
import pytest
from sqlalchemy import select

from app.connectors.nombres import componentes, desinvertir, mejor_principio, variantes
from app.connectors.pami import PamiClient, PamiError, agrupar, es_local, leer_listado
from app.models import PrincipioActivo
from app.services import pami, sincronizacion

CSV = (
    "ALFABETA;PRINCIPIO ACTIVO;MARCA COMERCIAL;PRESENTACION;LABORATORIO;PVP PAMI AL 25/09/2026;COBERTURA;IMPORTE AFILIADO\r\n"
    "1;amoxicilina+clavulánico,ác.;AMOCLAV DUO;875 mg comp.x 14;Lab A;$ 1;50%;$ 1\r\n"
    "2;amoxicilina+clavulánico,ác.;AMOCLAV DUO;jbe.x 60 ml;Lab A;$ 1;50%;$ 1\r\n"
    "3;insulina humana;INSULINA INSULATARD;iny.x 10 ml;Novo;$ 1;100%;$ 0\r\n"
    "4;insulina humana;INSULINA DENSULIN R;iny.x 10 ml;Denver;$ 1;100%;$ 0\r\n"
    "5;hidrocortisona;DERMACORT;crema x 15 g;Lab B;$ 1;40%;$ 1\r\n"
    "6;paracetamol;PARACETAMOL FECOFAR;500 mg comp.x 20;Fecofar;$ 1;40%;$ 1\r\n"
).encode("latin-1")


@pytest.mark.parametrize(("entrada", "salida"), [
    ("acetilsalicilico,ac.", "acido acetilsalicilico"),
    ("potasio,cloruro", "cloruro de potasio"),
    ("sodio,divalproato", "divalproato de sodio"),
    ("losartan,potasico", "losartan potasico"),
    ("paracetamol", "paracetamol"),
])
def test_desinvertir(entrada, salida):
    assert desinvertir(entrada) == salida


def test_componentes_notacion_alfabeta():
    assert componentes("amoxicilina+clavulánico,ác.") == ["amoxicilina", "acido clavulanico"]


def test_desempate_por_marca():
    indice = {1: variantes("insulina humana regular | insulina humana"), 2: variantes("insulina humana nph | insulina humana")}
    filtros = {1: r"!\bN\b|NPH|INSULATARD|ISOF|BASAL|\d+/\d+", 2: r"\bN\b|NPH|INSULATARD|ISOF|BASAL"}
    assert mejor_principio("insulina humana", indice, "INSULINA INSULATARD", filtros) == 2
    assert mejor_principio("insulina humana", indice, "INSULINA DENSULIN N", filtros) == 2
    assert mejor_principio("insulina humana", indice, "INSULINA DENSULIN R", filtros) == 1
    assert mejor_principio("insulina humana", indice, "HUMULIN 70/30", filtros) is None


def test_es_local():
    assert es_local("0.03% sol.oft.x 3 ml") and es_local("ung.x 15 g") and es_local("crema x 15 g")
    assert not es_local("875 mg comp.x 14") and not es_local("iny.f.a.x 1")


def test_leer_y_agrupar():
    filas = leer_listado(CSV)
    assert len(filas) == 6 and filas[0]["alfabeta"] == "1"
    productos = agrupar(filas)
    amoclav = next(p for p in productos.values() if p["nombre_comercial"] == "AMOCLAV DUO")
    assert [x["id_externo"] for x in amoclav["presentaciones"]] == ["alfabeta:1", "alfabeta:2"]
    assert amoclav["presentaciones"][0]["descripcion"] == "875 mg comp.x 14 · cobertura PAMI 50%"
    assert not any(p["nombre_comercial"] == "DERMACORT" for p in productos.values())
    assert next(p for p in productos.values() if p["nombre_comercial"] == "PARACETAMOL FECOFAR")["es_generico"] is True
    with pytest.raises(PamiError):
        leer_listado(b"columna;otra\r\n1;2\r\n")


def test_cliente_verifica_organizacion_y_host():
    def manejador(req):
        if req.url.host == "datos.gob.ar":
            return httpx.Response(200, json={"result": {
                "organization": {"title": "Instituto Nacional de Servicios Sociales para Jubilados y Pensionados (PAMI)"},
                "resources": [{"format": "CSV", "url": "http://datos.pami.org.ar/dataset/x/gavade.csv"}]}})
        assert req.url.scheme == "https"  # se fuerza HTTPS
        return httpx.Response(200, content=CSV)

    cliente = PamiClient(http=httpx.Client(transport=httpx.MockTransport(manejador)), pausa_segundos=0, dormir=lambda s: None)
    url, filas = cliente.listado()
    assert url == "https://datos.pami.org.ar/dataset/x/gavade.csv" and len(filas) == 6

    def ajeno(req):
        return httpx.Response(200, json={"result": {"organization": {"title": "Otro organismo"}, "resources": []}})
    with pytest.raises(PamiError, match="no está publicado por PAMI"):
        PamiClient(http=httpx.Client(transport=httpx.MockTransport(ajeno)), pausa_segundos=0, dormir=lambda s: None).listado()


class PamiFalso:
    def __init__(self):
        self.descargas = 0

    def listado(self):
        self.descargas += 1
        return "https://datos.pami.org.ar/x.csv", leer_listado(CSV)


def test_sincronizacion_pami(db_sembrada):
    cliente = PamiFalso()
    conector = pami.conector(db_sembrada, cliente)
    dcis = ["amoxicilina + ácido clavulánico", "insulina humana regular", "insulina humana NPH", "hidrocortisona"]
    p = {d: db_sembrada.scalar(select(PrincipioActivo).where(PrincipioActivo.dci_es == d)) for d in dcis}
    for principio in p.values():
        sincronizacion.sincronizar_principio(db_sembrada, conector, principio)
    assert cliente.descargas == 1
    assert [x.nombre_comercial for x in p["amoxicilina + ácido clavulánico"].productos] == ["AMOCLAV DUO"]
    assert [x.nombre_comercial for x in p["insulina humana NPH"].productos] == ["INSULINA INSULATARD"]
    assert [x.nombre_comercial for x in p["insulina humana regular"].productos] == ["INSULINA DENSULIN R"]
    assert p["hidrocortisona"].productos == []
    assert p["insulina humana NPH"].productos[0].pais == "AR"


def test_endpoint_admin_pami(cliente_api, db_sembrada):
    from app.api.admin import get_pami_client
    from app.main import app

    app.dependency_overrides[get_pami_client] = lambda: PamiFalso()
    r = cliente_api.post("/api/v1/admin/sincronizaciones/pami", headers={"X-Admin-Key": "secreta"}, json={})
    assert r.status_code == 202
    assert cliente_api.get("/api/v1/buscar", params={"q": "amoclav"}).json()["resultados"][0]["pais"] == "AR"
