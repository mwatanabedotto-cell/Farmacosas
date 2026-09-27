from fastapi import FastAPI

from app.api import admin, busqueda, principios

AVISO = (
    "La información de Farmacosas apoya, pero no sustituye, el juicio clínico "
    "ni la ficha técnica aprobada en cada país."
)

app = FastAPI(
    title="Farmacosas API",
    version="0.1.0",
    description=f"Consulta de medicamentos para médicos.\n\n**Aviso:** {AVISO}",
)

app.include_router(busqueda.router, prefix="/api/v1")
app.include_router(principios.router, prefix="/api/v1")
app.include_router(admin.router, prefix="/api/v1")


@app.middleware("http")
async def cabeceras_seguridad(request, call_next):
    respuesta = await call_next(request)
    respuesta.headers.setdefault("X-Content-Type-Options", "nosniff")
    respuesta.headers.setdefault("X-Frame-Options", "DENY")
    respuesta.headers.setdefault("Referrer-Policy", "no-referrer")
    return respuesta


@app.get("/salud", tags=["sistema"])
def salud() -> dict:
    return {"estado": "ok", "aviso": AVISO}
