from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Integer, case, func, select
from sqlalchemy.orm import Session, selectinload

from app.db import get_db
from app.models import PrincipioActivo, ProductoComercial, producto_principio
from app.models import ItemListaEsencial
from app.schemas import (
    DisponibilidadPais,
    ItemListaEsencialSalida,
    ListaPrincipios,
    ListaProductos,
    PrincipioDetalle,
    PrincipioResumen,
)
from app.services.listas_esenciales import resumen as resumen_listas

router = APIRouter(prefix="/principios", tags=["principios activos"])


def _obtener(db: Session, principio_id: int) -> PrincipioActivo:
    principio = db.get(PrincipioActivo, principio_id)
    if principio is None:
        raise HTTPException(404, "Principio activo no encontrado")
    return principio


@router.get("", response_model=ListaPrincipios)
def listar(
    grupo: str | None = None,
    limite: int = Query(default=50, ge=1, le=200),
    desplazamiento: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> ListaPrincipios:
    consulta = select(PrincipioActivo)
    if grupo:
        consulta = consulta.where(PrincipioActivo.grupo == grupo)
    total = db.scalar(select(func.count()).select_from(consulta.subquery()))
    filas = db.scalars(consulta.order_by(PrincipioActivo.dci_es).limit(limite).offset(desplazamiento)).all()
    return ListaPrincipios(total=total or 0, resultados=[PrincipioResumen.model_validate(p) for p in filas])


@router.get("/{principio_id}", response_model=PrincipioDetalle)
def detalle(principio_id: int, db: Session = Depends(get_db)) -> PrincipioDetalle:
    principio = _obtener(db, principio_id)
    filas = db.execute(
        select(
            ProductoComercial.pais,
            func.count(),
            func.sum(case((ProductoComercial.estado == "vigente", 1), else_=0)),
            func.sum(func.coalesce(ProductoComercial.es_generico, False).cast(Integer)),
            func.max(ProductoComercial.fecha_extraccion),
        )
        .join(producto_principio, producto_principio.c.producto_id == ProductoComercial.id)
        .where(producto_principio.c.principio_activo_id == principio_id)
        .group_by(ProductoComercial.pais)
        .order_by(ProductoComercial.pais)
    ).all()
    disponibilidad = [
        DisponibilidadPais(pais=p, productos=n, vigentes=v or 0, genericos=g or 0, ultima_verificacion=f)
        for p, n, v, g, f in filas
    ]
    return PrincipioDetalle(
        **PrincipioResumen.model_validate(principio).model_dump(),
        actualizado_en=principio.actualizado_en,
        disponibilidad=disponibilidad,
        listas_esenciales=resumen_listas(db, principio_id),
    )


@router.get("/{principio_id}/listas-esenciales", response_model=list[ItemListaEsencialSalida])
def listas_esenciales(
    principio_id: int, pais: str | None = Query(default=None, min_length=2, max_length=2),
    incluir_excluidos: bool = False, db: Session = Depends(get_db),
) -> list[ItemListaEsencial]:
    """Entradas de listas nacionales de medicamentos esenciales (LINAME de Bolivia)."""
    _obtener(db, principio_id)
    consulta = select(ItemListaEsencial).where(ItemListaEsencial.principio_activo_id == principio_id)
    if pais:
        consulta = consulta.where(ItemListaEsencial.pais == pais.upper())
    if not incluir_excluidos:
        consulta = consulta.where(ItemListaEsencial.estado == "incluido")
    return list(db.scalars(consulta.order_by(ItemListaEsencial.pais, ItemListaEsencial.codigo)))


@router.get("/{principio_id}/productos", response_model=ListaProductos)
def productos(
    principio_id: int,
    pais: str | None = Query(default=None, min_length=2, max_length=2),
    estado: str | None = Query(default=None, description="vigente | listado_vencido | no_listado"),
    es_generico: bool | None = None,
    limite: int = Query(default=50, ge=1, le=200),
    desplazamiento: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> ListaProductos:
    _obtener(db, principio_id)
    consulta = (
        select(ProductoComercial)
        .join(producto_principio, producto_principio.c.producto_id == ProductoComercial.id)
        .where(producto_principio.c.principio_activo_id == principio_id)
    )
    if pais:
        consulta = consulta.where(ProductoComercial.pais == pais.upper())
    if estado:
        consulta = consulta.where(ProductoComercial.estado == estado)
    if es_generico is not None:
        consulta = consulta.where(ProductoComercial.es_generico == es_generico)
    total = db.scalar(select(func.count()).select_from(consulta.subquery()))
    filas = db.scalars(
        consulta.options(selectinload(ProductoComercial.presentaciones), selectinload(ProductoComercial.fuente))
        .order_by(ProductoComercial.pais, ProductoComercial.nombre_comercial, ProductoComercial.id_externo)
        .limit(limite)
        .offset(desplazamiento)
    ).all()
    return ListaProductos(total=total or 0, resultados=filas)
