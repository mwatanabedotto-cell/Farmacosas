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
python -m app.cli sincronizar-invima --dci enoxaparina    # medicamentos de Colombia (CUM de INVIMA)
python -m app.cli sincronizar-liname                      # LINAME de Bolivia (AGEMED)
python -m app.cli sincronizar-cofepris                    # registros sanitarios de México (listados COFEPRIS)
python -m app.cli sincronizar-pami                        # medicamentos de Argentina (listado de PAMI)
python -m app.cli regenerar-borradores                    # vuelve a extraer los borradores (respeta lo revisado)
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
| `FARMACOSAS_DATOSGOV_APP_TOKEN` | — | Token de datos.gov.co (opcional). |
| `FARMACOSAS_INVIMA_PAUSA_SEGUNDOS` | `0.2` | Pausa entre páginas de datos.gov.co. |

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
| GET | `/api/v1/principios/{id}/listas-esenciales?pais=` | Entradas en listas nacionales de medicamentos esenciales (LINAME) |

Administración (cabecera `X-Admin-Key`):

| Método | Ruta | Descripción |
|---|---|---|
| POST | `/api/v1/admin/sincronizaciones/openfda` | Sincroniza productos de EE. UU. (`{"principio_ids": [..]}` o todos) |
| POST | `/api/v1/admin/sincronizaciones/fichas-openfda` | Importa fichas técnicas de EE. UU. y actualiza borradores |
| POST | `/api/v1/admin/sincronizaciones/cima` | Sincroniza medicamentos de España y después importa sus fichas técnicas |
| POST | `/api/v1/admin/sincronizaciones/invima` | Sincroniza medicamentos de Colombia (CUM) |
| POST | `/api/v1/admin/sincronizaciones/liname` | Descarga la LINAME de Bolivia |
| POST | `/api/v1/admin/sincronizaciones/cofepris` | Sincroniza registros sanitarios de México |
| POST | `/api/v1/admin/sincronizaciones/pami` | Sincroniza medicamentos de Argentina (PAMI) |
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

- Columna `sinonimos` del CSV: nombres locales separados por `|` (p. ej. `acetaminofén`, `dipirona`,
  `epinefrina`); se usan en la búsqueda y en la heurística de genéricos de INVIMA.
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

## Conector INVIMA (Colombia)

Fuente: Código Único de Medicamentos (CUM) publicado por INVIMA en [datos.gov.co](https://www.datos.gov.co/)
(API Socrata). Se usan los conjuntos **vigentes** (`i7cb-raxc`) y **en trámite de renovación** (`vgr4-gemg`).

- **Solo conjuntos oficiales:** en datos.gov.co hay copias subidas por particulares con la misma
  atribución a INVIMA. El conector usa identificadores fijos y **comprueba en cada ejecución que el
  propietario del conjunto sea Invima**; si no, se detiene con error.
- Búsqueda por código ATC, como en CIMA. Las filas (registro × presentación × principio activo × rol)
  se agrupan por **registro sanitario** (producto) y **CUM** (presentación). Se excluyen las muestras médicas.
- Estado: `vigente` si el registro tiene al menos un CUM activo; si no, `no_comercializado`.
- **Genérico (heurística):** el CUM no lo indica. Con ® o ™ es marca; si el nombre empieza por la
  denominación común o un sinónimo local (columna `sinonimos`: *dipirona*, *acetaminofén*...), genérico.
- La clasificación ATC es la de INVIMA. Hay errores de origen: p. ej. «INSULEX ® N» (NPH) figura como
  insulina regular (A10AB01). No se corrigen automáticamente.
- Token opcional `FARMACOSAS_DATOSGOV_APP_TOKEN` para ampliar el límite de peticiones de datos.gov.co.
- INVIMA no publica fichas técnicas por API: la monografía se apoya en las de España y EE. UU.

## Conector COFEPRIS (México)

El buscador y el visor de registros de COFEPRIS y datos.gob.mx rechazan el acceso automatizado (403 del propio
servidor), así que se usan los **listados oficiales en PDF** que COFEPRIS publica en
[gob.mx](https://www.gob.mx/cofepris/documentos/registros-sanitarios-medicamentos):
registros de medicamentos alopáticos **expedidos** cada año desde 2015 (formato tabular homogéneo; hoy
~3900 registros de 2015–2026) y los registros **revocados** y **cancelados**.

- Por registro: número (`001M2026 SSA`), titular, denominación distintiva (marca) y genérica, forma
  farmacéutica, **condición de venta** según el art. 226 de la LGS (fracción I–VI: receta especial,
  receta retenida, receta médica, venta libre...) y fecha de vigencia.
- Estado: `revocado`/`cancelado` según las listas oficiales; `vigente` si la vigencia no ha vencido;
  **`vigencia_por_confirmar`** si ya venció (pudo renovarse, pero COFEPRIS no publica las prórrogas).
- Sin ATC en origen: asignación por **denominación genérica** (componentes exactos, sales como
  «Clorhidrato de tramadol», sinónimos mexicanos) eligiendo el principio activo más específico
  («Insulina humana isófana» → NPH). Se excluyen formas de uso local (cremas, colirios, óvulos...).
- **Limitación de cobertura:** solo registros expedidos desde 2015; los medicamentos registrados antes y
  renovados (muchos originales) no aparecen. Para completarlos: carga asistida.
- Los PDF se descargan una vez por sincronización (unos 15 archivos).

## Conector PAMI (Argentina)

ANMAT no ofrece una fuente abierta y actualizada: el Vademécum Nacional de Medicamentos en datos.gob.ar es de
2018 y la web del VNM no responde por HTTPS desde el entorno de desarrollo. Se usa el **listado oficial de
medicamentos que cubre PAMI** (INSSJP), publicado cada semana en datos.gob.ar
([medicamentos-para-entidades](https://datos.gob.ar/dataset/medicamentos-para-entidades)).

- Por presentación: código **AlfaBeta**, principio activo, marca, presentación, laboratorio y **cobertura
  PAMI**. Se agrupan en productos por marca + laboratorio + principio activo. Los precios se ignoran a
  propósito (cambian cada semana y no son objeto del vademécum).
- Se comprueba que el conjunto lo publique PAMI y que el archivo venga de `datos.pami.org.ar` (por HTTPS).
- Sin ATC: asignación por nombre, entendiendo la **notación invertida de AlfaBeta** («acetilsalicílico,ác.»,
  «potasio,cloruro», «sodio,divalproato») y los nombres argentinos (ciprofloxacina, amlodipina, dipirona...).
  Se excluyen presentaciones de uso local («sol.oft.», «ung.», cremas...).
- **Desempate por marca:** si una denominación genérica encaja por igual con varios principios activos
  («insulina humana»), decide el filtro de nombre comercial (`openfda_filtro_nombre`): Insulatard o
  Densulin N → NPH; Densulin R → regular. Se aplica también en México.
- **Alcance:** es lo que cubre PAMI, no el registro completo de ANMAT: faltan sobre todo fármacos de uso
  hospitalario (propofol, rocuronio, noradrenalina...) y no hay número de certificado ANMAT.

## Bolivia (AGEMED): Lista Nacional de Medicamentos Esenciales

AGEMED **no publica de forma abierta el registro sanitario** (marcas y números de registro): su buscador
exige reCAPTCHA, que no se automatiza, y datos.gob.bo rechaza el acceso (403). Sí publica la
**LINAME vigente en Excel** (hoy LINAME 2026-2027, actualizada el 07-04-2026), que se importa como
**lista esencial**, no como productos comerciales:

- Por entrada: código LINAME, medicamento, forma farmacéutica, concentración, ATC, **uso restringido (R)**
  y, para antibióticos, la **clasificación AWaRe de la OMS** (Acceso / Vigilancia / Reserva).
- El conector toma de la web de AGEMED el enlace a la versión más reciente (solo acepta archivos de
  agemed.gob.bo) y marca como `excluido` lo que desaparece en una versión nueva.
- Asignación a principios activos por ATC (distingue usos: aciclovir crema no es aciclovir sistémico). Si el
  ATC de la lista no coincide o está incompleto, por nombre idéntico y mismo grupo terapéutico (3 primeros
  caracteres del ATC); así se recupera la cefazolina, que la LINAME codifica como J01DE04.
- Errores de la fuente que no se corrigen solos: la «insulina zinc cristalina» (regular) figura como NPH
  (A10AC01), y «Heparina de bajo peso molecular» (B01AB**) no se asigna a enoxaparina por ser genérica.
- Se muestra en el detalle del principio activo, en la monografía (`listas_esenciales`) y en
  `GET /api/v1/principios/{id}/listas-esenciales`.
- Las marcas comerciales de Bolivia quedan para la **carga asistida** prevista en el plan.

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
