"""Sincronización de registros sanitarios de México (listados de COFEPRIS)."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.connectors.cofepris import CofeprisClient, ConectorCofepris, variantes
from app.models import PrincipioActivo, Sincronizacion
from app.services import sincronizacion


def conector(db: Session, cliente: CofeprisClient) -> ConectorCofepris:
    indice = {p.id: variantes(p.texto_busqueda) for p in db.scalars(select(PrincipioActivo))}
    return ConectorCofepris(cliente, indice)


def sincronizar(db: Session, cliente: CofeprisClient, principio_ids: list[int] | None = None) -> list[Sincronizacion]:
    return sincronizacion.sincronizar(db, conector(db, cliente), principio_ids)
