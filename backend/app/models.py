from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Table, Text, TypeDecorator, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def ahora() -> datetime:
    return datetime.now(timezone.utc)


class FechaUTC(TypeDecorator):
    """DateTime con zona horaria; SQLite la pierde al leer, así que se restaura como UTC."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_result_value(self, valor, dialect):
        if valor is not None and valor.tzinfo is None:
            return valor.replace(tzinfo=timezone.utc)
        return valor


producto_principio = Table(
    "producto_principio",
    Base.metadata,
    Column("producto_id", ForeignKey("productos_comerciales.id", ondelete="CASCADE"), primary_key=True),
    Column("principio_activo_id", ForeignKey("principios_activos.id", ondelete="CASCADE"), primary_key=True),
)


class FuenteDatos(Base):
    """Fuente oficial de la que se extraen datos (una por agencia/endpoint)."""

    __tablename__ = "fuentes_datos"

    id: Mapped[int] = mapped_column(primary_key=True)
    codigo: Mapped[str] = mapped_column(String(50), unique=True)
    nombre: Mapped[str] = mapped_column(String(200))
    agencia: Mapped[str] = mapped_column(String(100))
    pais: Mapped[str] = mapped_column(String(2))
    url: Mapped[str] = mapped_column(String(500))
    licencia: Mapped[str | None] = mapped_column(String(200))
    ultima_sincronizacion: Mapped[datetime | None] = mapped_column(FechaUTC())


class PrincipioActivo(Base):
    __tablename__ = "principios_activos"

    id: Mapped[int] = mapped_column(primary_key=True)
    dci_es: Mapped[str] = mapped_column(String(200), unique=True)
    dci_en: Mapped[str] = mapped_column(String(200))
    atc: Mapped[str] = mapped_column(String(50), index=True)
    grupo: Mapped[str] = mapped_column(String(100), index=True)
    # Componentes separados por "+", alternativas por "|" (p. ej. "amoxicillin+clavulanate|clavulanic acid").
    terminos_openfda: Mapped[str | None] = mapped_column(Text)
    # Expresión regular opcional sobre el nombre comercial; con "!" delante, excluye.
    # Necesaria cuando la FDA usa el mismo ingrediente para productos distintos
    # (p. ej. insulina humana regular y NPH figuran ambas como "INSULIN HUMAN").
    filtro_nombre_openfda: Mapped[str | None] = mapped_column(String(200))
    # DCI (es/en) y términos de openFDA normalizados, para búsqueda.
    texto_busqueda: Mapped[str] = mapped_column(Text)
    creado_en: Mapped[datetime] = mapped_column(FechaUTC(), default=ahora)
    actualizado_en: Mapped[datetime] = mapped_column(FechaUTC(), default=ahora, onupdate=ahora)

    productos: Mapped[list["ProductoComercial"]] = relationship(
        secondary=producto_principio, back_populates="principios"
    )


class ProductoComercial(Base):
    """Producto registrado en un país, tal como lo publica la agencia."""

    __tablename__ = "productos_comerciales"
    __table_args__ = (UniqueConstraint("fuente_id", "id_externo"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    fuente_id: Mapped[int] = mapped_column(ForeignKey("fuentes_datos.id"))
    id_externo: Mapped[str] = mapped_column(String(100))
    pais: Mapped[str] = mapped_column(String(2), index=True)
    nombre_comercial: Mapped[str] = mapped_column(String(300))
    nombre_busqueda: Mapped[str] = mapped_column(String(300), index=True)
    laboratorio: Mapped[str | None] = mapped_column(String(300))
    forma_farmaceutica: Mapped[str | None] = mapped_column(String(200))
    via: Mapped[str | None] = mapped_column(String(200))
    composicion: Mapped[str | None] = mapped_column(Text)
    tipo_producto: Mapped[str | None] = mapped_column(String(100))
    categoria_registro: Mapped[str | None] = mapped_column(String(100))
    n_registro: Mapped[str | None] = mapped_column(String(100))
    es_generico: Mapped[bool | None] = mapped_column(Boolean)
    # vigente | listado_vencido | no_listado
    estado: Mapped[str] = mapped_column(String(30), index=True)
    url_fuente: Mapped[str | None] = mapped_column(String(500))
    url_ficha: Mapped[str | None] = mapped_column(String(500))
    hash_contenido: Mapped[str] = mapped_column(String(64))
    # Última vez que se comprobó en la fuente (fecha de verificación).
    fecha_extraccion: Mapped[datetime] = mapped_column(FechaUTC())
    # Última vez que cambió el contenido.
    fecha_actualizacion: Mapped[datetime] = mapped_column(FechaUTC())

    fuente: Mapped[FuenteDatos] = relationship()
    principios: Mapped[list[PrincipioActivo]] = relationship(
        secondary=producto_principio, back_populates="productos"
    )
    presentaciones: Mapped[list["Presentacion"]] = relationship(
        back_populates="producto", cascade="all, delete-orphan", order_by="Presentacion.id_externo"
    )


class Presentacion(Base):
    __tablename__ = "presentaciones"

    id: Mapped[int] = mapped_column(primary_key=True)
    producto_id: Mapped[int] = mapped_column(ForeignKey("productos_comerciales.id", ondelete="CASCADE"), index=True)
    id_externo: Mapped[str | None] = mapped_column(String(100))
    descripcion: Mapped[str | None] = mapped_column(Text)
    fecha_inicio_comercializacion: Mapped[str | None] = mapped_column(String(10))

    producto: Mapped[ProductoComercial] = relationship(back_populates="presentaciones")


class Sincronizacion(Base):
    """Registro de cada ejecución de un conector (auditoría de las fuentes)."""

    __tablename__ = "sincronizaciones"

    id: Mapped[int] = mapped_column(primary_key=True)
    fuente_id: Mapped[int] = mapped_column(ForeignKey("fuentes_datos.id"))
    principio_activo_id: Mapped[int | None] = mapped_column(ForeignKey("principios_activos.id", ondelete="SET NULL"))
    iniciada_en: Mapped[datetime] = mapped_column(FechaUTC(), default=ahora)
    finalizada_en: Mapped[datetime | None] = mapped_column(FechaUTC())
    # en_curso | ok | truncada | error
    estado: Mapped[str] = mapped_column(String(20))
    consulta: Mapped[str | None] = mapped_column(Text)
    n_recibidos: Mapped[int] = mapped_column(Integer, default=0)
    n_creados: Mapped[int] = mapped_column(Integer, default=0)
    n_actualizados: Mapped[int] = mapped_column(Integer, default=0)
    n_sin_cambios: Mapped[int] = mapped_column(Integer, default=0)
    n_no_listados: Mapped[int] = mapped_column(Integer, default=0)
    mensaje: Mapped[str | None] = mapped_column(Text)

    fuente: Mapped[FuenteDatos] = relationship()
    principio_activo: Mapped[PrincipioActivo | None] = relationship()
