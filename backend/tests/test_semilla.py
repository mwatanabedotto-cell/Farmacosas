import csv
import re

from sqlalchemy import func, select

from app.connectors.openfda import parsear_terminos
from app.core.config import Settings
from app.models import PrincipioActivo
from app.services.semilla import cargar_principios

ATC = re.compile(r"^[A-Z]\d{2}[A-Z]{2}\d{2}$")


def test_csv_semilla_es_valido():
    with open(Settings().csv_principios, encoding="utf-8") as f:
        filas = list(csv.DictReader(f))
    assert len(filas) == 100
    assert len({f["dci_es"] for f in filas}) == 100
    for fila in filas:
        for codigo in fila["atc"].split(" / "):
            assert ATC.match(codigo), fila
        assert parsear_terminos(fila["openfda_ingredientes"]), fila


def test_cargar_principios_es_idempotente(db):
    ruta = Settings().csv_principios
    assert cargar_principios(db, ruta) == (100, 0)
    assert cargar_principios(db, ruta) == (0, 0)
    assert db.scalar(select(func.count()).select_from(PrincipioActivo)) == 100
