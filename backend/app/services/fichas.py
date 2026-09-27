"""Importación de la ficha técnica de referencia (DailyMed, vía openFDA /drug/label)."""

import hashlib
import json
import logging
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.connectors.base import ErrorConector
from app.connectors.openfda import MAX_SET_IDS_POR_CONSULTA, OpenFDAClient, normalizar_ficha
from app.models import FichaTecnica, FuenteDatos, PrincipioActivo, ProductoComercial, SeccionFicha, Sincronizacion, ahora
from app.services.sync_openfda import obtener_fuente as obtener_fuente_ndc

log = logging.getLogger(__name__)

CODIGO_FUENTE = "openfda_label"
RECETA = "HUMAN PRESCRIPTION DRUG"
MARCA = {"NDA", "BLA"}
MAX_LOTES = 3
# Vías de acción local: sus fichas no sirven como referencia general del principio activo.
VIAS_LOCALES = {
    "TOPICAL", "OPHTHALMIC", "INTRAVITREAL", "INTRAOCULAR", "CONJUNCTIVAL", "RECTAL", "NASAL",
    "TRANSDERMAL", "AURICULAR (OTIC)", "VAGINAL", "CUTANEOUS", "DENTAL", "PERIODONTAL", "URETHRAL",
    "INTRAVESICAL", "INTRA-ARTICULAR", "INTRALESIONAL", "SOFT TISSUE", "OROPHARYNGEAL", "BUCCAL",
    "INTRACANALICULAR", "INTRACAMERAL", "SUBCONJUNCTIVAL", "RETROBULBAR", "INTRATYMPANIC", "IRRIGATION",
    "EPIDURAL", "INTRATHECAL", "INTRASPINAL", "SUBARACHNOID",
}


def obtener_fuente(db: Session) -> FuenteDatos:
    fuente = db.scalar(select(FuenteDatos).where(FuenteDatos.codigo == CODIGO_FUENTE))
    if fuente is None:
        fuente = FuenteDatos(
            codigo=CODIGO_FUENTE,
            nombre="DailyMed (NLM) — fichas técnicas SPL vía openFDA",
            agencia="FDA / NLM",
            pais="US",
            url="https://dailymed.nlm.nih.gov/",
            licencia="Dominio público, ver https://open.fda.gov/license/",
        )
        db.add(fuente)
        db.flush()
    return fuente


def _vias(texto: str | None) -> set[str]:
    return {v.strip().upper() for v in (texto or "").replace("|", ",").split(",") if v.strip()}


def candidatos_ordenados(db: Session, principio: PrincipioActivo) -> list[str]:
    """set_id de los productos de EE. UU., del más al menos adecuado como ficha de referencia.

    Orden de preferencia:
    1. Vía: las preferidas del principio activo o, si no hay, cualquier vía sistémica
       (se relegan tópica, oftálmica, rectal, nasal...).
    2. Con receta y de marca (NDA/BLA) > de marca > con receta > resto.
    3. El comercializado primero (el medicamento original, habitualmente el de referencia)
       antes que reformulaciones posteriores.
    """
    fuente_ndc = obtener_fuente_ndc(db)
    productos = db.scalars(
        select(ProductoComercial)
        .options(selectinload(ProductoComercial.presentaciones))
        .where(
            ProductoComercial.id.in_([p.id for p in principio.productos]),
            ProductoComercial.fuente_id == fuente_ndc.id,
            ProductoComercial.estado == "vigente",
            ProductoComercial.spl_set_id.is_not(None),
        )
    ).all()
    preferidas = _vias(principio.vias_openfda)

    def clave(p: ProductoComercial) -> tuple:
        vias = _vias(p.via)
        via_ok = bool(vias & preferidas) if preferidas else bool(vias - VIAS_LOCALES) or not vias
        marca = p.categoria_registro in MARCA
        receta = p.tipo_producto == RECETA
        nivel = 0 if receta and marca else 1 if marca else 2 if receta else 3
        fechas = [x.fecha_inicio_comercializacion for x in p.presentaciones if x.fecha_inicio_comercializacion]
        return (not via_ok, nivel, min(fechas) if fechas else "9999", p.spl_set_id)

    return list(dict.fromkeys(p.spl_set_id for p in sorted(productos, key=clave)))


def _hash(datos: dict) -> str:
    return hashlib.sha256(json.dumps(datos, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def ficha_existente(db: Session, principio: PrincipioActivo, fuente: FuenteDatos) -> FichaTecnica | None:
    return db.scalar(
        select(FichaTecnica).where(FichaTecnica.principio_activo_id == principio.id, FichaTecnica.fuente_id == fuente.id)
    )


def guardar_ficha(
    db: Session, principio: PrincipioActivo, fuente: FuenteDatos, idioma: str, datos: dict
) -> tuple[FichaTecnica, str]:
    """Crea o actualiza la ficha del principio activo en esa fuente. Devuelve (ficha, resultado)."""
    h = _hash(datos)
    momento = ahora()
    ficha = ficha_existente(db, principio, fuente)
    if ficha is not None and ficha.hash_contenido == h:
        ficha.fecha_extraccion = momento
        db.commit()
        return ficha, "sin_cambios"

    resultado = "actualizada"
    if ficha is None:
        resultado = "creada"
        ficha = FichaTecnica(principio_activo=principio, fuente=fuente, pais=fuente.pais, idioma=idioma)
        db.add(ficha)
    for k, v in datos.items():
        if k != "secciones":
            setattr(ficha, k, v)
    ficha.secciones = [SeccionFicha(**s) for s in datos["secciones"]]
    ficha.hash_contenido = h
    ficha.fecha_extraccion = momento
    ficha.fecha_actualizacion = momento
    db.commit()
    return ficha, resultado


def importar_ficha(db: Session, cliente: OpenFDAClient, principio: PrincipioActivo) -> tuple[FichaTecnica | None, str]:
    """Descarga y guarda la ficha de referencia de EE. UU. Devuelve (ficha, resultado).

    resultado: creada | actualizada | sin_cambios | sin_ficha
    Lanza OpenFDAError si la API falla.
    """
    # La ficha fijada a mano va primero; si ya no existe en DailyMed se usa la selección automática.
    candidatos = candidatos_ordenados(db, principio)[: MAX_LOTES * MAX_SET_IDS_POR_CONSULTA]
    if principio.ficha_set_id_openfda:
        candidatos = [principio.ficha_set_id_openfda] + [c for c in candidatos if c != principio.ficha_set_id_openfda]

    bruto = None
    for inicio in range(0, len(candidatos), MAX_SET_IDS_POR_CONSULTA):
        lote = candidatos[inicio : inicio + MAX_SET_IDS_POR_CONSULTA]
        encontradas = cliente.buscar_fichas(lote)
        bruto = next((encontradas[s] for s in lote if s in encontradas), None)
        if bruto:
            break
    if not bruto:
        return None, "sin_ficha"
    return guardar_ficha(db, principio, obtener_fuente(db), "en", normalizar_ficha(bruto))


def importar_lote(
    db: Session,
    fuente: FuenteDatos,
    importar: Callable[[Session, PrincipioActivo], tuple[FichaTecnica | None, str]],
    principio_ids: list[int] | None = None,
) -> list[Sincronizacion]:
    """Importa fichas con `importar`, actualiza los borradores de monografía y registra cada ejecución."""
    from app.services.monografias import generar_borrador

    consulta = select(PrincipioActivo).order_by(PrincipioActivo.id)
    if principio_ids:
        consulta = consulta.where(PrincipioActivo.id.in_(principio_ids))
    salida = []
    for principio in db.scalars(consulta).all():
        sync = Sincronizacion(fuente=fuente, principio_activo=principio, estado="en_curso")
        db.add(sync)
        db.commit()
        try:
            ficha, resultado = importar(db, principio)
        except ErrorConector as e:
            db.rollback()
            log.warning("Ficha de %s (%s): %s", principio.dci_es, fuente.codigo, e)
            sync.estado, sync.mensaje = "error", str(e)
        else:
            generar_borrador(db, principio)
            sync.mensaje = f"{resultado}: {ficha.titulo} v{ficha.version} ({ficha.fecha_efectiva})" if ficha else resultado
            sync.estado = "ok"
            sync.n_creados = int(resultado == "creada")
            sync.n_actualizados = int(resultado == "actualizada")
            sync.n_sin_cambios = int(resultado == "sin_cambios")
        sync.finalizada_en = ahora()
        db.commit()
        salida.append(sync)
    return salida


def importar_fichas(db: Session, cliente: OpenFDAClient, principio_ids: list[int] | None = None) -> list[Sincronizacion]:
    """Fichas técnicas de EE. UU. (DailyMed)."""
    return importar_lote(db, obtener_fuente(db), lambda db_, p: importar_ficha(db_, cliente, p), principio_ids)
