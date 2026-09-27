import csv
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import PrincipioActivo
from app.texto import normalizar


def texto_busqueda(dci_es: str, dci_en: str, terminos_openfda: str | None, sinonimos: str | None = None) -> str:
    """DCI, sinónimos locales (p. ej. «acetaminofén», «dipirona») y términos de openFDA, separados por «|»."""
    terminos = (terminos_openfda or "").replace("+", "|")
    partes = [dci_es, dci_en, *(sinonimos or "").split("|"), *terminos.split("|")]
    return " | ".join(dict.fromkeys(normalizar(p) for p in partes if p.strip()))


def cargar_principios(db: Session, ruta_csv: Path) -> tuple[int, int]:
    """Crea o actualiza principios activos desde el CSV semilla. Devuelve (creados, actualizados)."""
    creados = actualizados = 0
    existentes = {p.dci_es: p for p in db.scalars(select(PrincipioActivo))}
    with open(ruta_csv, encoding="utf-8", newline="") as f:
        for fila in csv.DictReader(f):
            datos = {
                "dci_es": fila["dci_es"].strip(),
                "dci_en": fila["dci_en"].strip(),
                "atc": fila["atc"].strip(),
                "grupo": fila["grupo"].strip(),
                "terminos_openfda": (fila.get("openfda_ingredientes") or "").strip() or None,
                "filtro_nombre_openfda": (fila.get("openfda_filtro_nombre") or "").strip() or None,
                "vias_openfda": (fila.get("openfda_vias") or "").strip() or None,
                "ficha_set_id_openfda": (fila.get("openfda_ficha_set_id") or "").strip() or None,
            }
            datos["texto_busqueda"] = texto_busqueda(
                datos["dci_es"], datos["dci_en"], datos["terminos_openfda"], fila.get("sinonimos")
            )
            principio = existentes.get(datos["dci_es"])
            if principio is None:
                db.add(PrincipioActivo(**datos))
                creados += 1
            elif any(getattr(principio, k) != v for k, v in datos.items()):
                for k, v in datos.items():
                    setattr(principio, k, v)
                actualizados += 1
    db.commit()
    return creados, actualizados
