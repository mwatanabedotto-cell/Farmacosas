"""Sincronización de medicamentos de Argentina (listado de PAMI)."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.connectors.nombres import variantes
from app.connectors.pami import ConectorPami, PamiClient
from app.models import PrincipioActivo, Sincronizacion
from app.services import sincronizacion


def conector(db: Session, cliente: PamiClient) -> ConectorPami:
    principios = db.scalars(select(PrincipioActivo)).all()
    indice = {p.id: variantes(p.texto_busqueda) for p in principios}
    # El filtro de nombre comercial desempata denominaciones genéricas ambiguas («insulina humana»).
    filtros = {p.id: p.filtro_nombre_openfda for p in principios}
    return ConectorPami(cliente, indice, filtros)


def sincronizar(db: Session, cliente: PamiClient, principio_ids: list[int] | None = None) -> list[Sincronizacion]:
    return sincronizacion.sincronizar(db, conector(db, cliente), principio_ids)
