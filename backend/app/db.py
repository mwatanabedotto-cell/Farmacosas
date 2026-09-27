from collections.abc import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings


class Base(DeclarativeBase):
    pass


def crear_engine(url: str, **kwargs) -> Engine:
    if not url.startswith("sqlite"):
        return create_engine(url, **kwargs)
    kwargs.setdefault("connect_args", {"check_same_thread": False})
    engine = create_engine(url, **kwargs)

    # SQLite no aplica las claves foráneas (ni ON DELETE CASCADE) si no se activan por conexión.
    @event.listens_for(engine, "connect")
    def _activar_claves_foraneas(conexion, _):
        cursor = conexion.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


engine = crear_engine(get_settings().database_url)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def get_session_factory() -> sessionmaker[Session]:
    return SessionLocal


def get_db() -> Iterator[Session]:
    with SessionLocal() as db:
        yield db
