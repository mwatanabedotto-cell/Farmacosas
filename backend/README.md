# Farmacosas — backend (FastAPI)

API de consulta de medicamentos. Ver el plan general en [`../docs/PLAN.md`](../docs/PLAN.md).

## Puesta en marcha

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"            # añade ",postgres" para usar PostgreSQL

alembic upgrade head               # crea las tablas (SQLite por defecto: ./farmacosas.db)
python -m app.cli sembrar          # carga los 100 principios activos de data/
python -m app.cli sincronizar-openfda --dci enoxaparina   # sin --dci sincroniza los 100
uvicorn app.main:app --reload      # documentación interactiva en http://localhost:8000/docs
```

## Configuración (variables de entorno o `.env`)

| Variable | Por defecto | Descripción |
|---|---|---|
| `FARMACOSAS_DATABASE_URL` | `sqlite:///./farmacosas.db` | P. ej. `postgresql+psycopg://usuario:clave@host/farmacosas` |
| `FARMACOSAS_ADMIN_API_KEY` | — | Clave de la cabecera `X-Admin-Key`. Sin ella, `/admin` está deshabilitado. |
| `FARMACOSAS_OPENFDA_API_KEY` | — | Recomendada: sin clave, openFDA permite 1000 peticiones/día; con clave, 120 000 ([solicitar](https://open.fda.gov/apis/authentication/)). |
| `FARMACOSAS_OPENFDA_MAX_RESULTADOS` | `5000` | Máximo de productos descargados por principio activo. |
| `FARMACOSAS_OPENFDA_PAUSA_SEGUNDOS` | `0.3` | Pausa entre páginas. |

## Endpoints

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/salud` | Estado y aviso legal |
| GET | `/api/v1/buscar?q=&pais=` | Búsqueda por DCI (es/en, sin tildes), código ATC o nombre comercial |
| GET | `/api/v1/principios?grupo=` | Lista de principios activos |
| GET | `/api/v1/principios/{id}` | Detalle con resumen de disponibilidad por país |
| GET | `/api/v1/principios/{id}/productos?pais=&estado=&es_generico=` | Productos comerciales con presentaciones, registro, fuente y fecha de verificación |
| POST | `/api/v1/admin/sincronizaciones/openfda` | Lanza una sincronización (`{"principio_ids": [..]}` o todos) — requiere `X-Admin-Key` |
| GET | `/api/v1/admin/sincronizaciones` | Historial de sincronizaciones — requiere `X-Admin-Key` |

## Conector openFDA (EE. UU.)

Fuente: [NDC Directory](https://open.fda.gov/apis/drug/ndc/) (dominio público).

- Cada principio activo tiene en el CSV la columna `openfda_ingredientes` con el nombre
  que usa la FDA: componentes separados por `+` y alternativas por `|`
  (p. ej. `amoxicillin+clavulanate|clavulanic acid`, `acetaminophen`, `albuterol|salbutamol`).
- Solo se asocian productos cuyos ingredientes coinciden **exactamente** con los componentes
  del principio activo; se aceptan sales (`METOPROLOL TARTRATE`), pero se excluyen las
  combinaciones con otros fármacos (p. ej. losartán + hidroclorotiazida no aparece en losartán).
- Se descartan productos homeopáticos y principios activos a granel.
- Un NDC listado en varias fichas (SPL) se fusiona en un solo producto.
- Genérico: `ANDA` y `NDA AUTHORIZED GENERIC`; marca: `NDA` y `BLA`.
- Cada producto guarda `fecha_extraccion` (última verificación) y `fecha_actualizacion`
  (último cambio real, detectado por hash) y el enlace a su ficha en DailyMed.
- Si un producto deja de aparecer en una descarga **completa**, pasa a `no_listado` (no se borra).
  Si la descarga se trunca por el límite, no se marca ninguna baja.

- Columna opcional `openfda_filtro_nombre`: expresión regular sobre el nombre comercial
  (con `!` delante, excluye). Se usa cuando la FDA registra productos distintos con el mismo
  ingrediente: insulina humana regular, NPH y las mezclas 70/30 figuran todas como
  `INSULIN HUMAN`. Regular: `!\bN\b|\d+/\d+`; NPH: `\bN\b`. Las mezclas 70/30 (ATC A10AD01)
  no pertenecen a ninguno de los dos.
- Si un producto sigue en openFDA pero deja de cumplir los criterios (p. ej. por un filtro
  nuevo), se **desvincula** del principio activo; no se marca como `no_listado`.

### Resultado de la primera sincronización completa (2026-09-27)

Los 100 principios activos se sincronizaron sin errores ni truncamientos (~22 000 productos).
Sin productos en EE. UU., lo cual es correcto: metamizol (solo principio activo a granel)
y butilbromuro de hioscina (no aprobados en EE. UU.). Sulfato ferroso tiene 1 producto porque
en EE. UU. el hierro oral se comercializa mayoritariamente como suplemento dietético, fuera del NDC.

## Pruebas

```bash
pytest
```

Las pruebas no acceden a la red: openFDA se simula.
