from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import PrincipioActivo, ProductoComercial, producto_principio
from app.schemas import PrincipioResumen, RespuestaBusqueda, ResultadoBusqueda
from app.texto import normalizar

router = APIRouter(tags=["búsqueda"])


@router.get("/buscar", response_model=RespuestaBusqueda)
def buscar(
    q: str = Query(min_length=2, max_length=100, description="DCI, nombre comercial o código ATC"),
    pais: str | None = Query(default=None, min_length=2, max_length=2, description="Código ISO del país"),
    limite: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> RespuestaBusqueda:
    consulta = normalizar(q)
    patron = f"%{consulta}%"
    resultados: list[ResultadoBusqueda] = []

    principios = db.scalars(
        select(PrincipioActivo)
        .where(or_(PrincipioActivo.texto_busqueda.like(patron), func.lower(PrincipioActivo.atc).like(f"{consulta}%")))
        .order_by(PrincipioActivo.dci_es)
        .limit(limite)
    ).all()
    # Primero los que empiezan por la consulta.
    principios = sorted(principios, key=lambda p: not normalizar(p.dci_es).startswith(consulta))
    for p in principios:
        coincidencia = "atc" if p.atc.lower().startswith(consulta) else "dci"
        resultados.append(
            ResultadoBusqueda(tipo="principio_activo", coincidencia=coincidencia, principio=PrincipioResumen.model_validate(p))
        )

    restantes = limite - len(resultados)
    if restantes > 0:
        consulta_productos = (
            select(ProductoComercial.nombre_comercial, ProductoComercial.pais, PrincipioActivo)
            .join(producto_principio, producto_principio.c.producto_id == ProductoComercial.id)
            .join(PrincipioActivo, PrincipioActivo.id == producto_principio.c.principio_activo_id)
            .where(ProductoComercial.nombre_busqueda.like(patron), ProductoComercial.estado != "no_listado")
            .distinct()
            .order_by(ProductoComercial.nombre_comercial)
            .limit(restantes)
        )
        if pais:
            consulta_productos = consulta_productos.where(ProductoComercial.pais == pais.upper())
        for nombre, pais_producto, principio in db.execute(consulta_productos).all():
            resultados.append(
                ResultadoBusqueda(
                    tipo="nombre_comercial",
                    coincidencia="nombre_comercial",
                    principio=PrincipioResumen.model_validate(principio),
                    nombre_comercial=nombre,
                    pais=pais_producto,
                )
            )
    return RespuestaBusqueda(consulta=q, resultados=resultados)
