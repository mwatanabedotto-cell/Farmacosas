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
    # Vías preferidas para elegir la ficha de referencia, separadas por "|" (p. ej. "RESPIRATORY (INHALATION)").
    vias_openfda: Mapped[str | None] = mapped_column(String(200))
    # Ficha técnica de referencia fijada a mano (set_id de DailyMed); si es nula se elige automáticamente.
    ficha_set_id_openfda: Mapped[str | None] = mapped_column(String(100))
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
    spl_set_id: Mapped[str | None] = mapped_column(String(100), index=True)
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


class FichaTecnica(Base):
    """Ficha técnica oficial de referencia de un principio activo en un país (texto original)."""

    __tablename__ = "fichas_tecnicas"
    __table_args__ = (UniqueConstraint("principio_activo_id", "fuente_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    principio_activo_id: Mapped[int] = mapped_column(ForeignKey("principios_activos.id", ondelete="CASCADE"))
    fuente_id: Mapped[int] = mapped_column(ForeignKey("fuentes_datos.id"))
    pais: Mapped[str] = mapped_column(String(2))
    idioma: Mapped[str] = mapped_column(String(5))
    set_id: Mapped[str] = mapped_column(String(100))
    spl_id: Mapped[str] = mapped_column(String(100))
    version: Mapped[str | None] = mapped_column(String(20))
    fecha_efectiva: Mapped[str | None] = mapped_column(String(10))
    titulo: Mapped[str] = mapped_column(String(300))
    laboratorio: Mapped[str | None] = mapped_column(String(300))
    n_registro: Mapped[str | None] = mapped_column(String(100))
    tipo_producto: Mapped[str | None] = mapped_column(String(100))
    url: Mapped[str] = mapped_column(String(500))
    hash_contenido: Mapped[str] = mapped_column(String(64))
    fecha_extraccion: Mapped[datetime] = mapped_column(FechaUTC())
    fecha_actualizacion: Mapped[datetime] = mapped_column(FechaUTC())

    principio_activo: Mapped[PrincipioActivo] = relationship()
    fuente: Mapped[FuenteDatos] = relationship()
    secciones: Mapped[list["SeccionFicha"]] = relationship(
        back_populates="ficha", cascade="all, delete-orphan", order_by="SeccionFicha.orden"
    )


class SeccionFicha(Base):
    __tablename__ = "secciones_ficha"

    id: Mapped[int] = mapped_column(primary_key=True)
    ficha_id: Mapped[int] = mapped_column(ForeignKey("fichas_tecnicas.id", ondelete="CASCADE"), index=True)
    codigo: Mapped[str] = mapped_column(String(80))
    orden: Mapped[int] = mapped_column(Integer)
    texto: Mapped[str] = mapped_column(Text)

    ficha: Mapped[FichaTecnica] = relationship(back_populates="secciones")


class Referencia(Base):
    __tablename__ = "referencias"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Identificador estable para no duplicar (p. ej. "dailymed:<set_id>:<version>", "pmid:123").
    clave: Mapped[str | None] = mapped_column(String(200), unique=True)
    # ficha_tecnica | articulo | guia | web
    tipo: Mapped[str] = mapped_column(String(30))
    titulo: Mapped[str] = mapped_column(Text)
    autores: Mapped[str | None] = mapped_column(Text)
    publicacion: Mapped[str | None] = mapped_column(String(300))
    fecha_publicacion: Mapped[str | None] = mapped_column(String(10))
    url: Mapped[str | None] = mapped_column(String(500))
    doi: Mapped[str | None] = mapped_column(String(200))
    pmid: Mapped[str | None] = mapped_column(String(20))
    fecha_acceso: Mapped[str] = mapped_column(String(10))
    creado_en: Mapped[datetime] = mapped_column(FechaUTC(), default=ahora)


class Monografia(Base):
    """Versión de la monografía de un principio activo. Solo una versión publicada a la vez."""

    __tablename__ = "monografias"
    __table_args__ = (UniqueConstraint("principio_activo_id", "version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    principio_activo_id: Mapped[int] = mapped_column(ForeignKey("principios_activos.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    # borrador | publicada | archivada
    estado: Mapped[str] = mapped_column(String(20), index=True)
    # Ficha técnica (y su versión) sobre la que se redactó; permite avisar si la ficha cambia.
    ficha_id: Mapped[int | None] = mapped_column(ForeignKey("fichas_tecnicas.id", ondelete="SET NULL"))
    ficha_spl_id_base: Mapped[str | None] = mapped_column(String(100))
    creada_en: Mapped[datetime] = mapped_column(FechaUTC(), default=ahora)
    actualizada_en: Mapped[datetime] = mapped_column(FechaUTC(), default=ahora, onupdate=ahora)
    publicada_en: Mapped[datetime | None] = mapped_column(FechaUTC())
    revisado_por: Mapped[str | None] = mapped_column(String(200))

    principio_activo: Mapped[PrincipioActivo] = relationship()
    ficha: Mapped[FichaTecnica | None] = relationship()
    secciones: Mapped[list["SeccionMonografia"]] = relationship(
        back_populates="monografia", cascade="all, delete-orphan", order_by="SeccionMonografia.orden"
    )
    pautas: Mapped[list["PautaPosologica"]] = relationship(
        back_populates="monografia", cascade="all, delete-orphan", order_by="PautaPosologica.orden"
    )


cita_seccion = Table(
    "citas_seccion",
    Base.metadata,
    Column("seccion_id", ForeignKey("secciones_monografia.id", ondelete="CASCADE"), primary_key=True),
    Column("referencia_id", ForeignKey("referencias.id", ondelete="RESTRICT"), primary_key=True),
    Column("orden", Integer, nullable=False, default=0),
)


class SeccionMonografia(Base):
    __tablename__ = "secciones_monografia"
    __table_args__ = (UniqueConstraint("monografia_id", "tipo"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    monografia_id: Mapped[int] = mapped_column(ForeignKey("monografias.id", ondelete="CASCADE"), index=True)
    tipo: Mapped[str] = mapped_column(String(40))
    orden: Mapped[int] = mapped_column(Integer)
    contenido: Mapped[str | None] = mapped_column(Text)
    idioma: Mapped[str] = mapped_column(String(5), default="es")
    # ficha_importada: texto copiado de la ficha, sin revisar | editor: revisado/redactado por un editor
    origen: Mapped[str] = mapped_column(String(20))
    fecha_verificacion: Mapped[datetime | None] = mapped_column(FechaUTC())

    monografia: Mapped[Monografia] = relationship(back_populates="secciones")
    # Solo lectura: las citas se escriben con orden explícito en services/monografias.py.
    referencias: Mapped[list[Referencia]] = relationship(
        secondary=cita_seccion, order_by=cita_seccion.c.orden, viewonly=True
    )


cita_pauta = Table(
    "citas_pauta",
    Base.metadata,
    Column("pauta_id", ForeignKey("pautas_posologicas.id", ondelete="CASCADE"), primary_key=True),
    Column("referencia_id", ForeignKey("referencias.id", ondelete="RESTRICT"), primary_key=True),
    Column("orden", Integer, nullable=False, default=0),
)


class PautaPosologica(Base):
    """Fila de posología estilo vademécum: una indicación y población con su pauta."""

    __tablename__ = "pautas_posologicas"

    id: Mapped[int] = mapped_column(primary_key=True)
    monografia_id: Mapped[int] = mapped_column(ForeignKey("monografias.id", ondelete="CASCADE"), index=True)
    orden: Mapped[int] = mapped_column(Integer)
    indicacion: Mapped[str] = mapped_column(String(300))
    # adultos | pediatria | geriatria | todas
    poblacion: Mapped[str] = mapped_column(String(20))
    dosis: Mapped[str] = mapped_column(String(300))
    via: Mapped[str] = mapped_column(String(100))
    frecuencia: Mapped[str] = mapped_column(String(200))
    duracion: Mapped[str | None] = mapped_column(String(200))
    dosis_maxima: Mapped[str | None] = mapped_column(String(200))
    notas: Mapped[str | None] = mapped_column(Text)

    monografia: Mapped["Monografia"] = relationship(back_populates="pautas")
    # Solo lectura: las citas se escriben con orden explícito en services/monografias.py.
    referencias: Mapped[list[Referencia]] = relationship(secondary=cita_pauta, order_by=cita_pauta.c.orden, viewonly=True)
