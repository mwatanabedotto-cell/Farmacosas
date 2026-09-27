from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

RAIZ_REPO = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    """Configuración leída de variables de entorno con prefijo FARMACOSAS_ (o de .env)."""

    model_config = SettingsConfigDict(env_prefix="FARMACOSAS_", env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./farmacosas.db"

    # Clave de administración para los endpoints /admin (cabecera X-Admin-Key).
    # Si no se define, los endpoints de administración quedan deshabilitados.
    admin_api_key: str | None = None

    # openFDA: sin clave el límite es de 1000 peticiones/día por IP; con clave, 120 000.
    openfda_api_key: str | None = None
    openfda_max_resultados: int = 5000
    openfda_pausa_segundos: float = 0.3

    csv_principios: Path = RAIZ_REPO / "data" / "principios_activos_iniciales.csv"


@lru_cache
def get_settings() -> Settings:
    return Settings()
