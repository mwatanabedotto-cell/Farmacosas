from app.services import monografias as m
from tests.test_monografias import PAUTA, SECCIONES, crear_ficha, principio

H = {"X-Admin-Key": "secreta"}


def preparar(db):
    p = principio(db)
    mono = m.generar_borrador(db, p, crear_ficha(db, p, **SECCIONES))
    return p.id, mono.id


def test_ficha_tecnica_publica(cliente_api, db_sembrada):
    pid, _ = preparar(db_sembrada)
    fichas = cliente_api.get(f"/api/v1/principios/{pid}/fichas-tecnicas").json()
    assert len(fichas) == 1
    f = fichas[0]
    assert (f["pais"], f["idioma"], f["version"], f["fuente"]["codigo"]) == ("US", "en", "1", "openfda_label")
    assert "idioma original" in f["aviso"]
    codigos = [s["codigo"] for s in f["secciones"]]
    assert "pregnancy" not in codigos  # incluida en use_in_specific_populations
    assert f["secciones"][0]["titulo"] == "Advertencia en recuadro (boxed warning)"
    assert cliente_api.get(f"/api/v1/principios/{pid}/fichas-tecnicas", params={"pais": "ES"}).json() == []


def test_flujo_editorial_completo(cliente_api, db_sembrada):
    pid, mid = preparar(db_sembrada)
    assert cliente_api.get(f"/api/v1/principios/{pid}/monografia").status_code == 404
    assert cliente_api.get(f"/api/v1/admin/principios/{pid}/borrador").status_code == 401

    b = cliente_api.get(f"/api/v1/admin/principios/{pid}/borrador", headers=H).json()
    assert b["publicable"] is False and b["checklist"] == m.CHECKLIST
    ref = next(s for s in b["secciones"] if s["tipo"] == "indicaciones")["referencia_ids"][0]

    r = cliente_api.post("/api/v1/admin/referencias", headers=H, json={
        "tipo": "articulo", "titulo": "Prevención de TEV en cirugía", "autores": "Gould MK, et al.",
        "publicacion": "Chest", "fecha_publicacion": "2012", "pmid": "22315263"})
    assert r.status_code == 201 and r.json()["texto"].startswith("Gould MK, et al. Prevención")
    articulo = r.json()["id"]
    dup = cliente_api.post("/api/v1/admin/referencias", headers=H, json={"tipo": "articulo", "titulo": "Otra", "pmid": "22315263"})
    assert dup.status_code == 409
    assert [x["id"] for x in cliente_api.get("/api/v1/admin/referencias", headers=H, params={"q": "Gould"}).json()] == [articulo]

    for s in b["secciones"]:
        if s["origen"] == "ficha_importada":
            r = cliente_api.put(f"/api/v1/admin/monografias/{mid}/secciones/{s['tipo']}", headers=H,
                                json={"contenido": f"Revisado: {s['titulo']}", "referencia_ids": [ref]})
            assert r.status_code == 200
    r = cliente_api.put(f"/api/v1/admin/monografias/{mid}/pautas", headers=H,
                        json=[{**PAUTA, "referencia_ids": [ref, articulo]}])
    assert r.status_code == 200 and r.json()["publicable"] is True
    assert r.json()["pautas"][0]["referencia_ids"] == [ref, articulo]

    mala = cliente_api.put(f"/api/v1/admin/monografias/{mid}/pautas", headers=H, json=[{**PAUTA, "poblacion": "bebes"}])
    assert mala.status_code == 422

    r = cliente_api.post(f"/api/v1/admin/monografias/{mid}/publicar", headers=H,
                         json={"revisor": "Dra. Revisora", "checklist": {"dosis_verificadas": True}})
    assert r.status_code == 422 and "Checklist incompleta" in r.json()["detail"]["errores"][0]
    r = cliente_api.post(f"/api/v1/admin/monografias/{mid}/publicar", headers=H,
                         json={"revisor": "Dra. Revisora", "checklist": {k: True for k in m.CHECKLIST}})
    assert r.status_code == 200 and r.json()["version"] == 1

    pub = cliente_api.get(f"/api/v1/principios/{pid}/monografia").json()
    assert pub["dci"] == "enoxaparina" and pub["frescura"]["nivel"] == "verde"
    assert pub["pautas"][0]["citas"] == [1, 2]
    assert pub["secciones"][0]["tipo"] == "alerta"
    assert "juicio clínico" in pub["aviso"]

    editar_publicada = cliente_api.put(f"/api/v1/admin/monografias/{mid}/secciones/alerta", headers=H,
                                       json={"contenido": "x", "referencia_ids": [ref]})
    assert editar_publicada.status_code == 422


def test_sincronizacion_de_fichas_en_segundo_plano(cliente_api, db_sembrada):
    from app.api.admin import get_openfda_client
    from app.main import app
    from app.services.sync_openfda import sincronizar_principio
    from tests.conftest import ficha_label, producto_ndc
    from tests.test_fichas import ClienteFichas
    from tests.test_sync_openfda import ClienteFalso

    p = principio(db_sembrada)
    sincronizar_principio(db_sembrada, ClienteFalso([producto_ndc(
        "0075-0620", [("ENOXAPARIN SODIUM", "40 mg/.4mL")], marketing_category="NDA", openfda={"spl_set_id": ["s1"]})]), p)
    app.dependency_overrides[get_openfda_client] = lambda: ClienteFichas({"s1": ficha_label("s1", contraindications="4 C")})
    r = cliente_api.post("/api/v1/admin/sincronizaciones/fichas-openfda", headers=H, json={"principio_ids": [p.id]})
    assert r.status_code == 202
    ultima = cliente_api.get("/api/v1/admin/sincronizaciones", headers=H).json()[0]
    assert ultima["estado"] == "ok" and ultima["mensaje"].startswith("creada: Lovenox")
    b = cliente_api.get(f"/api/v1/admin/principios/{p.id}/borrador", headers=H).json()
    assert next(s for s in b["secciones"] if s["tipo"] == "contraindicaciones")["contenido"] == "4 C"
