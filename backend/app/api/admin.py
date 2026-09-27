from fastapi import APIRouter, BackgroundTasks, Depends, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.connectors.openfda import OpenFDAClient
from app.core.config import Settings, get_settings
from app.core.security import requerir_admin
from app.db import get_db, get_session_factory
from app.models import Sincronizacion
from app.schemas import PeticionSincronizacion, SincronizacionSalida
from app.services import sync_openfda

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


@router.get("/sincronizaciones", response_model=list[SincronizacionSalida])
def listar_sincronizaciones(
    limite: int = Query(default=20, ge=1, le=200), db: Session = Depends(get_db)
) -> list[Sincronizacion]:
    return list(db.scalars(select(Sincronizacion).order_by(Sincronizacion.id.desc()).limit(limite)))
