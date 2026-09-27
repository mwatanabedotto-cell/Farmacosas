from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.connectors.openfda import SECCIONES_FICHA, secciones_sin_duplicar
from app.db import get_db
from app.core.avisos import AVISO, AVISO_FICHA
from app.models import FichaTecnica, FuenteDatos, PrincipioActivo
from app.schemas import FichaBase, FichaSalida, MonografiaPublica, SeccionFichaSalida
from app.services.monografias import vista_publica

router = APIRouter(prefix="/principios", tags=["monografías y fichas técnicas"])

def _principio(db: Session, principio_id: int) -> PrincipioActivo:
    principio = db.get(PrincipioActivo, principio_id)
    if principio is None:
        raise HTTPException(404, "Principio activo no encontrado")
    return principio


@router.get("/{principio_id}/monografia", response_model=MonografiaPublica)
def monografia(principio_id: int, db: Session = Depends(get_db)) -> dict:
    vista = vista_publica(db, _principio(db, principio_id))
    if vista is None:
        raise HTTPException(404, "Este principio activo aún no tiene monografía publicada")
    return {**vista, "aviso": AVISO}


@router.get("/{principio_id}/fichas-tecnicas", response_model=list[FichaSalida])
def fichas_tecnicas(principio_id: int, pais: str | None = None, db: Session = Depends(get_db)) -> list[FichaSalida]:
    _principio(db, principio_id)
    consulta = select(FichaTecnica).join(FuenteDatos).where(FichaTecnica.principio_activo_id == principio_id)
    if pais:
        consulta = consulta.where(FichaTecnica.pais == pais.upper())
    return [
        FichaSalida(
            **FichaBase.model_validate(ficha).model_dump(),
            aviso=AVISO_FICHA,
            secciones=_secciones(ficha),
        )
        for ficha in db.scalars(consulta.order_by(FichaTecnica.pais))
    ]


def _secciones(ficha: FichaTecnica) -> list[SeccionFichaSalida]:
    textos = {s.codigo: s.texto for s in ficha.secciones}
    return [
        SeccionFichaSalida(codigo=c, titulo=SECCIONES_FICHA.get(c, c), texto=textos[c])
        for c in secciones_sin_duplicar(textos)
    ]
