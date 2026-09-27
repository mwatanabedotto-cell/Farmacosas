import httpx
import pytest
from sqlalchemy import select

from app.connectors.invima import (
    ConectorInvima,
    InvimaClient,
    InvimaError,
    agrupar_expedientes,
    es_generico,
)
from app.models import PrincipioActivo
from app.services import sincronizacion


def fila(expediente, consecutivo, producto="CLENOX ®", **extra) -> dict:
    """Fila con la forma del CUM de INVIMA en datos.gov.co."""
    base = {
        "expediente": expediente, "producto": producto, "titular": "PROCAPS S.A.",
        "registrosanitario": "INVIMA 2024MB-0004412-R2", "estadoregistro": "Vigente",
        "expedientecum": expediente, "consecutivocum": str(consecutivo),
        "descripcioncomercial": f"CAJA CON {consecutivo} JERINGAS", "estadocum": "Activo",
        "fechaactivo": "11/10/2006", "muestramedica": "No", "atc": "B01AB05",
        "viaadministracion": "SUBCUTANEA", "principioactivo": "ENOXAPARINA SODICA", "unidadmedida": "mg",
        "cantidad": "40", "formafarmaceutica": "SOLUCION INYECTABLE", "tiporol": "FABRICANTE",
    }
    base.update(extra)
    return base


def principio(db, dci="enoxaparina") -> PrincipioActivo:
    return db.scalar(select(PrincipioActivo).where(PrincipioActivo.dci_es == dci))


@pytest.mark.parametrize(("nombre", "esperado"), [
    ("CLENOX ®", False),
    ("ENOXAPARINASODICA JERINGAS 80 MG", True),
    ("DIPIRONA SÓDICA 1G/2ML", True),
    ("NOVALGINA TABLETAS", False),
    ("AMOXICILINA/ACIDO CLAVULANICO 875 MG", True),
])
def test_es_generico(nombre, esperado):
    genericos = ["enoxaparina", "metamizol", "dipirona", "amoxicilina + acido clavulanico"]
    assert es_generico(nombre, genericos) is esperado


def test_agrupar_expedientes():
    filas = [
        fila("1", 1), fila("1", 1, tiporol="IMPORTADOR"),  # misma presentación, otro rol
        fila("1", 2, estadocum="Inactivo"),
        fila("1", 3, muestramedica="Si"),
        fila("1", 4, viaadministracion="INTRAVENOSA"),
        fila("2", 1, producto="ENOXAPARINA SODICA 40 MG", estadocum="Inactivo", principioactivo="ENOXAPARINA 40 MG"),
    ]
    productos = agrupar_expedientes(filas, "i7cb-raxc", ["enoxaparina"])
    clenox, generico = productos["1"], productos["2"]
    assert (clenox["nombre_comercial"], clenox["es_generico"], clenox["estado"]) == ("CLENOX", False, "vigente")
    assert [p["id_externo"] for p in clenox["presentaciones"]] == ["1-1", "1-4"]
    assert clenox["presentaciones"][0]["fecha_inicio_comercializacion"] == "2006-11-10"
    assert clenox["via"] == "INTRAVENOSA, SUBCUTANEA"
    assert clenox["composicion"] == "ENOXAPARINA SODICA 40 mg"
    assert clenox["url_fuente"].endswith("i7cb-raxc.json?expediente=1")
    assert (generico["es_generico"], generico["estado"], generico["composicion"]) == (True, "no_comercializado", "ENOXAPARINA 40 MG")


def cliente_con(manejador) -> InvimaClient:
    http = httpx.Client(base_url="https://www.datos.gov.co", transport=httpx.MockTransport(manejador))
    return InvimaClient(http=http, pausa_segundos=0, dormir=lambda s: None)


def test_cliente_verifica_propietario_y_pagina(monkeypatch):
    monkeypatch.setattr("app.connectors.invima.TAMANO_PAGINA", 2)
    peticiones = []

    def manejador(req):
        if req.url.path.startswith("/api/views/"):
            return httpx.Response(200, json={"owner": {"displayName": "Invima"}})
        peticiones.append((req.url.params["$where"], req.url.params["$offset"]))
        offset = int(req.url.params["$offset"])
        return httpx.Response(200, json=[fila("1", i) for i in range(offset, min(offset + 2, 3))])

    cliente = cliente_con(manejador)
    filas, truncado = cliente.filas_por_atc("i7cb-raxc", ["B01AB05", "B01"])
    assert (len(filas), truncado) == (3, False)
    assert peticiones == [("atc in ('B01AB05')", "0"), ("atc in ('B01AB05')", "2")]


def test_rechaza_conjunto_de_terceros():
    cliente = cliente_con(lambda req: httpx.Response(200, json={"owner": {"displayName": "jairolopezlon"}}))
    with pytest.raises(InvimaError, match="no está publicado por Invima"):
        cliente.filas_por_atc("3iz9-pfjh", ["B01AB05"])


class InvimaFalso:
    def __init__(self, filas: dict[str, list[dict]]):
        self.filas = filas

    def filas_por_atc(self, conjunto, codigos):
        return [f for f in self.filas.get(conjunto, []) if f["atc"] in codigos], False


def test_sincronizacion_invima(db_sembrada):
    p = principio(db_sembrada)
    cliente = InvimaFalso({"i7cb-raxc": [fila("1", 1)], "vgr4-gemg": [fila("2", 1, producto="CLEXANE INYECTABLE 20 MG")]})
    sync = sincronizacion.sincronizar_principio(db_sembrada, ConectorInvima(cliente), p)
    assert (sync.estado, sync.n_creados) == ("ok", 2)
    assert {(x.pais, x.nombre_comercial) for x in p.productos} == {("CO", "CLENOX"), ("CO", "CLEXANE")}

    del cliente.filas["vgr4-gemg"]
    sync = sincronizacion.sincronizar_principio(db_sembrada, ConectorInvima(cliente), p)
    assert (sync.n_sin_cambios, sync.n_no_listados) == (1, 1)


def test_endpoint_admin_invima(cliente_api, db_sembrada):
    from app.api.admin import get_invima_client
    from app.main import app

    p = principio(db_sembrada)
    app.dependency_overrides[get_invima_client] = lambda: InvimaFalso({"i7cb-raxc": [fila("1", 1)]})
    r = cliente_api.post("/api/v1/admin/sincronizaciones/invima", headers={"X-Admin-Key": "secreta"},
                         json={"principio_ids": [p.id]})
    assert r.status_code == 202
    detalle = cliente_api.get(f"/api/v1/principios/{p.id}").json()
    assert detalle["disponibilidad"][0]["pais"] == "CO"
    assert cliente_api.get("/api/v1/buscar", params={"q": "clenox", "pais": "CO"}).json()["resultados"][0]["nombre_comercial"] == "CLENOX"
