import httpx
import pytest
from sqlalchemy import select

from app.connectors.base import html_a_texto
from app.connectors.cima import (
    CimaClient,
    CimaError,
    ConectorCima,
    codigos_atc,
    estado,
    marca,
    normalizar_ficha,
    normalizar_medicamento,
)
from app.models import FichaTecnica, PrincipioActivo
from app.services import fichas_cima, sincronizacion
from app.services.monografias import TIPOS, generar_borrador, texto_desde_ficha
from tests.conftest import medicamento_cima


def cliente_con(manejador) -> CimaClient:
    http = httpx.Client(base_url="https://cima.aemps.es/cima/rest", transport=httpx.MockTransport(manejador))
    return CimaClient(http=http, pausa_segundos=0, dormir=lambda s: None)


def principio(db, dci="enoxaparina") -> PrincipioActivo:
    return db.scalar(select(PrincipioActivo).where(PrincipioActivo.dci_es == dci))


@pytest.mark.parametrize(("nombre", "esperado"), [
    ("CLEXANE 4.000 UI (40 mg)/ 0,4 ml SOLUCION INYECTABLE EN JERINGA PRECARGADA", "CLEXANE"),
    ("ENOXAPARINA ROVI 10.000 UI (100 MG)/1 ML SOLUCION INYECTABLE", "ENOXAPARINA ROVI"),
    ("A.A.S. 100 mg COMPRIMIDOS", "A.A.S."),
    ("HUMULINA NPH KWIKPEN 100 UI/ml SUSPENSION INYECTABLE", "HUMULINA NPH KWIKPEN"),
    ("LANTUS SOLOSTAR SOLUCION INYECTABLE", "LANTUS SOLOSTAR"),
    ("NOLOTIL", "NOLOTIL"),
    ("INSULEX ® R100 UI/ML (INSULINA HUMANA)", "INSULEX"),
    ("WOSULIN ® R 100UI/ML SOLUCIÓN INYECTABLE", "WOSULIN R"),
    ("CARDIOASPIRINA® 81MG TABLETAS", "CARDIOASPIRINA"),
    ("NOVALGINA TABLETAS", "NOVALGINA"),
    ("SULFATO DE MAGNESIO AL 20% SOLUCION", "SULFATO DE MAGNESIO"),
])
def test_marca(nombre, esperado):
    assert marca(nombre) == esperado


def test_estado():
    assert estado({"comerc": True, "estado": {"aut": 1}}) == "vigente"
    assert estado({"comerc": False, "estado": {"aut": 1}}) == "no_comercializado"
    assert estado({"comerc": True, "estado": {"aut": 1, "susp": 2}}) == "suspendido"
    assert estado({"comerc": False, "estado": {"aut": 1, "rev": 2}}) == "revocado"


def test_codigos_atc():
    class P:
        atc = "J01XD01 / P01AB01"
    assert codigos_atc(P) == ["J01XD01", "P01AB01"]
    P.atc = "B01A"
    assert codigos_atc(P) == []


def test_normalizar_medicamento():
    d = normalizar_medicamento(medicamento_cima("82490", "ENOXAPARINA ROVI 40 mg SOLUCION", generico=True, psum=True,
                                                comerc=False))
    assert d["nombre_comercial"] == "ENOXAPARINA ROVI" and d["descripcion"].startswith("ENOXAPARINA ROVI 40")
    assert (d["categoria_registro"], d["es_generico"], d["estado"], d["problema_suministro"]) == ("EFG", True, "no_comercializado", True)
    assert d["spl_set_id"] == "82490" and d["url_fuente"].endswith("nregistro=82490")
    sin_segmentar = medicamento_cima("1", "X 1 mg", docs=[{"tipo": 1, "url": "u", "secc": False}])
    assert normalizar_medicamento(sin_segmentar)["spl_set_id"] is None


def test_html_a_texto():
    html = "<div><p>Uno&#xa0; <b>dos</b></p><ul><li>a</li><li>b</li></ul><table><tr><td>x</td><td>y</td></tr></table><p>&#xa0;</p></div>"
    assert html_a_texto(html) == "Uno dos\n• a\n• b\n| x | y"
    assert html_a_texto(None) == ""


def test_paginacion_y_reintentos():
    peticiones = []
    fallos = iter([True, False, False])

    def manejador(req):
        pagina = int(req.url.params["pagina"])
        peticiones.append((req.url.params["atc"], pagina))
        if next(fallos, False):
            raise httpx.ConnectError("reset")
        n = 200 if pagina == 1 else 24
        return httpx.Response(200, json={"totalFilas": 224, "resultados": [{"nregistro": f"{pagina}-{i}"} for i in range(n)]})

    meds, total, truncado = cliente_con(manejador).medicamentos_por_atc("N02BE01")
    assert (len(meds), total, truncado) == (224, 224, False)
    assert peticiones == [("N02BE01", 1), ("N02BE01", 1), ("N02BE01", 2)]


def test_error_de_cliente():
    with pytest.raises(CimaError):
        cliente_con(lambda req: httpx.Response(400, text="mal")).medicamentos_por_atc("X")


class CimaFalso:
    """Cliente CIMA en memoria."""

    def __init__(self, meds: dict[str, list[dict]], detalles=None, secciones=None):
        self.meds, self.detalles, self.secciones = meds, detalles or {}, secciones or {}
        self.llamadas: list[tuple] = []

    def medicamentos_por_atc(self, atc):
        self.llamadas.append(("lista", atc))
        meds = self.meds.get(atc, [])
        return meds, len(meds), False

    def medicamento(self, nregistro):
        self.llamadas.append(("detalle", nregistro))
        return self.detalles.get(nregistro)

    def secciones_ficha(self, nregistro):
        self.llamadas.append(("secciones", nregistro))
        return self.secciones.get(nregistro, [])


CLEXANE = medicamento_cima("58502", "CLEXANE 4.000 UI (40 mg)/0,4 ml SOLUCION INYECTABLE")
ROVI = medicamento_cima("82490", "ENOXAPARINA ROVI 40 mg SOLUCION INYECTABLE", biosimilar=True)
EFG = medicamento_cima("90000", "ENOXAPARINA NORMON 40 mg SOLUCION INYECTABLE EFG", generico=True)
DETALLES = {
    "58502": {**CLEXANE, "principiosActivos": [{"nombre": "ENOXAPARINA SODICA", "cantidad": "40", "unidad": "mg", "orden": 1}],
              "presentaciones": [{"cn": "639484", "nombre": "CLEXANE ..., 10 jeringas", "estado": {"aut": 635122800000}}]},
    "82490": {**ROVI, "presentaciones": [{"cn": "719277", "nombre": "ROVI ..., 10 jeringas", "estado": {"aut": 1519712657000}}]},
    "90000": {**EFG, "presentaciones": [{"cn": "700000", "nombre": "NORMON ...", "estado": {"aut": 1300000000000}}]},
}
SECCIONES = {"58502": [
    {"seccion": "1", "titulo": "NOMBRE DEL MEDICAMENTO", "contenido": "<p>CLEXANE</p>"},
    {"seccion": "4.1", "titulo": "Indicaciones terapéuticas", "contenido": "<p>Profilaxis de la ETV.</p>"},
    {"seccion": "4.2.1", "titulo": "Posología", "contenido": "<p>40 mg una vez al día.</p><p>Insuficiencia renal grave (aclaramiento de creatinina &lt;30 ml/min): 20 mg una vez al día.</p>"},
    {"seccion": "4.2.2", "titulo": "Forma de administración", "contenido": "<p>Inyección subcutánea profunda.</p>"},
    {"seccion": "4.3", "titulo": "Contraindicaciones", "contenido": "<p>Hemorragia activa.</p>"},
    {"seccion": "4.4", "titulo": "Advertencias", "contenido": "<p>Riesgo de hemorragia. Precaución en insuficiencia hepática.</p>"},
    {"seccion": "4.6.1", "titulo": "Embarazo", "contenido": "<p>No atraviesa la placenta.</p>"},
    {"seccion": "4.6.2", "titulo": "Lactancia", "contenido": "<p>Puede utilizarse durante la lactancia.</p>"},
    {"seccion": "5.1", "titulo": "Propiedades farmacodinámicas", "contenido": "<p>Grupo: heparinas.</p><p>Inhibe el factor Xa.</p>"},
    {"seccion": "6.1", "titulo": "Lista de excipientes", "contenido": "<p>Agua</p>"},
]}


def test_sincronizacion_cima(db_sembrada):
    p = principio(db_sembrada)
    cliente = CimaFalso({"B01AB05": [CLEXANE, ROVI, EFG]}, DETALLES)
    sync = sincronizacion.sincronizar_principio(db_sembrada, ConectorCima(cliente), p)
    assert (sync.estado, sync.n_creados, sync.consulta) == ("ok", 3, "atc=B01AB05")
    clexane = next(x for x in p.productos if x.id_externo == "58502")
    assert (clexane.pais, clexane.nombre_comercial, clexane.composicion) == ("ES", "CLEXANE", "ENOXAPARINA SODICA 40 mg")
    assert [x.id_externo for x in clexane.presentaciones] == ["639484"]
    assert clexane.presentaciones[0].fecha_inicio_comercializacion == "1990-02-15"

    # Sin cambios: no se vuelve a pedir el detalle.
    cliente.llamadas.clear()
    sync = sincronizacion.sincronizar_principio(db_sembrada, ConectorCima(cliente), p)
    assert sync.n_sin_cambios == 3 and cliente.llamadas == [("lista", "B01AB05")]

    # Deja de comercializarse: se actualiza; y uno desaparece de CIMA: no_listado.
    cliente.meds["B01AB05"] = [{**CLEXANE, "comerc": False}, ROVI]
    sync = sincronizacion.sincronizar_principio(db_sembrada, ConectorCima(cliente), p)
    assert (sync.n_actualizados, sync.n_sin_cambios, sync.n_no_listados) == (1, 1, 1)
    assert clexane.estado == "no_comercializado"


def test_ficha_cima_y_borrador_en_espanol(db_sembrada):
    p = principio(db_sembrada)
    cliente = CimaFalso({"B01AB05": [CLEXANE, ROVI, EFG]}, DETALLES, SECCIONES)
    sincronizacion.sincronizar_principio(db_sembrada, ConectorCima(cliente), p)
    # Original (autorizado primero, no genérico) antes que biosimilar y EFG.
    assert fichas_cima.candidatos(db_sembrada, p) == ["58502", "82490", "90000"]

    ficha, resultado = fichas_cima.importar_ficha(db_sembrada, cliente, p)
    assert resultado == "creada" and (ficha.pais, ficha.idioma, ficha.titulo) == ("ES", "es", "CLEXANE (enoxaparina sodio)")
    assert ficha.version == "2022-10-07" and ficha.spl_id == "58502:1665182910000"
    assert [s.codigo for s in ficha.secciones] == ["4.1", "4.2.1", "4.2.2", "4.3", "4.4", "4.6.1", "4.6.2", "5.1"]

    # Misma fecha de ficha: no se descargan de nuevo las secciones.
    cliente.llamadas.clear()
    assert fichas_cima.importar_ficha(db_sembrada, cliente, p)[1] == "sin_cambios"
    assert ("secciones", "58502") not in cliente.llamadas

    t = lambda tipo: texto_desde_ficha(ficha, TIPOS[tipo])  # noqa: E731
    assert t("indicaciones") == "Profilaxis de la ETV."
    assert t("posologia_adultos").startswith("40 mg una vez al día.")
    assert t("posologia_ninos") is None  # la ficha de prueba no tiene subsección pediátrica
    assert t("modo_administracion") == "Inyección subcutánea profunda."
    assert t("insuficiencia_renal") == "Insuficiencia renal grave (aclaramiento de creatinina <30 ml/min): 20 mg una vez al día."
    assert t("insuficiencia_hepatica") == "Precaución en insuficiencia hepática."
    assert t("embarazo_lactancia") == "No atraviesa la placenta.\nPuede utilizarse durante la lactancia."
    assert t("mecanismo_farmacocinetica") == "Grupo: heparinas.\nInhibe el factor Xa."
    assert t("alerta") is None and t("perioperatorio") is None

    mono = generar_borrador(db_sembrada, p)
    assert mono.ficha_id == ficha.id
    s = {x.tipo: x for x in mono.secciones}
    assert s["indicaciones"].idioma == "es"
    assert s["indicaciones"].referencias[0].clave == "cima:58502:2022-10-07"


def test_borrador_prefiere_ficha_espanola(db_sembrada):
    from app.services.fichas import obtener_fuente as fuente_label
    from app.models import ahora

    p = principio(db_sembrada)
    us = FichaTecnica(principio_activo=p, fuente=fuente_label(db_sembrada), pais="US", idioma="en", set_id="s", spl_id="x",
                      titulo="Lovenox", url="u", hash_contenido="h", fecha_extraccion=ahora(), fecha_actualizacion=ahora())
    db_sembrada.add(us)
    db_sembrada.commit()
    assert generar_borrador(db_sembrada, p).ficha_id == us.id

    cliente = CimaFalso({"B01AB05": [CLEXANE]}, DETALLES, SECCIONES)
    sincronizacion.sincronizar_principio(db_sembrada, ConectorCima(cliente), p)
    es, _ = fichas_cima.importar_ficha(db_sembrada, cliente, p)
    mono = generar_borrador(db_sembrada, p)
    assert mono.ficha_id == es.id
    assert {x.tipo: x for x in mono.secciones}["indicaciones"].contenido == "Profilaxis de la ETV."


def test_normalizar_ficha_sin_fecha():
    med = {**CLEXANE, "docs": [{"tipo": 1, "url": "u", "secc": True}]}
    datos = normalizar_ficha(med, SECCIONES["58502"])
    assert datos["version"] is None and datos["url"] == "u"


def test_endpoint_admin_cima(cliente_api, db_sembrada):
    from app.api.admin import get_cima_client
    from app.main import app

    p = principio(db_sembrada)
    app.dependency_overrides[get_cima_client] = lambda: CimaFalso({"B01AB05": [CLEXANE]}, DETALLES, SECCIONES)
    r = cliente_api.post("/api/v1/admin/sincronizaciones/cima", headers={"X-Admin-Key": "secreta"},
                         json={"principio_ids": [p.id]})
    assert r.status_code == 202
    syncs = cliente_api.get("/api/v1/admin/sincronizaciones", headers={"X-Admin-Key": "secreta"}).json()
    assert [s["estado"] for s in syncs] == ["ok", "ok"]
    fichas = cliente_api.get(f"/api/v1/principios/{p.id}/fichas-tecnicas", params={"pais": "ES"}).json()
    assert fichas[0]["secciones"][0] == {"codigo": "4.1", "titulo": "Indicaciones terapéuticas", "texto": "Profilaxis de la ETV."}
    productos = cliente_api.get(f"/api/v1/principios/{p.id}/productos", params={"pais": "ES"}).json()["resultados"]
    assert productos[0]["descripcion"].startswith("CLEXANE 4.000") and productos[0]["problema_suministro"] is False
    assert cliente_api.get("/api/v1/buscar", params={"q": "clexane"}).json()["resultados"][0]["pais"] == "ES"
