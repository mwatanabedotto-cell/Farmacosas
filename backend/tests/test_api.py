from sqlalchemy import select

from app.api.admin import get_openfda_client
from app.main import app
from app.models import PrincipioActivo
from app.services.sync_openfda import sincronizar_principio
from tests.conftest import producto_ndc
from tests.test_sync_openfda import ClienteFalso


def id_de(db, dci):
    return db.scalar(select(PrincipioActivo.id).where(PrincipioActivo.dci_es == dci))


def test_salud(cliente_api):
    r = cliente_api.get("/salud")
    assert r.status_code == 200 and "juicio clínico" in r.json()["aviso"]
    assert r.headers["X-Content-Type-Options"] == "nosniff"


def test_buscar_por_dci_sin_tildes_y_nombre_estadounidense(cliente_api):
    r = cliente_api.get("/api/v1/buscar", params={"q": "ACIDO TRANEX"}).json()
    assert r["resultados"][0]["principio"]["dci_es"] == "ácido tranexámico"
    r = cliente_api.get("/api/v1/buscar", params={"q": "acetaminophen"}).json()
    assert [x["principio"]["dci_es"] for x in r["resultados"]] == ["paracetamol"]


def test_buscar_por_atc(cliente_api):
    r = cliente_api.get("/api/v1/buscar", params={"q": "b01ab05"}).json()
    assert r["resultados"][0] == {**r["resultados"][0], "coincidencia": "atc"}
    assert r["resultados"][0]["principio"]["dci_es"] == "enoxaparina"


def test_buscar_por_nombre_comercial_y_disponibilidad(cliente_api, db_sembrada):
    pid = id_de(db_sembrada, "enoxaparina")
    lovenox = producto_ndc("0075-0620", [("ENOXAPARIN SODIUM", "40 mg/.4mL")], brand_name="Lovenox", marketing_category="NDA")
    sincronizar_principio(db_sembrada, ClienteFalso([lovenox]), db_sembrada.get(PrincipioActivo, pid))

    r = cliente_api.get("/api/v1/buscar", params={"q": "loven"}).json()
    assert r["resultados"] == [{
        "tipo": "nombre_comercial", "coincidencia": "nombre_comercial", "nombre_comercial": "Lovenox", "pais": "US",
        "principio": {"id": pid, "dci_es": "enoxaparina", "dci_en": "enoxaparin", "atc": "B01AB05",
                      "grupo": "Cardiovascular y antitrombóticos"},
    }]
    assert cliente_api.get("/api/v1/buscar", params={"q": "loven", "pais": "MX"}).json()["resultados"] == []

    detalle = cliente_api.get(f"/api/v1/principios/{pid}").json()
    assert detalle["disponibilidad"][0] | {"ultima_verificacion": None} == {
        "pais": "US", "productos": 1, "vigentes": 1, "genericos": 0, "ultima_verificacion": None}

    productos = cliente_api.get(f"/api/v1/principios/{pid}/productos", params={"pais": "us"}).json()
    assert productos["total"] == 1
    assert productos["resultados"][0]["fuente"]["agencia"] == "FDA"
    assert productos["resultados"][0]["presentaciones"][0]["descripcion"] == "10 SYRINGE in 1 CARTON"


def test_listar_y_404(cliente_api):
    r = cliente_api.get("/api/v1/principios", params={"grupo": "Anestesia y urgencias"}).json()
    assert r["total"] == 12
    assert cliente_api.get("/api/v1/principios/9999").status_code == 404


def test_admin_requiere_clave(cliente_api):
    assert cliente_api.get("/api/v1/admin/sincronizaciones").status_code == 401
    r = cliente_api.get("/api/v1/admin/sincronizaciones", headers={"X-Admin-Key": "mala"})
    assert r.status_code == 401


def test_admin_sincroniza_en_segundo_plano(cliente_api, db_sembrada):
    pid = id_de(db_sembrada, "enoxaparina")
    falso = ClienteFalso([producto_ndc("0075-0620", [("ENOXAPARIN SODIUM", "40 mg/.4mL")])])
    app.dependency_overrides[get_openfda_client] = lambda: falso
    cabeceras = {"X-Admin-Key": "secreta"}

    r = cliente_api.post("/api/v1/admin/sincronizaciones/openfda", json={"principio_ids": [pid]}, headers=cabeceras)
    assert r.status_code == 202
    syncs = cliente_api.get("/api/v1/admin/sincronizaciones", headers=cabeceras).json()
    assert [(s["principio_activo_id"], s["estado"], s["n_creados"]) for s in syncs] == [(pid, "ok", 1)]
