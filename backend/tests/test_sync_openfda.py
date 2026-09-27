from sqlalchemy import select

from app.connectors.openfda import OpenFDAError, ResultadoBusqueda
from app.models import PrincipioActivo, ProductoComercial
from app.services.sync_openfda import sincronizar_principio
from tests.conftest import producto_ndc


class ClienteFalso:
    def __init__(self, resultados=None, truncado=False, error=None):
        self.resultados = resultados or []
        self.truncado = truncado
        self.error = error
        self.busquedas = []

    def buscar_ndc(self, busqueda):
        self.busquedas.append(busqueda)
        if self.error:
            raise self.error
        return ResultadoBusqueda(self.resultados, len(self.resultados) + (10 if self.truncado else 0), self.truncado)


def principio(db, dci="enoxaparina") -> PrincipioActivo:
    return db.scalar(select(PrincipioActivo).where(PrincipioActivo.dci_es == dci))


LOVENOX = producto_ndc("0075-0620", [("ENOXAPARIN SODIUM", "40 mg/.4mL")], brand_name="Lovenox",
                       marketing_category="NDA", application_number="NDA020164")
GENERICO = producto_ndc("0000-1111", [("ENOXAPARIN SODIUM", "40 mg/.4mL")], brand_name="Enoxaparin Sodium")
HOMEOPATICO = producto_ndc("9999-0001", [("ENOXAPARIN SODIUM", "30 [hp_C]/1")], marketing_category="UNAPPROVED HOMEOPATHIC")


def test_crea_productos_y_filtra(db_sembrada):
    p = principio(db_sembrada)
    cliente = ClienteFalso([LOVENOX, GENERICO, HOMEOPATICO])
    sync = sincronizar_principio(db_sembrada, cliente, p)

    assert cliente.busquedas == ['active_ingredients.name:"enoxaparin"']
    assert (sync.estado, sync.n_recibidos, sync.n_creados) == ("ok", 3, 2)
    productos = {x.id_externo: x for x in p.productos}
    assert set(productos) == {"0075-0620", "0000-1111"}
    assert productos["0075-0620"].es_generico is False
    assert productos["0000-1111"].es_generico is True
    assert productos["0075-0620"].pais == "US"
    assert productos["0075-0620"].presentaciones[0].id_externo == "0075-0620-01"


def test_segunda_sincronizacion_detecta_cambios_y_bajas(db_sembrada):
    p = principio(db_sembrada)
    sincronizar_principio(db_sembrada, ClienteFalso([LOVENOX, GENERICO]), p)
    lovenox = db_sembrada.scalar(select(ProductoComercial).where(ProductoComercial.id_externo == "0075-0620"))
    fecha_contenido = lovenox.fecha_actualizacion

    cambiado = {**LOVENOX, "labeler_name": "Nuevo Titular"}
    sync = sincronizar_principio(db_sembrada, ClienteFalso([cambiado]), p)
    assert (sync.n_actualizados, sync.n_sin_cambios, sync.n_no_listados) == (1, 0, 1)
    assert lovenox.laboratorio == "Nuevo Titular"
    assert lovenox.fecha_actualizacion > fecha_contenido
    generico = db_sembrada.scalar(select(ProductoComercial).where(ProductoComercial.id_externo == "0000-1111"))
    assert generico.estado == "no_listado"

    # Sin cambios: solo se actualiza la fecha de verificación. El genérico vuelve a estar vigente.
    sync = sincronizar_principio(db_sembrada, ClienteFalso([cambiado, GENERICO]), p)
    assert (sync.n_creados, sync.n_actualizados, sync.n_sin_cambios) == (0, 1, 1)
    assert generico.estado == "vigente"


def test_descarga_truncada_no_marca_bajas(db_sembrada):
    p = principio(db_sembrada)
    sincronizar_principio(db_sembrada, ClienteFalso([LOVENOX, GENERICO]), p)
    sync = sincronizar_principio(db_sembrada, ClienteFalso([LOVENOX], truncado=True), p)
    assert sync.estado == "truncada" and sync.n_no_listados == 0
    assert all(x.estado == "vigente" for x in p.productos)


def test_combinacion(db_sembrada):
    p = principio(db_sembrada, "amoxicilina + ácido clavulánico")
    augmentin = producto_ndc("1111-2222", [("AMOXICILLIN", "875 mg/1"), ("CLAVULANATE POTASSIUM", "125 mg/1")])
    solo_amox = producto_ndc("1111-3333", [("AMOXICILLIN", "500 mg/1")])
    sync = sincronizar_principio(db_sembrada, ClienteFalso([augmentin, solo_amox]), p)
    assert sync.n_creados == 1
    assert [x.id_externo for x in p.productos] == ["1111-2222"]


def test_error_de_openfda_queda_registrado(db_sembrada):
    sync = sincronizar_principio(db_sembrada, ClienteFalso(error=OpenFDAError("caída")), principio(db_sembrada))
    assert sync.estado == "error" and sync.mensaje == "caída" and sync.finalizada_en is not None
