"""Listas nacionales de medicamentos esenciales (hoy: LINAME de Bolivia)."""

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.connectors.agemed import URL_PAGINA, AgemedClient
from app.connectors.base import ErrorConector
from app.connectors.cima import codigos_atc
from app.models import ItemListaEsencial, PrincipioActivo, Sincronizacion, ahora
from app.services import sincronizacion
from app.texto import normalizar

log = logging.getLogger(__name__)

CODIGO_FUENTE = "agemed_liname"
DATOS_FUENTE = {
    "nombre": "AGEMED — Lista Nacional de Medicamentos Esenciales (LINAME)",
    "agencia": "AGEMED / Ministerio de Salud y Deportes",
    "pais": "BO",
    "url": URL_PAGINA,
    "licencia": "Documento público oficial (aprobado por Resolución Ministerial)",
}
CAMPOS = ("medicamento", "atc", "uso_restringido", "aware", "lista")


def asignar_principios(db: Session) -> dict:
    """Índices para asignar entradas a principios activos: por ATC y por nombre exacto."""
    por_atc, por_nombre, grupos = {}, {}, {}
    for p in db.scalars(select(PrincipioActivo)):
        codigos = codigos_atc(p)
        for codigo in codigos:
            por_atc.setdefault(codigo, p.id)
        grupos[p.id] = {c[:3] for c in codigos}
        for nombre in (p.texto_busqueda or "").split("|"):
            if nombre.strip():
                por_nombre.setdefault(normalizar(nombre), p.id)
    return {"atc": por_atc, "nombre": por_nombre, "grupos": grupos}


def principio_de(entrada: dict, indices: dict) -> int | None:
    """Asigna una entrada de la lista a un principio activo.

    1. Por código ATC: identifica también el uso (aciclovir crema D06BB03 no es aciclovir sistémico J05AB01).
    2. Si el ATC no coincide (la lista trae errores, p. ej. cefazolina como J01DE04) o está incompleto
       (``B01AB**``): por nombre idéntico al DCI o a un sinónimo, y solo si el grupo terapéutico
       (tres primeros caracteres del ATC) es el mismo, para no confundir formas tópicas con sistémicas.
    """
    atc = entrada.get("atc") or ""
    if atc in indices["atc"]:
        return indices["atc"][atc]
    principio_id = indices["nombre"].get(normalizar(entrada["medicamento"]))
    if principio_id is not None and (len(atc) < 3 or atc[:3] in indices["grupos"].get(principio_id, set())):
        return principio_id
    return None


def sincronizar_liname(db: Session, cliente: AgemedClient) -> Sincronizacion:
    fuente = sincronizacion.obtener_fuente(db, CODIGO_FUENTE, DATOS_FUENTE)
    sync = Sincronizacion(fuente=fuente, estado="en_curso")
    db.add(sync)
    db.commit()
    try:
        url, lista, entradas = cliente.liname()
    except ErrorConector as e:
        db.rollback()
        log.warning("LINAME: %s", e)
        sync.estado, sync.mensaje, sync.finalizada_en = "error", str(e), ahora()
        db.commit()
        return sync

    momento = ahora()
    indices = asignar_principios(db)
    existentes = {
        (i.codigo, i.forma_farmaceutica, i.concentracion): i
        for i in db.scalars(select(ItemListaEsencial).where(ItemListaEsencial.fuente_id == fuente.id))
    }
    vistos = set()
    asignadas = 0
    for entrada in entradas:
        clave = (entrada["codigo"], entrada["forma_farmaceutica"], entrada["concentracion"])
        if clave in vistos:
            continue
        vistos.add(clave)
        datos = {**entrada, "lista": lista}
        principio_id = principio_de(entrada, indices)
        asignadas += principio_id is not None
        item = existentes.get(clave)
        if item is None:
            item = ItemListaEsencial(fuente=fuente, pais=fuente.pais, fecha_actualizacion=momento, **datos)
            db.add(item)
            sync.n_creados += 1
        elif any(getattr(item, c) != datos[c] for c in CAMPOS) or item.estado != "incluido" \
                or item.principio_activo_id != principio_id:
            for c in CAMPOS:
                setattr(item, c, datos[c])
            item.fecha_actualizacion = momento
            sync.n_actualizados += 1
        else:
            sync.n_sin_cambios += 1
        item.principio_activo_id = principio_id
        item.estado = "incluido"
        item.url_fuente = url
        item.fecha_extraccion = momento

    for clave, item in existentes.items():
        if clave not in vistos and item.estado != "excluido":
            item.estado = "excluido"
            item.fecha_actualizacion = item.fecha_extraccion = momento
            sync.n_no_listados += 1

    sync.n_recibidos = len(vistos)
    sync.consulta = url
    sync.mensaje = f"{lista}: {len(vistos)} entradas, {asignadas} asignadas a principios activos"
    sync.estado = "ok"
    fuente.ultima_sincronizacion = momento
    sync.finalizada_en = ahora()
    db.commit()
    return sync


def resumen(db: Session, principio_id: int) -> list[dict]:
    """Por país y lista: presentaciones incluidas, uso restringido, AWaRe y fecha de verificación."""
    items = db.scalars(
        select(ItemListaEsencial)
        .where(ItemListaEsencial.principio_activo_id == principio_id, ItemListaEsencial.estado == "incluido")
        .order_by(ItemListaEsencial.pais, ItemListaEsencial.codigo)
    ).all()
    principio = db.get(PrincipioActivo, principio_id)
    dci = normalizar(principio.dci_es) if principio else ""
    salida: dict[tuple, dict] = {}
    for i in items:
        r = salida.setdefault((i.pais, i.lista), {
            "pais": i.pais, "lista": i.lista, "presentaciones": [], "uso_restringido": False, "aware": None,
            "url_fuente": i.url_fuente, "ultima_verificacion": i.fecha_extraccion,
        })
        detalles = [i.medicamento] if normalizar(i.medicamento) != dci else []
        detalles += ["uso restringido"] if i.uso_restringido else []
        texto = f"{i.forma_farmaceutica} {i.concentracion}".strip() + (f" ({'; '.join(detalles)})" if detalles else "")
        if texto not in r["presentaciones"]:
            r["presentaciones"].append(texto)
        r["uso_restringido"] = r["uso_restringido"] or i.uso_restringido
        r["aware"] = r["aware"] or i.aware
        r["ultima_verificacion"] = max(r["ultima_verificacion"], i.fecha_extraccion)
    return list(salida.values())
