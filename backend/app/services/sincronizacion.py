"""Sincronización genérica de productos comerciales de un país a partir de un conector.

Un conector expone:
    codigo_fuente, datos_fuente           -> identifican la FuenteDatos
    buscar(principio) -> ResultadoProductos  (productos ya filtrados y normalizados)
    completar(datos) -> datos             (detalle extra; solo para productos nuevos o que cambiaron)
"""

import hashlib
import json
import logging
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.connectors.base import ErrorConector, ResultadoProductos
from app.models import FuenteDatos, Presentacion, PrincipioActivo, ProductoComercial, Sincronizacion, ahora
from app.texto import normalizar

log = logging.getLogger(__name__)


class Conector(Protocol):
    codigo_fuente: str
    datos_fuente: dict

    def buscar(self, principio: PrincipioActivo) -> ResultadoProductos: ...

    def completar(self, datos: dict) -> dict: ...


def obtener_fuente(db: Session, codigo: str, datos: dict) -> FuenteDatos:
    fuente = db.scalar(select(FuenteDatos).where(FuenteDatos.codigo == codigo))
    if fuente is None:
        fuente = FuenteDatos(codigo=codigo, **datos)
        db.add(fuente)
        db.flush()
    return fuente


def _hash(datos: dict) -> str:
    return hashlib.sha256(json.dumps(datos, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def sincronizar_principio(db: Session, conector: Conector, principio: PrincipioActivo) -> Sincronizacion:
    fuente = obtener_fuente(db, conector.codigo_fuente, conector.datos_fuente)
    sync = Sincronizacion(fuente=fuente, principio_activo=principio, estado="en_curso")
    db.add(sync)
    db.commit()

    momento = ahora()
    try:
        resultado = conector.buscar(principio)
        sync.consulta = resultado.consulta
        sync.n_recibidos = len(resultado.recibidos)
        vistos: set[int] = set()
        for datos in resultado.productos:
            producto, cambio = _guardar_producto(db, conector, fuente, datos, momento)
            if principio not in producto.principios:
                producto.principios.append(principio)
            vistos.add(producto.id)
            if cambio == "creado":
                sync.n_creados += 1
            elif cambio == "actualizado":
                sync.n_actualizados += 1
            else:
                sync.n_sin_cambios += 1
    except ErrorConector as e:
        db.rollback()
        log.warning("%s falló para %s: %s", fuente.codigo, principio.dci_es, e)
        sync.estado, sync.mensaje, sync.finalizada_en = "error", str(e), ahora()
        db.commit()
        return sync

    if resultado.truncado:
        sync.estado = "truncada"
        sync.mensaje = f"Se procesaron {len(resultado.recibidos)} de {resultado.total} resultados"
    else:
        # Solo con una descarga completa se puede afirmar que un producto dejó de estar listado.
        sync.n_no_listados, desvinculados = _conciliar_ausentes(
            db, fuente, principio, vistos, resultado.recibidos, momento
        )
        if desvinculados:
            sync.mensaje = f"{desvinculados} productos desvinculados por no cumplir los criterios"
        sync.estado = "ok"

    fuente.ultima_sincronizacion = momento
    sync.finalizada_en = ahora()
    db.commit()
    return sync


def _guardar_producto(
    db: Session, conector: Conector, fuente: FuenteDatos, datos: dict, momento
) -> tuple[ProductoComercial, str]:
    h = _hash(datos)
    producto = db.scalar(
        select(ProductoComercial).where(
            ProductoComercial.fuente_id == fuente.id, ProductoComercial.id_externo == datos["id_externo"]
        )
    )
    # El estado se compara aparte: "no_listado" se asigna fuera del hash y debe revertirse si reaparece.
    if producto is not None and producto.hash_contenido == h and producto.estado == datos["estado"]:
        producto.fecha_extraccion = momento
        return producto, "sin_cambios"

    completos = conector.completar(datos)
    cambio = "actualizado"
    if producto is None:
        cambio = "creado"
        producto = ProductoComercial(fuente=fuente, pais=fuente.pais, id_externo=datos["id_externo"])
        db.add(producto)

    for k, v in completos.items():
        if k != "presentaciones":
            setattr(producto, k, v)
    producto.nombre_busqueda = normalizar(completos["nombre_comercial"])
    if "presentaciones" in completos:
        producto.presentaciones = [Presentacion(**p) for p in completos["presentaciones"]]
    producto.hash_contenido = h
    producto.fecha_extraccion = momento
    producto.fecha_actualizacion = momento
    db.flush()
    return producto, cambio


def _conciliar_ausentes(
    db: Session, fuente: FuenteDatos, principio: PrincipioActivo, vistos: set[int], recibidos: set[str], momento
) -> tuple[int, int]:
    """Trata los productos vinculados que no se asociaron en esta descarga.

    - Si la fuente los devolvió pero ya no cumplen los criterios (p. ej. un filtro nuevo),
      se desvinculan del principio activo: siguen listados, pero no le corresponden.
    - Si la fuente ya no los devuelve, se marcan como "no_listado" (no se borran).
    Devuelve (no_listados, desvinculados).
    """
    no_listados = desvinculados = 0
    for producto in list(principio.productos):
        if producto.fuente_id != fuente.id or producto.id in vistos:
            continue
        if producto.id_externo in recibidos:
            principio.productos.remove(producto)
            desvinculados += 1
        elif producto.estado != "no_listado":
            producto.estado = "no_listado"
            producto.fecha_actualizacion = momento
            producto.fecha_extraccion = momento
            no_listados += 1
    return no_listados, desvinculados


def sincronizar(db: Session, conector: Conector, principio_ids: list[int] | None = None) -> list[Sincronizacion]:
    consulta = select(PrincipioActivo).order_by(PrincipioActivo.id)
    if principio_ids:
        consulta = consulta.where(PrincipioActivo.id.in_(principio_ids))
    return [sincronizar_principio(db, conector, p) for p in db.scalars(consulta).all()]
