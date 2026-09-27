from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, sessionmaker

from app.connectors.openfda import OpenFDAClient
from app.core.config import Settings, get_settings
from app.core.security import requerir_admin
from app.db import get_db, get_session_factory
from app.models import Monografia, PrincipioActivo, Referencia, Sincronizacion
from app.schemas import (
    EdicionSeccion,
    MonografiaBorrador,
    PautaBorrador,
    PautaEntrada,
    PeticionPublicacion,
    PeticionSincronizacion,
    ReferenciaEntrada,
    ReferenciaSalida,
    SeccionBorrador,
    SincronizacionSalida,
)
from app.services import fichas, monografias, sync_openfda

router = APIRouter(prefix="/admin", tags=["administración"], dependencies=[Depends(requerir_admin)])


def get_openfda_client(settings: Settings = Depends(get_settings)) -> OpenFDAClient:
    return OpenFDAClient(
        api_key=settings.openfda_api_key,
        max_resultados=settings.openfda_max_resultados,
        pausa_segundos=settings.openfda_pausa_segundos,
    )


def _ejecutar_sync(fabrica: sessionmaker[Session], cliente: OpenFDAClient, principio_ids: list[int] | None) -> None:
    with fabrica() as db:
        sync_openfda.sincronizar(db, cliente, principio_ids)


@router.post("/sincronizaciones/openfda", status_code=status.HTTP_202_ACCEPTED)
def sincronizar_openfda(
    peticion: PeticionSincronizacion,
    tareas: BackgroundTasks,
    fabrica: sessionmaker[Session] = Depends(get_session_factory),
    cliente: OpenFDAClient = Depends(get_openfda_client),
) -> dict:
    tareas.add_task(_ejecutar_sync, fabrica, cliente, peticion.principio_ids)
    return {"mensaje": "Sincronización con openFDA iniciada", "principio_ids": peticion.principio_ids}


def _ejecutar_fichas(fabrica: sessionmaker[Session], cliente: OpenFDAClient, principio_ids: list[int] | None) -> None:
    with fabrica() as db:
        fichas.importar_fichas(db, cliente, principio_ids)


@router.post("/sincronizaciones/fichas-openfda", status_code=status.HTTP_202_ACCEPTED)
def sincronizar_fichas(
    peticion: PeticionSincronizacion,
    tareas: BackgroundTasks,
    fabrica: sessionmaker[Session] = Depends(get_session_factory),
    cliente: OpenFDAClient = Depends(get_openfda_client),
) -> dict:
    """Importa las fichas técnicas de DailyMed y actualiza los borradores de monografía."""
    tareas.add_task(_ejecutar_fichas, fabrica, cliente, peticion.principio_ids)
    return {"mensaje": "Importación de fichas técnicas iniciada", "principio_ids": peticion.principio_ids}


@router.get("/sincronizaciones", response_model=list[SincronizacionSalida])
def listar_sincronizaciones(
    limite: int = Query(default=20, ge=1, le=200), db: Session = Depends(get_db)
) -> list[Sincronizacion]:
    return list(db.scalars(select(Sincronizacion).order_by(Sincronizacion.id.desc()).limit(limite)))


# --- Monografías -----------------------------------------------------------------

def _vista_borrador(db: Session, m: Monografia) -> MonografiaBorrador:
    ficha = m.ficha
    errores = monografias.errores_publicacion(m, {k: True for k in monografias.CHECKLIST})
    return MonografiaBorrador(
        id=m.id,
        principio_activo_id=m.principio_activo_id,
        version=m.version,
        estado=m.estado,
        ficha_spl_id_base=m.ficha_spl_id_base,
        ficha_cambiada=bool(ficha and ficha.spl_id != m.ficha_spl_id_base),
        actualizada_en=m.actualizada_en,
        publicable=not errores,
        errores_publicacion=errores,
        avisos_formato=monografias.avisos_formato(m),
        checklist=monografias.CHECKLIST,
        secciones=[
            SeccionBorrador(
                tipo=s.tipo,
                titulo=monografias.TIPOS[s.tipo].titulo,
                obligatoria=monografias.TIPOS[s.tipo].obligatoria,
                origen=s.origen,
                idioma=s.idioma,
                contenido=s.contenido,
                fecha_verificacion=s.fecha_verificacion,
                referencia_ids=[r.id for r in s.referencias],
            )
            for s in m.secciones
        ],
        pautas=[
            PautaBorrador(**monografias.datos_pauta(p), referencia_ids=[r.id for r in p.referencias])
            for p in m.pautas
        ],
    )


def _monografia(db: Session, monografia_id: int) -> Monografia:
    m = db.get(Monografia, monografia_id)
    if m is None:
        raise HTTPException(404, "Monografía no encontrada")
    return m


@router.get("/principios/{principio_id}/borrador", response_model=MonografiaBorrador)
def ver_borrador(principio_id: int, db: Session = Depends(get_db)) -> MonografiaBorrador:
    if db.get(PrincipioActivo, principio_id) is None:
        raise HTTPException(404, "Principio activo no encontrado")
    m = monografias.monografia_en_estado(db, principio_id, "borrador")
    if m is None:
        raise HTTPException(404, "No hay borrador; importa primero la ficha técnica")
    return _vista_borrador(db, m)


@router.put("/monografias/{monografia_id}/secciones/{tipo}", response_model=MonografiaBorrador)
def editar_seccion(monografia_id: int, tipo: str, edicion: EdicionSeccion, db: Session = Depends(get_db)) -> MonografiaBorrador:
    m = _monografia(db, monografia_id)
    try:
        monografias.editar_seccion(db, m, tipo, edicion.contenido, edicion.idioma, edicion.referencia_ids)
    except monografias.ErrorEditorial as e:
        raise HTTPException(422, {"errores": e.errores}) from e
    return _vista_borrador(db, m)


@router.put("/monografias/{monografia_id}/pautas", response_model=MonografiaBorrador)
def reemplazar_pautas(monografia_id: int, pautas: list[PautaEntrada], db: Session = Depends(get_db)) -> MonografiaBorrador:
    """Sustituye la tabla de posología (pautas estructuradas) del borrador."""
    m = _monografia(db, monografia_id)
    try:
        monografias.reemplazar_pautas(
            db, m, [(p.model_dump(exclude={"referencia_ids"}), p.referencia_ids) for p in pautas]
        )
    except monografias.ErrorEditorial as e:
        raise HTTPException(422, {"errores": e.errores}) from e
    return _vista_borrador(db, m)


@router.post("/monografias/{monografia_id}/publicar")
def publicar(monografia_id: int, peticion: PeticionPublicacion, db: Session = Depends(get_db)) -> dict:
    m = _monografia(db, monografia_id)
    try:
        monografias.publicar(db, m, peticion.revisor, peticion.checklist)
    except monografias.ErrorEditorial as e:
        raise HTTPException(422, {"errores": e.errores}) from e
    return {"mensaje": "Monografía publicada", "version": m.version, "publicada_en": m.publicada_en}


# --- Referencias -----------------------------------------------------------------

def _salida_referencia(r: Referencia) -> ReferenciaSalida:
    return ReferenciaSalida.model_validate(r).model_copy(update={"texto": monografias.formato_vancouver(r)})


@router.post("/referencias", response_model=ReferenciaSalida, status_code=status.HTTP_201_CREATED)
def crear_referencia(entrada: ReferenciaEntrada, db: Session = Depends(get_db)) -> ReferenciaSalida:
    clave = f"pmid:{entrada.pmid}" if entrada.pmid else f"doi:{entrada.doi.lower()}" if entrada.doi else None
    if clave and (existente := db.scalar(select(Referencia).where(Referencia.clave == clave))):
        raise HTTPException(409, {"mensaje": "La referencia ya existe", "id": existente.id})
    ref = Referencia(**entrada.model_dump(), clave=clave, fecha_acceso=monografias.ahora().date().isoformat())
    db.add(ref)
    db.commit()
    return _salida_referencia(ref)


@router.get("/referencias", response_model=list[ReferenciaSalida])
def listar_referencias(
    q: str | None = Query(default=None, min_length=2), limite: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> list[ReferenciaSalida]:
    consulta = select(Referencia).order_by(Referencia.id.desc()).limit(limite)
    if q:
        patron = f"%{q}%"
        consulta = consulta.where(or_(Referencia.titulo.ilike(patron), Referencia.autores.ilike(patron),
                                      Referencia.pmid == q, Referencia.doi.ilike(patron)))
    return [_salida_referencia(r) for r in db.scalars(consulta)]
