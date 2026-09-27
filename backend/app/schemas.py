from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


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
