# Farmacosas — backend (FastAPI)

API de consulta de medicamentos. Ver el plan general en [`../docs/PLAN.md`](../docs/PLAN.md).

## Puesta en marcha

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"            # añade ",postgres" para usar PostgreSQL

alembic upgrade head               # crea las tablas (SQLite por defecto: ./farmacosas.db)
python -m app.cli sembrar          # carga los 100 principios activos de data/
python -m app.cli sincronizar-openfda --dci enoxaparina   # productos de EE. UU. (sin --dci: los 100)
python -m app.cli importar-fichas --dci enoxaparina       # ficha técnica de DailyMed + borrador de monografía
python -m app.cli sincronizar-cima --dci enoxaparina      # medicamentos de España (CIMA, AEMPS)
python -m app.cli importar-fichas-cima --dci enoxaparina  # ficha técnica española + borrador en español
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
| `FARMACOSAS_CIMA_PAUSA_SEGUNDOS` | `0.2` | Pausa entre peticiones a CIMA. |

## Endpoints

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/salud` | Estado y aviso legal |
| GET | `/api/v1/buscar?q=&pais=` | Búsqueda por DCI (es/en, sin tildes), código ATC o nombre comercial |
| GET | `/api/v1/principios?grupo=` | Lista de principios activos |
| GET | `/api/v1/principios/{id}` | Detalle con resumen de disponibilidad por país |
| GET | `/api/v1/principios/{id}/productos?pais=&estado=&es_generico=` | Productos comerciales con presentaciones, registro, fuente y fecha de verificación |
| GET | `/api/v1/principios/{id}/monografia` | **Monografía publicada en formato vademécum** (404 si no hay) |
| GET | `/api/v1/principios/{id}/fichas-tecnicas?pais=` | Ficha técnica oficial, texto original por secciones |

Administración (cabecera `X-Admin-Key`):

| Método | Ruta | Descripción |
|---|---|---|
| POST | `/api/v1/admin/sincronizaciones/openfda` | Sincroniza productos de EE. UU. (`{"principio_ids": [..]}` o todos) |
| POST | `/api/v1/admin/sincronizaciones/fichas-openfda` | Importa fichas técnicas de EE. UU. y actualiza borradores |
| POST | `/api/v1/admin/sincronizaciones/cima` | Sincroniza medicamentos de España y después importa sus fichas técnicas |
| GET | `/api/v1/admin/sincronizaciones` | Historial de sincronizaciones e importaciones |
| GET | `/api/v1/admin/principios/{id}/borrador` | Borrador con errores que impiden publicar y avisos de formato |
| PUT | `/api/v1/admin/monografias/{id}/secciones/{tipo}` | Edita una sección (`contenido`, `idioma`, `referencia_ids`) |
| PUT | `/api/v1/admin/monografias/{id}/pautas` | Sustituye la tabla de posología |
| POST | `/api/v1/admin/monografias/{id}/publicar` | Publica (`revisor` y `checklist` completa) |
| POST / GET | `/api/v1/admin/referencias` | Crea (deduplica por PMID/DOI) y busca referencias |

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

- Columnas del CSV para openFDA: `openfda_ingredientes`, `openfda_filtro_nombre`, `openfda_vias`
  (vías preferidas para la ficha, separadas por `|`) y `openfda_ficha_set_id` (ficha fijada a mano).
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

## Conector CIMA (España)

Fuente: [CIMA](https://cima.aemps.es/) de la AEMPS, API REST pública.

- Los medicamentos se buscan por el **código ATC** del principio activo (columna `atc` del CSV).
  El ATC ya distingue el uso: insulina regular (A10AB01) frente a NPH (A10AC01), AAS antiagregante
  (B01AC06) frente a analgésico, hidrocortisona sistémica (H02AB09) frente a tópica, etc. Con varios
  códigos (p. ej. metronidazol `J01XD01 / P01AB01`) se consultan todos.
- Cada medicamento (número de registro) es un producto: marca extraída del nombre (`CLEXANE`),
  nombre completo, titular, vía, forma, composición, EFG, condición de prescripción, **problema de
  suministro** y estado (`vigente`, `no_comercializado`, `suspendido`, `revocado`, `no_listado`).
- Las presentaciones (código nacional) y la composición exacta se piden al detalle solo para
  medicamentos nuevos o que cambiaron, así que las sincronizaciones posteriores son rápidas.
- **Ficha técnica en español** de referencia: se prefiere el medicamento comercializado, con receta, no
  EFG y autorizado primero (el original: Clexane, Nolotil, Augmentine, Flagyl...). Se importan las
  secciones 4.x, 5.1 y 5.2, convertidas a texto plano. Si la fecha de la ficha no cambió, no se
  descargan de nuevo.
- CIMA corta a veces la conexión; el cliente reintenta con espera progresiva.

La ficha española es la **base preferida del borrador de monografía**, porque ya está en español:
el editor solo tiene que resumirla. Correspondencia con las secciones del vademécum: 4.1 → indicaciones;
4.2 → posología (subsección «Forma de administración» → modo de administración); 4.3 → contraindicaciones;
4.4 → advertencias; 4.2 + 4.4 filtradas → insuficiencia renal/hepática; 4.5 → interacciones;
4.6 → embarazo y lactancia; 4.8 → reacciones adversas; 4.9 → sobredosis; 5.1 → mecanismo de acción.

## Monografías en formato vademécum

Cada principio activo tiene una monografía breve y estructurada, en este orden:

alerta destacada · mecanismo de acción · indicaciones · **posología (tabla de pautas)** ·
modo de administración · contraindicaciones · advertencias y precauciones · insuficiencia renal ·
insuficiencia hepática · interacciones · embarazo · lactancia · reacciones adversas · sobredosis ·
consideraciones perioperatorias

La vista pública añade la cabecera (DCI, ATC, grupo), los **nombres comerciales por país**, la
fecha de última actualización, quién la revisó, un indicador de frescura (🟢 < 6 meses,
🟡 6–12 meses, 🔴 > 12 meses **o la ficha oficial cambió después de la revisión**) y las
**referencias numeradas al final** (estilo Vancouver), citadas como `[n]` en cada sección y pauta.

La **posología** se publica como pautas estructuradas: indicación, población (adultos, pediatría,
geriatría, todas), dosis, vía, frecuencia, duración, dosis máxima y notas, cada una con sus referencias.

### Flujo editorial

1. `importar-fichas-cima` (España, en español) o `importar-fichas` (EE. UU.) descargan la ficha técnica
   de referencia y generan (o actualizan) un **borrador**, preferentemente sobre la ficha española:
   cada sección se rellena con el texto original de la ficha, con la ficha ya citada. Para
   insuficiencia renal/hepática, embarazo y lactancia se extraen solo las frases pertinentes.
2. El editor reescribe cada sección en español, en formato breve, y carga las pautas.
3. Para **publicar**, el sistema exige: la checklist completa (dosis, ajustes, contraindicaciones,
   interacciones, referencias), que no quede texto importado sin revisar, las secciones obligatorias
   (indicaciones, contraindicaciones, advertencias), al menos una pauta y referencias en cada
   sección y pauta. Las secciones de más de 1500 caracteres o no escritas en español generan
   avisos, sin bloquear la publicación.
4. Si la ficha oficial cambia, la monografía publicada pasa a 🔴 y se crea un borrador nuevo que
   conserva lo revisado (y las pautas) y rellena desde la ficha nueva lo que no lo estaba.
   Publicarlo archiva la versión anterior.

### Elección de la ficha de referencia

Entre los productos de EE. UU. del principio activo se prefiere: (1) una vía sistémica (o las
de `openfda_vias`); (2) con receta y de marca (NDA/BLA); (3) el comercializado primero. Se descartan
así colirios, parches, implantes o reformulaciones recientes. Cuando la elección automática no es
la adecuada para un vademécum, se fija a mano con `openfda_ficha_set_id` (set_id de DailyMed); si esa
ficha deja de existir se vuelve a la selección automática. Hoy hay 12 fichas fijadas a mano
(p. ej. Lipitor, Coreg, Humulin R, Marcaine, Xylocaine, Adrenalin).

## Pruebas

```bash
pytest
```

Las pruebas no acceden a la red: openFDA se simula.
