"""Ficha técnica de referencia en España (CIMA), en español."""

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.connectors.cima import CimaClient, ConectorCima, ficha_tecnica, normalizar_ficha
from app.models import FichaTecnica, FuenteDatos, PrincipioActivo, ProductoComercial, Sincronizacion
from app.services import sincronizacion
from app.services.fichas import ficha_existente, guardar_ficha, importar_lote

CODIGO_FUENTE = "cima_ft"
MAX_CANDIDATOS = 3


def obtener_fuente(db: Session) -> FuenteDatos:
    return sincronizacion.obtener_fuente(db, CODIGO_FUENTE, {
        "nombre": "CIMA (AEMPS) — fichas técnicas",
        "agencia": "AEMPS",
        "pais": "ES",
        "url": "https://cima.aemps.es/",
        "licencia": "Ver condiciones de uso en https://cima.aemps.es/",
    })


def candidatos(db: Session, principio: PrincipioActivo) -> list[str]:
    """nregistro de los medicamentos españoles, del más al menos adecuado como ficha de referencia.

    Comercializado > con receta y no genérico (EFG) > no genérico > con receta > resto;
    dentro de cada nivel, el autorizado primero (habitualmente el medicamento original).
    """
    fuente = sincronizacion.obtener_fuente(db, ConectorCima.codigo_fuente, ConectorCima.datos_fuente)
    productos = db.scalars(
        select(ProductoComercial)
        .options(selectinload(ProductoComercial.presentaciones))
        .where(
            ProductoComercial.id.in_([p.id for p in principio.productos]),
            ProductoComercial.fuente_id == fuente.id,
            ProductoComercial.spl_set_id.is_not(None),
        )
    ).all()

    def clave(p: ProductoComercial) -> tuple:
        receta = "prescripci" in (p.tipo_producto or "").lower()
        nivel = 0 if receta and not p.es_generico else 1 if not p.es_generico else 2 if receta else 3
        fechas = [x.fecha_inicio_comercializacion for x in p.presentaciones if x.fecha_inicio_comercializacion]
        return (p.estado != "vigente", nivel, min(fechas) if fechas else "9999", p.spl_set_id)

    return list(dict.fromkeys(p.spl_set_id for p in sorted(productos, key=clave)))


def importar_ficha(db: Session, cliente: CimaClient, principio: PrincipioActivo) -> tuple[FichaTecnica | None, str]:
    fuente = obtener_fuente(db)
    actual = ficha_existente(db, principio, fuente)
    for nregistro in candidatos(db, principio)[:MAX_CANDIDATOS]:
        med = cliente.medicamento(nregistro)
        if not med or not (ficha_tecnica(med) or {}).get("secc"):
            continue
        # Misma ficha y misma fecha de revisión: no hace falta descargar las secciones.
        # (Algunas fichas, p. ej. de autorización europea, no traen fecha: entonces se compara el contenido.)
        fecha = ficha_tecnica(med).get("fecha")
        if fecha and actual is not None and actual.spl_id == f"{nregistro}:{fecha}":
            return guardar_ficha(db, principio, fuente, "es", _datos_actuales(actual))
        secciones = cliente.secciones_ficha(nregistro)
        datos = normalizar_ficha(med, secciones)
        if datos["secciones"]:
            return guardar_ficha(db, principio, fuente, "es", datos)
    return None, "sin_ficha"


def _datos_actuales(ficha: FichaTecnica) -> dict:
    """Reconstruye los datos guardados para que guardar_ficha los reconozca como sin cambios."""
    campos = ("set_id", "spl_id", "version", "fecha_efectiva", "titulo", "laboratorio", "n_registro", "tipo_producto", "url")
    return {
        **{c: getattr(ficha, c) for c in campos},
        "secciones": [{"codigo": s.codigo, "titulo": s.titulo, "orden": s.orden, "texto": s.texto} for s in ficha.secciones],
    }


def importar_fichas(db: Session, cliente: CimaClient, principio_ids: list[int] | None = None) -> list[Sincronizacion]:
    return importar_lote(db, obtener_fuente(db), lambda db_, p: importar_ficha(db_, cliente, p), principio_ids)
