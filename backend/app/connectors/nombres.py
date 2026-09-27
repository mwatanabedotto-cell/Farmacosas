"""Asignación de productos a principios activos por nombre genérico (fuentes sin código ATC).

Se exige que el producto tenga exactamente los mismos componentes que el principio activo (admitiendo sales:
«Enoxaparina sódica», «Clorhidrato de tramadol») y, si encaja con varios, se elige el nombre más específico
(«Insulina humana isófana» es NPH, no insulina regular).
"""

import re
from itertools import permutations

from app.texto import normalizar

SAL_INICIAL = re.compile(
    r"^(clorhidrato|bromhidrato|sulfato|fosfato|maleato|besilato|succinato|tartrato|citrato|acetato|bromuro|"
    r"mesilato|fumarato|valerato|propionato|dipropionato|fosfato sodico|succinato sodico)\s+de\s+"
)



def desinvertir(componente: str) -> str:
    """Notación invertida de AlfaBeta (Argentina): «acetilsalicilico,ac.» -> «acido acetilsalicilico»,
    «potasio,cloruro» -> «cloruro de potasio», «losartan,potasico» -> «losartan potasico»."""
    if "," not in componente:
        return componente
    base, _, resto = (x.strip() for x in componente.partition(","))
    if not base or not resto:
        return base or resto
    if resto.rstrip(".") in ("ac", "acido"):
        return f"acido {base}"
    if re.fullmatch(r"\w+(ico|ica)", resto):
        return f"{base} {resto}"
    return f"{resto} de {base}"


def componentes(texto: str) -> list[str]:
    return [desinvertir(c.strip()) for c in re.split(r"\s*[/+]\s*", normalizar(texto)) if c.strip()]


def _empieza(componente: str, termino: str) -> bool:
    for c in (componente, SAL_INICIAL.sub("", componente)):
        if c == termino or (c.startswith(termino) and c[len(termino)] in " ,("):
            return True
    return False


def puntuacion(generica: str, variante: list[str]) -> int:
    """Longitud de la coincidencia si la denominación genérica tiene exactamente esos componentes; si no, 0."""
    comps = componentes(generica)
    if len(comps) != len(variante) or len(comps) > 4:
        return 0
    for orden in permutations(variante):
        if all(_empieza(c, t) for c, t in zip(comps, orden)):
            return sum(len(t) for t in variante)
    return 0


def variantes(texto_busqueda: str) -> list[list[str]]:
    """Nombres del principio activo (DCI, sinónimos...) como listas de componentes."""
    return [componentes(n) for n in (texto_busqueda or "").split("|") if n.strip()]


def cumple_filtro(nombre: str, filtro: str | None) -> bool:
    """Filtro por nombre comercial: 'regex' debe coincidir; '!regex' no debe coincidir."""
    if not filtro:
        return True
    excluir = filtro.startswith("!")
    coincide = re.search(filtro[1:] if excluir else filtro, nombre, re.IGNORECASE) is not None
    return coincide != excluir


def mejor_principio(
    generica: str, indice: dict[int, list[list[str]]], marca: str = "", filtros: dict[int, str | None] | None = None
) -> int | None:
    """El principio activo cuyo nombre coincide de forma más específica (p. ej. NPH frente a regular).

    Si se indican `marca` y `filtros` (filtro de nombre comercial por principio activo), se descartan los
    principios cuyo filtro no admite la marca: así se separan productos con la misma denominación genérica
    («insulina humana»: Insulatard es NPH, Densulin R es regular).
    """
    mejor, puntos = None, 0
    for principio_id, vs in indice.items():
        if filtros and marca and not cumple_filtro(marca, filtros.get(principio_id)):
            continue
        for v in vs:
            p = puntuacion(generica, v)
            if p > puntos:
                mejor, puntos = principio_id, p
    return mejor
