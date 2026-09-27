import hashlib
import json
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.connectors.openfda import (
    OpenFDAClient,
    OpenFDAError,
    construir_busqueda,
    es_excluido,
    fusionar_duplicados,
    normalizar_producto,
    parsear_terminos,
    producto_coincide,
)
from app.models import FuenteDatos, Presentacion, PrincipioActivo, ProductoComercial, Sincronizacion, ahora
from app.texto import normalizar

log = logging.getLogger(__name__)

CODIGO_FUENTE = "openfda_ndc"


def obtener_fuente(db: Session) -> FuenteDatos:
    fuente = db.scalar(select(FuenteDatos).where(FuenteDatos.codigo == CODIGO_FUENTE))
    if fuente is None:
        fuente = FuenteDatos(
            codigo=CODIGO_FUENTE,
            nombre="openFDA — NDC Directory",
            agencia="FDA",
            pais="US",
            url="https://open.fda.gov/apis/drug/ndc/",
            licencia="Dominio público (CC0), ver https://open.fda.gov/license/",
        )
        db.add(fuente)
        db.flush()
    return fuente


def _hash(datos: dict) -> str:
    return hashlib.sha256(json.dumps(datos, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def sincronizar_principio(db: Session, cliente: OpenFDAClient, principio: PrincipioActivo) -> Sincronizacion:
    """Actualiza los productos de EE. UU. de un principio activo desde el NDC Directory."""
    fuente = obtener_fuente(db)
    componentes = parsear_terminos(principio.terminos_openfda)
    busqueda = construir_busqueda(componentes) if componentes else None
    sync = Sincronizacion(fuente=fuente, principio_activo=principio, estado="en_curso", consulta=busqueda)
    db.add(sync)
    db.commit()

    if not busqueda:
        sync.estado, sync.mensaje, sync.finalizada_en = "error", "El principio activo no tiene términos de openFDA", ahora()
        db.commit()
        return sync

    try:
        resultado = cliente.buscar_ndc(busqueda)
    except OpenFDAError as e:
        log.warning("openFDA falló para %s: %s", principio.dci_es, e)
        sync.estado, sync.mensaje, sync.finalizada_en = "error", str(e), ahora()
        db.commit()
        return sync

    momento = ahora()
    sync.n_recibidos = len(resultado.resultados)
    vistos: set[int] = set()
    for bruto in fusionar_duplicados(resultado.resultados):
        ingredientes = [i.get("name", "") for i in bruto.get("active_ingredients") or []]
        if es_excluido(bruto) or not bruto.get("product_ndc") or not producto_coincide(ingredientes, componentes):
            continue
        datos = normalizar_producto(bruto)
        producto, cambio = _guardar_producto(db, fuente, datos, momento)
        if principio not in producto.principios:
            producto.principios.append(principio)
        vistos.add(producto.id)
        if cambio == "creado":
            sync.n_creados += 1
        elif cambio == "actualizado":
            sync.n_actualizados += 1
        else:
            sync.n_sin_cambios += 1

    if resultado.truncado:
        sync.estado = "truncada"
        sync.mensaje = f"Se procesaron {len(resultado.resultados)} de {resultado.total} resultados"
    else:
        # Solo con una descarga completa se puede afirmar que un producto dejó de estar listado.
        sync.n_no_listados = _marcar_no_listados(db, fuente, principio, vistos, momento)
        sync.estado = "ok"

    fuente.ultima_sincronizacion = momento
    sync.finalizada_en = ahora()
    db.commit()
    return sync


def _guardar_producto(db: Session, fuente: FuenteDatos, datos: dict, momento) -> tuple[ProductoComercial, str]:
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

    cambio = "actualizado"
    if producto is None:
        cambio = "creado"
        producto = ProductoComercial(fuente=fuente, pais=fuente.pais, id_externo=datos["id_externo"])
        db.add(producto)

    campos = {k: v for k, v in datos.items() if k != "presentaciones"}
    for k, v in campos.items():
        setattr(producto, k, v)
    producto.nombre_busqueda = normalizar(datos["nombre_comercial"])
    producto.presentaciones = [Presentacion(**p) for p in datos["presentaciones"]]
    producto.hash_contenido = h
    producto.fecha_extraccion = momento
    producto.fecha_actualizacion = momento
    db.flush()
    return producto, cambio


def _marcar_no_listados(db: Session, fuente: FuenteDatos, principio: PrincipioActivo, vistos: set[int], momento) -> int:
    n = 0
    for producto in principio.productos:
        if producto.fuente_id == fuente.id and producto.id not in vistos and producto.estado != "no_listado":
            producto.estado = "no_listado"
            producto.fecha_actualizacion = momento
            producto.fecha_extraccion = momento
            n += 1
    return n


def sincronizar(db: Session, cliente: OpenFDAClient, principio_ids: list[int] | None = None) -> list[Sincronizacion]:
    consulta = select(PrincipioActivo).order_by(PrincipioActivo.id)
    if principio_ids:
        consulta = consulta.where(PrincipioActivo.id.in_(principio_ids))
    return [sincronizar_principio(db, cliente, p) for p in db.scalars(consulta).all()]
