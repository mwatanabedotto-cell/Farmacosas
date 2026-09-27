from datetime import timedelta

import pytest
from sqlalchemy import select

from app.models import FichaTecnica, PrincipioActivo, SeccionFicha, ahora
from app.services import monografias as m
from app.services.fichas import obtener_fuente
from app.services.sync_openfda import sincronizar_principio
from tests.conftest import producto_ndc
from tests.test_sync_openfda import ClienteFalso

CHECKLIST_OK = {k: True for k in m.CHECKLIST}
PAUTA = {"indicacion": "Profilaxis de TVP en cirugía abdominal", "poblacion": "adultos", "dosis": "40 mg",
         "via": "subcutánea", "frecuencia": "cada 24 h", "duracion": "7–10 días", "dosis_maxima": None, "notas": None}


def principio(db, dci="enoxaparina") -> PrincipioActivo:
    return db.scalar(select(PrincipioActivo).where(PrincipioActivo.dci_es == dci))


def crear_ficha(db, p, spl_id="spl-1", version="1", **secciones) -> FichaTecnica:
    ficha = db.scalar(select(FichaTecnica).where(FichaTecnica.principio_activo_id == p.id))
    if ficha is None:
        ficha = FichaTecnica(principio_activo=p, fuente=obtener_fuente(db), pais="US", idioma="en", set_id="set-1",
                             titulo="Lovenox (enoxaparin sodium)", laboratorio="Sanofi", url="https://dailymed/x",
                             hash_contenido="h", fecha_extraccion=ahora(), fecha_actualizacion=ahora())
        db.add(ficha)
    ficha.spl_id, ficha.version, ficha.fecha_efectiva = spl_id, version, "2026-05-28"
    ficha.secciones = [SeccionFicha(codigo=c, orden=i, texto=t) for i, (c, t) in enumerate(secciones.items())]
    db.commit()
    return ficha


SECCIONES = dict(
    boxed_warning="WARNING: SPINAL/EPIDURAL HEMATOMAS",
    indications_and_usage="1 INDICATIONS Prophylaxis of DVT.",
    dosage_and_administration="2.1 Adults: 40 mg once daily. 2.3 In severe renal impairment (creatinine clearance <30 mL/min) use 30 mg once daily. Inject subcutaneously.",
    contraindications="4 CONTRAINDICATIONS Active major bleeding.",
    warnings_and_cautions="5 WARNINGS Bleeding risk.",
    use_in_specific_populations="8 USE. 8.1 Pregnancy Risk Summary data. Benzyl alcohol caused hepatic failure in neonates. 8.2 Lactation It is unknown whether it is excreted in human milk. Hepatic Impairment: use with care in patients with hepatic impairment.",
    pregnancy="8.1 Pregnancy Risk Summary data.",
    pediatric_use="Benzyl alcohol caused hepatic failure in neonates.",
)


def borrador(db):
    p = principio(db)
    return m.generar_borrador(db, p, crear_ficha(db, p, **SECCIONES))


def secciones(mono):
    return {s.tipo: s for s in mono.secciones}


def revisar_todo(db, mono, ref_id):
    for s in mono.secciones:
        if s.origen == "ficha_importada":
            m.editar_seccion(db, mono, s.tipo, f"Texto revisado de {s.tipo}", "es", [ref_id])
    m.reemplazar_pautas(db, mono, [(PAUTA, [ref_id])])


def test_borrador_desde_ficha(db_sembrada):
    mono = borrador(db_sembrada)
    s = secciones(mono)
    assert [x.tipo for x in mono.secciones] == [t.tipo for t in m.TIPOS_SECCION]
    assert s["alerta"].contenido.startswith("WARNING") and s["alerta"].origen == "ficha_importada"
    assert s["modo_administracion"].origen == "vacia"
    assert s["insuficiencia_renal"].contenido == "2.3 In severe renal impairment (creatinine clearance <30 mL/min) use 30 mg once daily."
    # La frase de uso pediátrico ("hepatic failure") no se cuela en insuficiencia hepática.
    assert s["insuficiencia_hepatica"].contenido == "Hepatic Impairment: use with care in patients with hepatic impairment."
    assert s["lactancia"].contenido == "8.2 Lactation It is unknown whether it is excreted in human milk."
    ref = s["indicaciones"].referencias[0]
    assert ref.tipo == "ficha_tecnica" and ref.clave == "dailymed:set-1:1"
    # Idempotente
    assert m.generar_borrador(db_sembrada, principio(db_sembrada), mono.ficha).id == mono.id


def test_no_se_publica_sin_revision_ni_pautas(db_sembrada):
    mono = borrador(db_sembrada)
    errores = m.errores_publicacion(mono, {})
    assert any("Checklist incompleta" in e for e in errores)
    assert "«Indicaciones» contiene texto importado sin revisar" in errores
    assert "La posología necesita al menos una pauta estructurada" in errores
    with pytest.raises(m.ErrorEditorial):
        m.publicar(db_sembrada, mono, "Dra. Revisora", CHECKLIST_OK)


def test_edicion_valida_referencias_y_estado(db_sembrada):
    mono = borrador(db_sembrada)
    with pytest.raises(m.ErrorEditorial, match="inexistentes"):
        m.editar_seccion(db_sembrada, mono, "indicaciones", "x", "es", [999])
    with pytest.raises(m.ErrorEditorial, match="desconocida"):
        m.editar_seccion(db_sembrada, mono, "no_existe", "x", "es", [])
    with pytest.raises(m.ErrorEditorial, match="población"):
        m.reemplazar_pautas(db_sembrada, mono, [({**PAUTA, "poblacion": "bebes"}, [])])
    s = m.editar_seccion(db_sembrada, mono, "alerta", "   ", "es", [])
    assert s.origen == "vacia" and s.contenido is None


def test_publicar_y_vista_vademecum(db_sembrada):
    p = principio(db_sembrada)
    sincronizar_principio(db_sembrada, ClienteFalso([
        producto_ndc("0075-0620", [("ENOXAPARIN SODIUM", "40 mg/.4mL")], brand_name="Lovenox", marketing_category="NDA"),
        producto_ndc("0000-1111", [("ENOXAPARIN SODIUM", "40 mg/.4mL")], brand_name="Enoxaparin Sodium"),
    ]), p)
    mono = borrador(db_sembrada)
    ref_ficha = secciones(mono)["indicaciones"].referencias[0].id
    revisar_todo(db_sembrada, mono, ref_ficha)
    articulo = m.Referencia(tipo="articulo", titulo="Guía de tromboprofilaxis", autores="Pérez A", publicacion="Chest",
                            fecha_publicacion="2024", pmid="123", fecha_acceso="2026-09-27")
    db_sembrada.add(articulo)
    db_sembrada.commit()
    m.editar_seccion(db_sembrada, mono, "perioperatorio", "Suspender 12 h antes de anestesia neuraxial.", "es",
                     [articulo.id, ref_ficha])
    m.editar_seccion(db_sembrada, mono, "sobredosis", None, "es", [])
    m.publicar(db_sembrada, mono, "Dra. Revisora", CHECKLIST_OK)
    assert mono.estado == "publicada"

    vista = m.vista_publica(db_sembrada, p)
    assert vista["atc"] == "B01AB05" and vista["revisado_por"] == "Dra. Revisora"
    assert vista["frescura"]["nivel"] == "verde"
    assert vista["nombres_comerciales"] == [{"pais": "US", "marcas": ["Lovenox"], "productos": 2, "genericos": 1}]
    assert "sobredosis" not in [s["tipo"] for s in vista["secciones"]]
    assert vista["pautas"][0]["dosis"] == "40 mg" and vista["pautas"][0]["citas"] == [1]
    periop = next(s for s in vista["secciones"] if s["tipo"] == "perioperatorio")
    assert periop["citas"] == [2, 1]
    assert [r["numero"] for r in vista["referencias"]] == [1, 2]
    assert vista["referencias"][0]["texto"].startswith("Lovenox (enoxaparin sodium) [ficha técnica]. Sanofi; 2026-05-28.")
    assert vista["referencias"][1]["texto"] == "Pérez A. Guía de tromboprofilaxis. Chest. 2024. PMID: 123."


def test_cambio_de_ficha_marca_rojo_y_nuevo_borrador_conserva_revision(db_sembrada):
    p = principio(db_sembrada)
    mono = borrador(db_sembrada)
    ref = secciones(mono)["indicaciones"].referencias[0].id
    revisar_todo(db_sembrada, mono, ref)
    m.publicar(db_sembrada, mono, "Dra. Revisora", CHECKLIST_OK)

    ficha = crear_ficha(db_sembrada, p, spl_id="spl-2", version="2", **{**SECCIONES, "overdosage": "10 OVERDOSAGE protamine"})
    assert m.vista_publica(db_sembrada, p)["frescura"]["nivel"] == "rojo"

    nuevo = m.generar_borrador(db_sembrada, p, ficha)
    assert nuevo.version == 2 and nuevo.estado == "borrador"
    s = secciones(nuevo)
    assert s["indicaciones"].origen == "editor" and s["indicaciones"].contenido == "Texto revisado de indicaciones"
    assert s["sobredosis"].origen == "ficha_importada"  # nueva en la ficha: hay que revisarla
    assert len(nuevo.pautas) == 1 and nuevo.pautas[0].referencias[0].id == ref

    m.editar_seccion(db_sembrada, nuevo, "sobredosis", "Antídoto: protamina.", "es", [ref])
    m.publicar(db_sembrada, nuevo, "Dra. Revisora", CHECKLIST_OK)
    assert mono.estado == "archivada"
    assert m.vista_publica(db_sembrada, p)["version"] == 2


def test_frescura_por_antiguedad(db_sembrada):
    mono = borrador(db_sembrada)
    revisar_todo(db_sembrada, mono, secciones(mono)["indicaciones"].referencias[0].id)
    m.publicar(db_sembrada, mono, "Dra. Revisora", CHECKLIST_OK)
    ahora_ = ahora()
    assert m.frescura(mono, mono.ficha, ahora_ + timedelta(days=200))["nivel"] == "amarillo"
    assert m.frescura(mono, mono.ficha, ahora_ + timedelta(days=400))["nivel"] == "rojo"


def test_avisos_de_formato(db_sembrada):
    mono = borrador(db_sembrada)
    ref = secciones(mono)["indicaciones"].referencias[0].id
    m.editar_seccion(db_sembrada, mono, "indicaciones", "x" * 2000, "en", [ref])
    avisos = m.avisos_formato(mono)
    assert any("2000 caracteres" in a for a in avisos) and any("no está en español" in a for a in avisos)
