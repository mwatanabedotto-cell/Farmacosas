"""Monografías en formato vademécum: borrador desde la ficha técnica, edición, publicación y vista pública.

Regla central: el texto importado de una ficha nunca se publica sin revisión. Cada
sección publicada la redacta o revisa un editor y cita al menos una referencia, y la
posología se publica como pautas estructuradas (indicación, población, dosis, vía...).
"""

import re
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, func, insert, select
from sqlalchemy.orm import Session

from app.services.listas_esenciales import resumen as resumen_listas
from app.models import (
    FichaTecnica,
    Monografia,
    PautaPosologica,
    PrincipioActivo,
    ProductoComercial,
    Referencia,
    SeccionMonografia,
    ahora,
    cita_pauta,
    cita_seccion,
    producto_principio,
)


@dataclass(frozen=True)
class ExtraccionCima:
    """Cómo obtener una sección de la ficha técnica española (numeración del RCP: 4.1, 4.2...)."""

    # Secciones a usar, incluidas sus subsecciones (p. ej. "4.2" abarca "4.2.1", "4.2.2"...).
    prefijos: tuple[str, ...]
    # Solo subsecciones cuyo título coincide (p. ej. "administración"); con "!" delante, las que no.
    titulo: str | None = None
    # Solo las frases que coinciden.
    filtro: str | None = None
    # Solo los primeros párrafos, hasta este número de caracteres.
    max_caracteres: int | None = None


@dataclass(frozen=True)
class TipoSeccion:
    tipo: str
    titulo: str
    fuentes: tuple[str, ...] = ()
    # Se usan solo si ninguna de las fuentes principales tiene texto.
    fuentes_alternativas: tuple[str, ...] = ()
    # El texto de estas secciones se elimina del resultado (ya se muestra en otra sección).
    excluir: tuple[str, ...] = ()
    # Si se indica, del texto de las fuentes solo se conservan las frases que coinciden.
    filtro: str | None = None
    # Igual, pero solo para las fuentes alternativas.
    filtro_alternativas: str | None = None
    obligatoria: bool = False
    # Extracción desde la ficha técnica española: se prueba cada alternativa en orden.
    cima: tuple[ExtraccionCima, ...] = ()
    # Si es True, se concatenan todas las extracciones de `cima` en lugar de usar la primera con texto.
    cima_combinar: bool = False


RENAL = r"\b(renal|kidney|creatinine clearance|CrCl|CLcr|eGFR|dialysis|hemodialysis)\b"
HEPATICA = r"\b((hepatic|liver) (impairment|insufficiency|disease|dysfunction)|Child-Pugh|cirrhosis)\b"
EMBARAZO = r"\b(pregnan\w*|fetal|fetus|teratogen\w*)\b"
LACTANCIA = r"\b(lactation|lactating|breast[- ]?(fed|feeding|milk)|breastfe\w*|nursing|human milk)\b"
RENAL_ES = r"\b(renal|riñón|riñones|aclaramiento de creatinina|ClCr|CLcr|filtrado glomerular|TFG|diálisis|hemodiálisis)\b"
HEPATICA_ES = r"(\b(insuficiencia|alteración|enfermedad|disfunción) hepática|\bChild[- ]Pugh\b|\bcirrosis\b)"
EMBARAZO_ES = r"\b(embaraz\w*|gestación|gestante\w*|fet\w+|teratog\w*)\b"
LACTANCIA_ES = r"\b(lactancia|leche materna|amamant\w*)\b"
TITULO_ADMINISTRACION = r"^\s*(forma|modo|v[ií]a) de administraci"
ADMINISTRACION_ES = (r"\b(forma de administraci\w*|v[ií]a (oral|intravenosa|intramuscular|subcut[aá]nea)|inyecci\w+|perfusi[oó]n|"
                     r"diluir|diluci[oó]n|reconstitu\w+|tragar|con o sin alimentos|con (las )?comidas|en ayunas)\b")
PEDIATRIA_ES = r"\b(niñ[oa]s?|pediátric\w*|población pediátrica|lactantes|neonat\w*|recién nacid\w*|adolescent\w*|menores de \d+)\b"

# Texto que no debe arrastrarse a las extracciones renal/hepática (tiene sus propias secciones).
OTRAS_POBLACIONES = ("pregnancy", "lactation", "nursing_mothers", "pediatric_use")

# Orden de uso clínico: primero lo que se consulta al prescribir. Las referencias se añaden al final.
# La posología se completa con pautas estructuradas (adultos: adultos/geriatría/todas; niños: pediatría).
TIPOS_SECCION: tuple[TipoSeccion, ...] = (
    TipoSeccion("alerta", "Alerta destacada", ("boxed_warning",)),
    TipoSeccion("indicaciones", "Indicaciones", ("indications_and_usage",), obligatoria=True,
                cima=(ExtraccionCima(("4.1",)),)),
    # Muchas fichas no dividen la 4.2: su título es «Posología y forma de administración». Por eso los
    # filtros miran el inicio del título de la subsección y, si no hay subsecciones, las frases.
    TipoSeccion("posologia_adultos", "Posología en adultos", ("dosage_and_administration",),
                cima=(ExtraccionCima(("4.2",), titulo=f"!{TITULO_ADMINISTRACION}|pedi"),)),
    TipoSeccion("posologia_ninos", "Posología en niños", ("pediatric_use",),
                cima=(ExtraccionCima(("4.2",), titulo="pedi"), ExtraccionCima(("4.2",), filtro=PEDIATRIA_ES))),
    TipoSeccion("modo_administracion", "Modo de administración",
                cima=(ExtraccionCima(("4.2",), titulo=TITULO_ADMINISTRACION),
                      ExtraccionCima(("4.2",), filtro=ADMINISTRACION_ES))),
    TipoSeccion("insuficiencia_renal", "Ajuste en insuficiencia renal",
                ("dosage_and_administration", "use_in_specific_populations"), excluir=OTRAS_POBLACIONES, filtro=RENAL,
                cima=(ExtraccionCima(("4.2", "4.4"), filtro=RENAL_ES),)),
    TipoSeccion("insuficiencia_hepatica", "Ajuste en insuficiencia hepática",
                ("dosage_and_administration", "use_in_specific_populations"), excluir=OTRAS_POBLACIONES,
                filtro=HEPATICA, cima=(ExtraccionCima(("4.2", "4.4"), filtro=HEPATICA_ES),)),
    TipoSeccion("contraindicaciones", "Contraindicaciones", ("contraindications",), obligatoria=True,
                cima=(ExtraccionCima(("4.3",)),)),
    TipoSeccion("advertencias", "Advertencias y precauciones",
                ("warnings_and_cautions", "warnings", "precautions"), obligatoria=True, cima=(ExtraccionCima(("4.4",)),)),
    TipoSeccion("interacciones", "Interacciones", ("drug_interactions",), cima=(ExtraccionCima(("4.5",)),)),
    TipoSeccion("embarazo_lactancia", "Embarazo y lactancia", ("pregnancy", "lactation", "nursing_mothers"),
                ("use_in_specific_populations",), filtro_alternativas=f"{EMBARAZO}|{LACTANCIA}",
                cima=(ExtraccionCima(("4.6",), titulo="embarazo|lactancia"), ExtraccionCima(("4.6",), titulo="!fertilidad"))),
    TipoSeccion("reacciones_adversas", "Reacciones adversas", ("adverse_reactions",), cima=(ExtraccionCima(("4.8",)),)),
    TipoSeccion("sobredosis", "Sobredosis y antídoto", ("overdosage",), cima=(ExtraccionCima(("4.9",)),)),
    TipoSeccion("perioperatorio", "Consideraciones perioperatorias"),
    TipoSeccion("mecanismo_farmacocinetica", "Mecanismo de acción y farmacocinética",
                ("mechanism_of_action", "pharmacokinetics"), ("clinical_pharmacology",),
                cima=(ExtraccionCima(("5.1",), max_caracteres=1200), ExtraccionCima(("5.2",), max_caracteres=1200)),
                cima_combinar=True),
)
TIPOS = {t.tipo: t for t in TIPOS_SECCION}

# Secciones de versiones anteriores de la estructura y su equivalente actual.
MIGRACION_SECCIONES = {
    "posologia": "posologia_adultos",
    "embarazo": "embarazo_lactancia",
    "lactancia": "embarazo_lactancia",
    "mecanismo_accion": "mecanismo_farmacocinetica",
}
TITULOS_ANTIGUOS = {"posologia": "Posología", "embarazo": "Embarazo", "lactancia": "Lactancia",
                    "mecanismo_accion": "Mecanismo de acción"}


def titulo_seccion(tipo: str) -> str:
    return TIPOS[tipo].titulo if tipo in TIPOS else TITULOS_ANTIGUOS.get(tipo, tipo)


def obligatoria(tipo: str) -> bool:
    return tipo in TIPOS and TIPOS[tipo].obligatoria

POBLACIONES = ("adultos", "pediatria", "geriatria", "todas")

CHECKLIST = {
    "dosis_verificadas": "Dosis, unidades, vías e intervalos comprobados contra la fuente",
    "ajustes_verificados": "Ajustes en insuficiencia renal/hepática y poblaciones especiales comprobados",
    "contraindicaciones_y_advertencias_verificadas": "Contraindicaciones y advertencias (incl. alerta destacada) completas",
    "interacciones_verificadas": "Interacciones relevantes revisadas",
    "referencias_verificadas": "Cada sección y pauta cita su fuente y los enlaces funcionan",
}

# Formato vademécum: textos breves. Por encima de esto se sugiere resumir (no bloquea).
MAX_CARACTERES_SECCION = 1500

DIAS_AMARILLO = 183
DIAS_ROJO = 365
MAX_MARCAS_POR_PAIS = 15


class ErrorEditorial(Exception):
    def __init__(self, errores: list[str]):
        super().__init__("; ".join(errores))
        self.errores = errores


# --- Referencias -----------------------------------------------------------------

PREFIJO_REFERENCIA = {"openfda_label": "dailymed", "cima_ft": "cima"}


def referencia_de_ficha(db: Session, ficha: FichaTecnica) -> Referencia:
    prefijo = PREFIJO_REFERENCIA.get(ficha.fuente.codigo, ficha.fuente.codigo)
    clave = f"{prefijo}:{ficha.set_id}:{ficha.version}"
    ref = db.scalar(select(Referencia).where(Referencia.clave == clave))
    if ref is None:
        ref = Referencia(
            clave=clave,
            tipo="ficha_tecnica",
            titulo=ficha.titulo,
            publicacion=ficha.laboratorio,
            fecha_publicacion=ficha.fecha_efectiva,
            url=ficha.url,
            fecha_acceso=ficha.fecha_extraccion.date().isoformat(),
        )
        db.add(ref)
        db.flush()
    return ref


def formato_vancouver(ref: Referencia) -> str:
    anio = (ref.fecha_publicacion or "")[:4]
    if ref.tipo == "articulo":
        partes = [ref.autores, ref.titulo, ref.publicacion, anio]
        texto = ". ".join(p.rstrip(".") for p in partes if p) + "."
        if ref.doi:
            texto += f" doi:{ref.doi}."
        if ref.pmid:
            texto += f" PMID: {ref.pmid}."
        return texto
    titulo = f"{ref.titulo} [ficha técnica]" if ref.tipo == "ficha_tecnica" else ref.titulo
    texto = ". ".join(p.rstrip(".") for p in [ref.autores, titulo] if p) + "."
    editorial = "; ".join(p for p in [ref.publicacion, ref.fecha_publicacion] if p)
    if editorial:
        texto += f" {editorial}."
    if ref.url:
        texto += f" Disponible en: {ref.url}."
    return texto + f" Consultado: {ref.fecha_acceso}."


def _fijar_citas(db: Session, tabla, columna: str, objeto, referencia_ids: list[int]) -> None:
    db.execute(delete(tabla).where(tabla.c[columna] == objeto.id))
    ids = list(dict.fromkeys(referencia_ids))
    if ids:
        db.execute(insert(tabla), [{columna: objeto.id, "referencia_id": rid, "orden": i} for i, rid in enumerate(ids)])
    db.expire(objeto, ["referencias"])


def _validar_referencias(db: Session, referencia_ids: list[int]) -> None:
    existentes = set(db.scalars(select(Referencia.id).where(Referencia.id.in_(referencia_ids))))
    faltan = [i for i in referencia_ids if i not in existentes]
    if faltan:
        raise ErrorEditorial([f"Referencias inexistentes: {faltan}"])


# --- Borrador --------------------------------------------------------------------

def _frases(texto: str) -> list[str]:
    return [f.strip() for f in re.split(r"\n+|(?<=[.;])\s+(?=[A-ZÁÉÍÓÚÑ0-9(•])", texto) if f.strip()]


def _filtrar_frases(texto: str, filtro: str) -> str:
    patron = re.compile(filtro, re.IGNORECASE)
    return "\n".join(dict.fromkeys(f for f in _frases(texto) if patron.search(f)))


def _primeros_parrafos(texto: str, maximo: int) -> str:
    salida: list[str] = []
    for parrafo in texto.split("\n"):
        if salida and len("\n".join(salida + [parrafo])) > maximo:
            break
        salida.append(parrafo)
    return "\n".join(salida)


def _texto_cima(ficha: FichaTecnica, tipo: TipoSeccion) -> str | None:
    if tipo.cima_combinar:
        partes = [_extraer_cima(ficha, e) for e in tipo.cima]
        return "\n\n".join(p for p in partes if p) or None
    for extraccion in tipo.cima:
        texto = _extraer_cima(ficha, extraccion)
        if texto:
            return texto
    return None


def _extraer_cima(ficha: FichaTecnica, extraccion: ExtraccionCima) -> str | None:
    partes = []
    for s in ficha.secciones:
        if not any(s.codigo == p or s.codigo.startswith(p + ".") for p in extraccion.prefijos):
            continue
        if extraccion.titulo:
            excluir = extraccion.titulo.startswith("!")
            coincide = re.search(extraccion.titulo.lstrip("!"), s.titulo or "", re.IGNORECASE) is not None
            if coincide == excluir:
                continue
        partes.append(s.texto)
    texto = "\n".join(partes)
    if extraccion.filtro:
        texto = _filtrar_frases(texto, extraccion.filtro)
    if extraccion.max_caracteres:
        texto = _primeros_parrafos(texto, extraccion.max_caracteres)
    return texto.strip() or None


def texto_desde_ficha(ficha: FichaTecnica, tipo: TipoSeccion) -> str | None:
    if ficha.fuente.codigo == "cima_ft":
        return _texto_cima(ficha, tipo)
    por_codigo = {s.codigo: s.texto for s in ficha.secciones}
    for codigos, filtro in ((tipo.fuentes, tipo.filtro), (tipo.fuentes_alternativas, tipo.filtro_alternativas)):
        textos = [por_codigo[c] for c in codigos if por_codigo.get(c)]
        if not textos:
            continue
        texto = "\n\n".join(textos)
        for c in tipo.excluir:
            if por_codigo.get(c):
                texto = texto.replace(por_codigo[c], "")
        if filtro:
            texto = _filtrar_frases(texto, filtro)
        return re.sub(r"[ \t]{2,}", " ", texto).strip() or None
    return None


def monografia_en_estado(db: Session, principio_id: int, estado: str) -> Monografia | None:
    return db.scalar(
        select(Monografia)
        .where(Monografia.principio_activo_id == principio_id, Monografia.estado == estado)
        .order_by(Monografia.version.desc())
    )


# Secciones que, si la ficha base no las tiene, se toman de otra ficha del mismo principio activo.
# La alerta destacada (boxed warning) es propia de la FDA: las fichas europeas no la incluyen.
DE_OTRA_FICHA = {"alerta"}


def _rellenar_desde_ficha(db: Session, seccion: SeccionMonografia, ficha: FichaTecnica, ref: Referencia) -> None:
    texto = texto_desde_ficha(ficha, TIPOS[seccion.tipo])
    fuente, cita = ficha, ref
    if not texto and seccion.tipo in DE_OTRA_FICHA:
        otras = db.scalars(select(FichaTecnica).where(
            FichaTecnica.principio_activo_id == ficha.principio_activo_id, FichaTecnica.id != ficha.id)).all()
        for otra in otras:
            texto = texto_desde_ficha(otra, TIPOS[seccion.tipo])
            if texto:
                fuente, cita = otra, referencia_de_ficha(db, otra)
                break
    seccion.contenido = texto
    seccion.idioma = fuente.idioma
    seccion.origen = "ficha_importada" if texto else "vacia"
    seccion.fecha_verificacion = None
    db.flush()
    _fijar_citas(db, cita_seccion, "seccion_id", seccion, [cita.id] if texto else [])


def ficha_preferida(db: Session, principio: PrincipioActivo) -> FichaTecnica | None:
    """Base del borrador: la ficha en español si existe (el editor solo tiene que resumir), si no la de EE. UU."""
    fichas = db.scalars(select(FichaTecnica).where(FichaTecnica.principio_activo_id == principio.id)).all()
    return min(fichas, key=lambda f: (f.idioma != "es", f.pais != "US", f.id), default=None)


def generar_borrador(
    db: Session, principio: PrincipioActivo, ficha: FichaTecnica | None = None, forzar: bool = False
) -> Monografia | None:
    """Crea o actualiza el borrador a partir de la ficha (por defecto, la preferida). Idempotente.

    - Si hay borrador: se refrescan solo las secciones aún no revisadas.
    - Si la versión publicada ya se basa en esta versión de la ficha: no hace nada.
    - Si no: crea un borrador nuevo que conserva las secciones revisadas y las pautas de
      la versión publicada, y rellena el resto con el texto de la ficha.
    """
    ficha = ficha or ficha_preferida(db, principio)
    if ficha is None:
        return None
    borrador = monografia_en_estado(db, principio.id, "borrador")
    publicada = monografia_en_estado(db, principio.id, "publicada")
    ref = referencia_de_ficha(db, ficha)

    if borrador is not None:
        if _ajustar_estructura(db, borrador, ficha, ref):
            db.commit()
        # `forzar` vuelve a extraer el texto aunque la ficha no haya cambiado (p. ej. tras mejorar los
        # filtros de extracción). Nunca toca lo revisado por un editor.
        if not forzar and borrador.ficha_id == ficha.id and borrador.ficha_spl_id_base == ficha.spl_id:
            return borrador
        for seccion in borrador.secciones:
            if seccion.origen != "editor":
                _rellenar_desde_ficha(db, seccion, ficha, ref)
        borrador.ficha, borrador.ficha_spl_id_base = ficha, ficha.spl_id
        db.commit()
        return borrador

    if publicada is not None and publicada.ficha_id == ficha.id and publicada.ficha_spl_id_base == ficha.spl_id:
        return None

    version = (db.scalar(select(func.max(Monografia.version)).where(Monografia.principio_activo_id == principio.id)) or 0) + 1
    borrador = Monografia(principio_activo=principio, version=version, estado="borrador",
                          ficha=ficha, ficha_spl_id_base=ficha.spl_id)
    db.add(borrador)
    # Secciones revisadas de la versión publicada, llevadas a la estructura actual.
    previas: dict[str, list[SeccionMonografia]] = {}
    for s in publicada.secciones if publicada else []:
        destino = s.tipo if s.tipo in TIPOS else MIGRACION_SECCIONES.get(s.tipo)
        if destino and s.origen == "editor":
            previas.setdefault(destino, []).append(s)
    for orden, tipo in enumerate(TIPOS_SECCION):
        seccion = SeccionMonografia(monografia=borrador, tipo=tipo.tipo, orden=orden, origen="vacia")
        db.add(seccion)
        db.flush()
        if tipo.tipo in previas:
            _copiar_revisado(db, seccion, previas[tipo.tipo])
        else:
            _rellenar_desde_ficha(db, seccion, ficha, ref)
    for pauta in publicada.pautas if publicada else []:
        _crear_pauta(db, borrador, pauta.orden, datos_pauta(pauta), [r.id for r in pauta.referencias])
    db.commit()
    return borrador


def _copiar_revisado(db: Session, seccion: SeccionMonografia, origen: list[SeccionMonografia]) -> None:
    """Copia a `seccion` el contenido revisado de una o varias secciones (p. ej. Embarazo + Lactancia)."""
    seccion.contenido = "\n\n".join(s.contenido for s in origen if s.contenido)
    seccion.idioma = origen[0].idioma
    seccion.origen = "editor"
    fechas = [s.fecha_verificacion for s in origen if s.fecha_verificacion]
    seccion.fecha_verificacion = min(fechas) if fechas else None
    db.flush()
    _fijar_citas(db, cita_seccion, "seccion_id", seccion, [r.id for s in origen for r in s.referencias])


def _ajustar_estructura(db: Session, monografia: Monografia, ficha: FichaTecnica, ref: Referencia) -> bool:
    """Adapta un borrador a la estructura actual de secciones. Devuelve True si cambió algo.

    Las secciones que ya no existen se trasladan a su equivalente (lo revisado se conserva) y las
    que faltan se crean desde la ficha.
    """
    actuales = {s.tipo: s for s in monografia.secciones}
    obsoletas = [s for t, s in actuales.items() if t not in TIPOS]
    faltan = [t for t in TIPOS_SECCION if t.tipo not in actuales]
    if not obsoletas and not faltan and [s.tipo for s in monografia.secciones] == [t.tipo for t in TIPOS_SECCION]:
        return False

    revisadas: dict[str, list[SeccionMonografia]] = {}
    for s in obsoletas:
        destino = MIGRACION_SECCIONES.get(s.tipo)
        if destino and s.origen == "editor":
            revisadas.setdefault(destino, []).append(s)
    for tipo in faltan:
        seccion = SeccionMonografia(monografia=monografia, tipo=tipo.tipo, orden=0, origen="vacia")
        db.add(seccion)
        db.flush()
        actuales[tipo.tipo] = seccion
    for destino, origen in revisadas.items():
        seccion = actuales[destino]
        previas = [seccion] if seccion.origen == "editor" else []
        _copiar_revisado(db, seccion, previas + origen)
    for tipo in faltan:
        if actuales[tipo.tipo].origen != "editor":
            _rellenar_desde_ficha(db, actuales[tipo.tipo], ficha, ref)
    for s in obsoletas:
        monografia.secciones.remove(s)
        db.delete(s)
    for orden, tipo in enumerate(TIPOS_SECCION):
        actuales[tipo.tipo].orden = orden
    db.flush()
    db.expire(monografia, ["secciones"])
    return True


# --- Edición y publicación -------------------------------------------------------

def editar_seccion(
    db: Session, monografia: Monografia, tipo: str, contenido: str | None, idioma: str, referencia_ids: list[int]
) -> SeccionMonografia:
    if monografia.estado != "borrador":
        raise ErrorEditorial(["Solo se pueden editar borradores"])
    seccion = next((s for s in monografia.secciones if s.tipo == tipo), None)
    if seccion is None:
        raise ErrorEditorial([f"Sección desconocida: {tipo}"])
    _validar_referencias(db, referencia_ids)

    contenido = (contenido or "").strip() or None
    seccion.contenido = contenido
    seccion.idioma = idioma
    seccion.origen = "editor" if contenido else "vacia"
    seccion.fecha_verificacion = ahora() if contenido else None
    db.flush()
    _fijar_citas(db, cita_seccion, "seccion_id", seccion, referencia_ids if contenido else [])
    monografia.actualizada_en = ahora()
    db.commit()
    return seccion


CAMPOS_PAUTA = ("indicacion", "poblacion", "dosis", "via", "frecuencia", "duracion", "dosis_maxima", "notas")


def datos_pauta(pauta: PautaPosologica) -> dict:
    return {c: getattr(pauta, c) for c in CAMPOS_PAUTA}


def _crear_pauta(db: Session, monografia: Monografia, orden: int, datos: dict, referencia_ids: list[int]) -> PautaPosologica:
    pauta = PautaPosologica(monografia=monografia, orden=orden, **datos)
    db.add(pauta)
    db.flush()
    _fijar_citas(db, cita_pauta, "pauta_id", pauta, referencia_ids)
    return pauta


def reemplazar_pautas(db: Session, monografia: Monografia, pautas: list[tuple[dict, list[int]]]) -> None:
    """Sustituye todas las pautas del borrador. Cada elemento: (datos, referencia_ids)."""
    if monografia.estado != "borrador":
        raise ErrorEditorial(["Solo se pueden editar borradores"])
    errores = [f"Pauta {i + 1}: población no válida" for i, (d, _) in enumerate(pautas) if d["poblacion"] not in POBLACIONES]
    if errores:
        raise ErrorEditorial(errores)
    _validar_referencias(db, [rid for _, ids in pautas for rid in ids])
    ids = [p.id for p in monografia.pautas]
    if ids:
        db.execute(delete(cita_pauta).where(cita_pauta.c.pauta_id.in_(ids)))
    monografia.pautas.clear()
    db.flush()
    for orden, (datos, referencia_ids) in enumerate(pautas):
        _crear_pauta(db, monografia, orden, datos, referencia_ids)
    monografia.actualizada_en = ahora()
    db.commit()
    db.refresh(monografia)


def errores_publicacion(monografia: Monografia, checklist: dict[str, bool]) -> list[str]:
    errores = []
    if monografia.estado != "borrador":
        errores.append("Solo se pueden publicar borradores")
    pendientes = [k for k in CHECKLIST if not checklist.get(k)]
    if pendientes:
        errores.append(f"Checklist incompleta: {', '.join(pendientes)}")
    for s in monografia.secciones:
        titulo = titulo_seccion(s.tipo)
        if s.origen == "ficha_importada":
            errores.append(f"«{titulo}» contiene texto importado sin revisar")
        elif s.origen == "vacia" and obligatoria(s.tipo):
            errores.append(f"«{titulo}» es obligatoria")
        elif s.origen == "editor" and not s.referencias:
            errores.append(f"«{titulo}» no tiene referencias")
    if not monografia.pautas:
        errores.append("La posología necesita al menos una pauta estructurada")
    errores += [f"Pauta {p.orden + 1} ({p.indicacion}) no tiene referencias" for p in monografia.pautas if not p.referencias]
    return errores


def avisos_formato(monografia: Monografia) -> list[str]:
    """Sugerencias que no bloquean la publicación."""
    avisos = []
    for s in monografia.secciones:
        if s.origen == "editor" and s.contenido and len(s.contenido) > MAX_CARACTERES_SECCION:
            avisos.append(f"«{titulo_seccion(s.tipo)}» tiene {len(s.contenido)} caracteres; "
                          f"en formato vademécum conviene resumir a menos de {MAX_CARACTERES_SECCION}")
        if s.origen == "editor" and s.idioma != "es":
            avisos.append(f"«{titulo_seccion(s.tipo)}» no está en español")
    return avisos


def publicar(db: Session, monografia: Monografia, revisor: str, checklist: dict[str, bool]) -> Monografia:
    errores = errores_publicacion(monografia, checklist)
    if errores:
        raise ErrorEditorial(errores)
    anterior = monografia_en_estado(db, monografia.principio_activo_id, "publicada")
    if anterior is not None:
        anterior.estado = "archivada"
    monografia.estado = "publicada"
    monografia.publicada_en = ahora()
    monografia.revisado_por = revisor
    db.commit()
    return monografia


# --- Vista pública ---------------------------------------------------------------

def frescura(monografia: Monografia, ficha_actual: FichaTecnica | None, momento: datetime | None = None) -> dict:
    momento = momento or ahora()
    if ficha_actual is not None and monografia.ficha_id == ficha_actual.id and ficha_actual.spl_id != monografia.ficha_spl_id_base:
        return {"nivel": "rojo", "motivo": "La ficha técnica oficial cambió después de la última revisión"}
    fechas = [s.fecha_verificacion for s in monografia.secciones if s.origen == "editor" and s.fecha_verificacion]
    referencia = min(fechas) if fechas else monografia.publicada_en or monografia.creada_en
    dias = (momento - referencia).days
    nivel = "rojo" if dias >= DIAS_ROJO else "amarillo" if dias >= DIAS_AMARILLO else "verde"
    return {"nivel": nivel, "motivo": f"Sección más antigua verificada hace {dias} días"}


def nombres_comerciales(db: Session, principio_id: int) -> list[dict]:
    """Resumen por país: marcas vigentes (sin repetir) y número de genéricos."""
    filas = db.execute(
        select(ProductoComercial.pais, ProductoComercial.nombre_comercial, ProductoComercial.es_generico)
        .join(producto_principio, producto_principio.c.producto_id == ProductoComercial.id)
        .where(producto_principio.c.principio_activo_id == principio_id, ProductoComercial.estado == "vigente")
        .order_by(ProductoComercial.pais, ProductoComercial.nombre_comercial)
    ).all()
    paises: dict[str, dict] = {}
    for pais, nombre, generico in filas:
        datos = paises.setdefault(pais, {"pais": pais, "marcas": {}, "productos": 0, "genericos": 0})
        datos["productos"] += 1
        datos["genericos"] += bool(generico)
        if generico is False:
            datos["marcas"].setdefault(nombre.upper(), nombre)
    return [
        {**d, "marcas": list(d["marcas"].values())[:MAX_MARCAS_POR_PAIS]} for d in paises.values()
    ]


def vista_publica(db: Session, principio: PrincipioActivo) -> dict | None:
    monografia = monografia_en_estado(db, principio.id, "publicada")
    if monografia is None:
        return None
    numeros: dict[int, int] = {}
    referencias: list[Referencia] = []

    def citar(refs: list[Referencia]) -> list[int]:
        for ref in refs:
            if ref.id not in numeros:
                referencias.append(ref)
                numeros[ref.id] = len(referencias)
        return [numeros[r.id] for r in refs]

    secciones, pautas = [], []
    for s in monografia.secciones:
        if s.tipo in ("posologia_adultos", "posologia"):
            pautas = [{**datos_pauta(p), "citas": citar(p.referencias)} for p in monografia.pautas]
        if s.origen != "editor":
            continue
        secciones.append({
            "tipo": s.tipo, "titulo": titulo_seccion(s.tipo), "contenido": s.contenido, "idioma": s.idioma,
            "fecha_verificacion": s.fecha_verificacion, "citas": citar(s.referencias),
        })
    ficha_actual = db.get(FichaTecnica, monografia.ficha_id) if monografia.ficha_id else None
    return {
        "principio_activo_id": principio.id,
        "dci": principio.dci_es,
        "dci_en": principio.dci_en,
        "atc": principio.atc,
        "grupo": principio.grupo,
        "version": monografia.version,
        "ultima_actualizacion": monografia.publicada_en,
        "revisado_por": monografia.revisado_por,
        "frescura": frescura(monografia, ficha_actual),
        "nombres_comerciales": nombres_comerciales(db, principio.id),
        "listas_esenciales": resumen_listas(db, principio.id),
        "secciones": secciones,
        "pautas": pautas,
        "referencias": [
            {"numero": numeros[r.id], "texto": formato_vancouver(r), "url": r.url, "doi": r.doi,
             "pmid": r.pmid, "fecha_acceso": r.fecha_acceso}
            for r in referencias
        ],
    }
