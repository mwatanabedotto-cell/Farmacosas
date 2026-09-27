from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings, get_settings
from app.db import Base, crear_engine, get_db, get_session_factory
from app.main import app
from app.services.semilla import cargar_principios


@pytest.fixture
def fabrica():
    engine = crear_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()


@pytest.fixture
def db(fabrica):
    with fabrica() as sesion:
        yield sesion


@pytest.fixture
def db_sembrada(db):
    cargar_principios(db, Settings().csv_principios)
    return db


@pytest.fixture
def cliente_api(fabrica, db_sembrada):
    def _get_db():
        with fabrica() as sesion:
            yield sesion

    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[get_session_factory] = lambda: fabrica
    app.dependency_overrides[get_settings] = lambda: Settings(admin_api_key="secreta")
    yield TestClient(app)
    app.dependency_overrides.clear()


def producto_ndc(ndc: str, ingredientes: list[tuple[str, str]], **extra) -> dict:
    """Registro con la forma del NDC Directory de openFDA."""
    base = {
        "product_ndc": ndc,
        "generic_name": " and ".join(n for n, _ in ingredientes),
        "brand_name": extra.pop("brand_name", ingredientes[0][0].title()),
        "labeler_name": "Laboratorio Ejemplo",
        "dosage_form": "INJECTION, SOLUTION",
        "route": ["SUBCUTANEOUS"],
        "marketing_category": "ANDA",
        "application_number": "ANDA000001",
        "product_type": "HUMAN PRESCRIPTION DRUG",
        "finished": True,
        "listing_expiration_date": f"{date.today().year + 1}1231",
        "active_ingredients": [{"name": n, "strength": s} for n, s in ingredientes],
        "packaging": [{"package_ndc": f"{ndc}-01", "description": "10 SYRINGE in 1 CARTON", "marketing_start_date": "20200115"}],
        "openfda": {"spl_set_id": [f"set-{ndc}"]},
    }
    base.update(extra)
    return base


def ficha_label(set_id: str, spl_id: str = "spl-1", version: str = "1", **secciones) -> dict:
    """Registro con la forma de /drug/label de openFDA."""
    base = {
        "set_id": set_id,
        "id": spl_id,
        "version": version,
        "effective_time": "20260528",
        "openfda": {"brand_name": ["Lovenox"], "generic_name": ["ENOXAPARIN SODIUM"],
                    "manufacturer_name": ["Sanofi-Aventis U.S. LLC"], "application_number": ["NDA020164"],
                    "product_type": ["HUMAN PRESCRIPTION DRUG"]},
    }
    base.update({k: [v] for k, v in secciones.items()})
    return base


def medicamento_cima(nregistro: str, nombre: str, **extra) -> dict:
    """Registro con la forma de /medicamentos de CIMA."""
    base = {
        "nregistro": nregistro,
        "nombre": nombre,
        "labtitular": "Sanofi Aventis S.A.",
        "cpresc": "Medicamento Sujeto A Prescripción Médica",
        "estado": {"aut": 635122800000},
        "comerc": True,
        "generico": False,
        "biosimilar": False,
        "psum": False,
        "docs": [{"tipo": 1, "urlHtml": f"https://cima.aemps.es/cima/dochtml/ft/{nregistro}/FT.html",
                  "secc": True, "fecha": 1665182910000}],
        "viasAdministracion": [{"id": 58, "nombre": "VÍA SUBCUTÁNEA"}],
        "formaFarmaceutica": {"nombre": "SOLUCIÓN INYECTABLE EN JERINGA PRECARGADA"},
        "vtm": {"nombre": "enoxaparina sodio"},
        "dosis": "40 mg",
    }
    base.update(extra)
    return base
