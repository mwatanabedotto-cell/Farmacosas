from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Esquema(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class PrincipioResumen(Esquema):
    id: int
    dci_es: str
    dci_en: str
    atc: str
    grupo: str


class DisponibilidadPais(BaseModel):
    pais: str
    productos: int
    vigentes: int
    genericos: int
    ultima_verificacion: datetime | None


class PrincipioDetalle(PrincipioResumen):
    actualizado_en: datetime
    disponibilidad: list[DisponibilidadPais]


class ListaPrincipios(BaseModel):
    total: int
    resultados: list[PrincipioResumen]


class PresentacionSalida(Esquema):
    id_externo: str | None
    descripcion: str | None
    fecha_inicio_comercializacion: str | None


class FuenteSalida(Esquema):
    codigo: str
    nombre: str
    agencia: str
    url: str


class ProductoSalida(Esquema):
    id: int
    pais: str
    nombre_comercial: str
    laboratorio: str | None
    forma_farmaceutica: str | None
    via: str | None
    composicion: str | None
    tipo_producto: str | None
    categoria_registro: str | None
    n_registro: str | None
    es_generico: bool | None
    estado: str
    url_fuente: str | None
    url_ficha: str | None
    fecha_extraccion: datetime
    fecha_actualizacion: datetime
    fuente: FuenteSalida
    presentaciones: list[PresentacionSalida]


class ListaProductos(BaseModel):
    total: int
    resultados: list[ProductoSalida]


class ResultadoBusqueda(BaseModel):
    tipo: Literal["principio_activo", "nombre_comercial"]
    coincidencia: Literal["dci", "atc", "nombre_comercial"]
    principio: PrincipioResumen
    nombre_comercial: str | None = None
    pais: str | None = None


class RespuestaBusqueda(BaseModel):
    consulta: str
    resultados: list[ResultadoBusqueda]


class PeticionSincronizacion(BaseModel):
    principio_ids: list[int] | None = None


class SincronizacionSalida(Esquema):
    id: int
    principio_activo_id: int | None
    iniciada_en: datetime
    finalizada_en: datetime | None
    estado: str
    consulta: str | None
    n_recibidos: int
    n_creados: int
    n_actualizados: int
    n_sin_cambios: int
    n_no_listados: int
    mensaje: str | None


class SeccionFichaSalida(BaseModel):
    codigo: str
    titulo: str
    texto: str


class FichaBase(Esquema):
    pais: str
    idioma: str
    titulo: str
    laboratorio: str | None
    n_registro: str | None
    tipo_producto: str | None
    set_id: str
    version: str | None
    fecha_efectiva: str | None
    url: str
    fecha_extraccion: datetime
    fecha_actualizacion: datetime
    fuente: FuenteSalida


class FichaSalida(FichaBase):
    aviso: str
    secciones: list[SeccionFichaSalida]


class Frescura(BaseModel):
    nivel: Literal["verde", "amarillo", "rojo"]
    motivo: str


class SeccionPublica(BaseModel):
    tipo: str
    titulo: str
    contenido: str
    idioma: str
    fecha_verificacion: datetime | None
    citas: list[int]


class ReferenciaNumerada(BaseModel):
    numero: int
    texto: str
    url: str | None
    doi: str | None
    pmid: str | None
    fecha_acceso: str


Poblacion = Literal["adultos", "pediatria", "geriatria", "todas"]


class PautaDatos(BaseModel):
    indicacion: str = Field(min_length=2, max_length=300)
    poblacion: Poblacion
    dosis: str = Field(min_length=1, max_length=300, description="p. ej. «40 mg» o «1 mg/kg»")
    via: str = Field(min_length=2, max_length=100)
    frecuencia: str = Field(min_length=2, max_length=200, description="p. ej. «cada 24 h»")
    duracion: str | None = Field(default=None, max_length=200)
    dosis_maxima: str | None = Field(default=None, max_length=200)
    notas: str | None = Field(default=None, max_length=2000)


class PautaEntrada(PautaDatos):
    referencia_ids: list[int] = []


class PautaBorrador(PautaDatos):
    referencia_ids: list[int]


class PautaPublica(PautaDatos):
    citas: list[int]


class NombresPais(BaseModel):
    pais: str
    marcas: list[str]
    productos: int
    genericos: int


class MonografiaPublica(BaseModel):
    """Monografía en formato vademécum."""

    principio_activo_id: int
    dci: str
    dci_en: str
    atc: str
    grupo: str
    version: int
    ultima_actualizacion: datetime
    revisado_por: str | None
    frescura: Frescura
    nombres_comerciales: list[NombresPais]
    secciones: list[SeccionPublica]
    pautas: list[PautaPublica]
    referencias: list[ReferenciaNumerada]
    aviso: str


class ReferenciaEntrada(BaseModel):
    tipo: Literal["articulo", "guia", "web", "ficha_tecnica"]
    titulo: str = Field(min_length=3)
    autores: str | None = None
    publicacion: str | None = None
    fecha_publicacion: str | None = Field(default=None, pattern=r"^\d{4}(-\d{2}(-\d{2})?)?$")
    url: str | None = Field(default=None, pattern=r"^https?://")
    doi: str | None = None
    pmid: str | None = Field(default=None, pattern=r"^\d{1,10}$")


class ReferenciaSalida(Esquema):
    id: int
    clave: str | None
    tipo: str
    titulo: str
    autores: str | None
    publicacion: str | None
    fecha_publicacion: str | None
    url: str | None
    doi: str | None
    pmid: str | None
    fecha_acceso: str
    texto: str = ""


class SeccionBorrador(BaseModel):
    tipo: str
    titulo: str
    obligatoria: bool
    origen: str
    idioma: str
    contenido: str | None
    fecha_verificacion: datetime | None
    referencia_ids: list[int]


class MonografiaBorrador(BaseModel):
    id: int
    principio_activo_id: int
    version: int
    estado: str
    ficha_spl_id_base: str | None
    ficha_cambiada: bool
    actualizada_en: datetime
    publicable: bool
    errores_publicacion: list[str]
    avisos_formato: list[str]
    checklist: dict[str, str]
    secciones: list[SeccionBorrador]
    pautas: list[PautaBorrador]


class EdicionSeccion(BaseModel):
    contenido: str | None = Field(default=None, max_length=50000)
    idioma: Literal["es", "en", "pt"] = "es"
    referencia_ids: list[int] = []


class PeticionPublicacion(BaseModel):
    revisor: str = Field(min_length=3, max_length=200)
    checklist: dict[str, bool]
