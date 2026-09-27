"""Sincronización de productos de EE. UU. (openFDA NDC Directory)."""

from sqlalchemy.orm import Session

from app.connectors.openfda import ConectorOpenFDA, OpenFDAClient
from app.models import FuenteDatos, PrincipioActivo, Sincronizacion
from app.services import sincronizacion

CODIGO_FUENTE = ConectorOpenFDA.codigo_fuente


def obtener_fuente(db: Session) -> FuenteDatos:
    return sincronizacion.obtener_fuente(db, ConectorOpenFDA.codigo_fuente, ConectorOpenFDA.datos_fuente)


def sincronizar_principio(db: Session, cliente: OpenFDAClient, principio: PrincipioActivo) -> Sincronizacion:
    return sincronizacion.sincronizar_principio(db, ConectorOpenFDA(cliente), principio)


def sincronizar(db: Session, cliente: OpenFDAClient, principio_ids: list[int] | None = None) -> list[Sincronizacion]:
    return sincronizacion.sincronizar(db, ConectorOpenFDA(cliente), principio_ids)
