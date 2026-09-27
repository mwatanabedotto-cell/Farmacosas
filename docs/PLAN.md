# Farmacosas — Plan de la aplicación

> Consulta de medicamentos para médicos: información confiable, trazable y segura,
> con fecha de última actualización, referencias al final de cada monografía y
> disponibilidad por nombre genérico (DCI) y nombre comercial por región.

---

## 0. Decisiones tomadas (2026-09-27)

| Tema | Decisión |
|---|---|
| **Países prioritarios** | 🇺🇸 Estados Unidos · 🇲🇽 México · 🇦🇷 Argentina · 🇨🇴 Colombia · 🇪🇸 España · 🇵🇪 Perú · 🇨🇱 Chile · 🇧🇴 Bolivia |
| **Estados Unidos** | Se incluye como país prioritario por su volumen de investigación y la apertura de sus datos: disponibilidad (FDA), fichas técnicas (DailyMed), farmacovigilancia (FAERS) y evidencia (PubMed, ClinicalTrials.gov). Ver §3.4. |
| **Países secundarios** (fases posteriores) | Brasil, Portugal, Unión Europea — EMA se usa desde el inicio **solo como fuente de contenido clínico**, no de disponibilidad. |
| **Contenido inicial** | 100 principios activos de uso frecuente → [`data/principios_activos_iniciales.csv`](../data/principios_activos_iniciales.csv) (propuesta a validar). |
| **Backend** | **Python + FastAPI**. |
| **Equipo editorial** | Inicialmente **una sola persona** (propietario/administrador). El sistema de roles permite invitar colaboradores después. |

---

## 1. Objetivo y alcance

**Problema.** El médico necesita, en segundos y en el punto de atención, datos
fiables de un fármaco (dosis, ajustes, contraindicaciones, interacciones) y saber
**cómo se llama y si está disponible en su país**. Las fuentes oficiales están
dispersas, en distintos idiomas y con formatos heterogéneos.

**Qué hace la app (MVP):**

1. Buscar un medicamento por **nombre genérico (DCI/INN)**, **nombre comercial**
   o **código ATC**, con tolerancia a errores de escritura.
2. Mostrar una **monografía estructurada** del principio activo.
3. Mostrar **fecha de última actualización** (de la monografía y de cada sección)
   y **fecha de verificación de la fuente**.
4. Listar **referencias numeradas al final** de la monografía, enlazadas a la
   fuente original.
5. Mostrar una tabla de **disponibilidad por región**: nombre comercial,
   laboratorio titular, presentaciones, número de registro sanitario y estado.

**Fuera de alcance del MVP:** calculadoras de dosis avanzadas, prescripción
electrónica, datos de pacientes (la app **no almacena datos clínicos de
pacientes**).

**Usuarios:** médicos (incluidos residentes y médicos en proceso de reválida),
farmacéuticos y estudiantes de medicina con acceso limitado.

---

## 2. Principios de confiabilidad

| Principio | Cómo se implementa |
|---|---|
| **Fuente primaria siempre** | Cada dato proviene de una ficha técnica/prospecto oficial, guía clínica o artículo indexado. Nada sin referencia. |
| **Trazabilidad por campo** | Cada sección guarda `fuente_id`, `fecha_fuente`, `fecha_verificación` y `revisor`. |
| **Revisión humana** | Ningún contenido importado automáticamente se publica sin aprobación humana. **Fase inicial (un solo editor):** *borrador → autorrevisión con checklist → publicado*, y la monografía muestra "Revisado por 1 revisor". **Con colaboradores:** se activa la doble revisión *borrador → revisión farmacéutica → revisión médica → publicado*, configurable por monografía. |
| **Versionado** | Todas las monografías son versionadas (historial consultable, diff entre versiones). |
| **Frescura visible** | Aviso visual si una sección no se verifica hace > 12 meses o si la fuente oficial cambió después de la última revisión. |
| **Alertas de seguridad** | Integración de alertas de farmacovigilancia (FDA, AEMPS, COFEPRIS, ANMAT, INVIMA, ISP, DIGEMID; EMA más adelante) destacadas arriba de la monografía. |
| **Descargo de responsabilidad** | Texto claro: la información apoya, no sustituye, el juicio clínico ni la ficha técnica local. |

---

## 3. Fuentes de datos

### 3.1 Terminología y clasificación
- **WHO ATC/DDD** — clasificación ATC y dosis diaria definida.
- **INN/DCI (OMS)** — nombre genérico internacional.
- **RxNorm (NLM)** — normalización de nombres y mapeo genérico ↔ comercial (EE. UU.).
- **SNOMED CT** (opcional, según licencias por país).

### 3.2 Monografías / fichas técnicas (contenido clínico)
- **DailyMed / openFDA** (EE. UU.) — etiquetas SPL, API pública.
- **EMA — EPAR / SmPC** (Unión Europea).
- **AEMPS — CIMA** (España) — API REST pública con ficha técnica y prospecto.
- Guías clínicas y literatura: **PubMed**, Cochrane, guías de sociedades.

### 3.3 Registro y disponibilidad por región (nombres comerciales)

**Países prioritarios** (en orden sugerido de implementación, según facilidad de acceso a los datos):

| # | País | Agencia | Fuente | Acceso | Genérico / intercambiable |
|---|---|---|---|---|---|
| 1 | Estados Unidos | FDA | openFDA (NDC Directory, Drugs@FDA), Orange Book, DailyMed; RxNorm para mapear nombres | API REST pública | Equivalencia terapéutica (códigos TE del Orange Book: AB, etc.) |
| 1 | España | AEMPS | CIMA | API REST pública | EFG |
| 2 | Colombia | INVIMA | Código Único de Medicamentos (CUM) en datos.gov.co | API de datos abiertos | — |
| 3 | Argentina | ANMAT | Vademécum Nacional de Medicamentos | Consulta web / descargas | — |
| 4 | Chile | ISP | Registro sanitario de productos farmacéuticos | Consulta web | Bioequivalente |
| 5 | Perú | DIGEMID | Registro sanitario / Observatorio de productos farmacéuticos | Consulta web | — |
| 6 | México | COFEPRIS | Registros sanitarios; Compendio Nacional de Insumos (CSG) | Consulta web / PDF | GI (genérico intercambiable) |
| 7 | Bolivia | AGEMED | LINAME (lista nacional de medicamentos esenciales) en Excel; el registro sanitario no es público (buscador con reCAPTCHA) | Descarga oficial (LINAME); marcas por carga asistida | — |

**Secundarios (fases posteriores):**

| Región | Agencia | Fuente |
|---|---|---|
| Brasil | ANVISA | Consulta de registros / Bulário Eletrônico |
| Unión Europea | EMA | Medicamentos autorizados centralizadamente |
| Portugal | INFARMED | Infomed |

> Si una agencia no ofrece API ni datos descargables, la disponibilidad de ese
> país se carga **de forma asistida**: el editor registra el producto con el
> enlace y la fecha de la consulta oficial, y un job periódico avisa cuándo toca
> volver a verificarlo.

> Cada conector (ETL) registra la **fecha y hora de extracción**; si la agencia
> no ofrece API se usan descargas oficiales de datos abiertos. Se revisarán los
> términos de uso/licencia de cada fuente antes de integrarla.

### 3.4 Investigación y evidencia (foco en EE. UU.)

Estados Unidos concentra una gran parte de los ensayos clínicos y de la
literatura biomédica indexada, y publica casi todo por API pública:

| Fuente | Qué aporta | Acceso |
|---|---|---|
| **PubMed / MEDLINE (NLM)** | Ensayos clínicos aleatorizados, metaanálisis y revisiones; PMID para las referencias | API E-utilities |
| **ClinicalTrials.gov** | Ensayos en curso y completados por principio activo, con resultados publicados | API v2 |
| **DailyMed / openFDA drug label** | Ficha técnica oficial (SPL): posología, *boxed warnings*, interacciones | API REST |
| **openFDA FAERS** | Notificaciones de reacciones adversas posautorización | API REST |
| **FDA Drug Safety Communications** | Alertas de seguridad oficiales | Web / RSS |
| **RxNorm / RxClass (NLM)** | Normalización de nombres, clases terapéuticas, mapeo con ATC | API REST |

**Cómo se usa en la app:**
- **Sección "Evidencia reciente"** en cada monografía: metaanálisis y ensayos
  aleatorizados relevantes (filtros de PubMed por tipo de publicación) y ensayos
  activos en ClinicalTrials.gov. Un job semanal propone novedades, que **el
  editor revisa antes de publicarlas**.
- Las referencias de PubMed se completan automáticamente (autores, revista,
  año, DOI, PMID) a partir del identificador.
- Las diferencias de indicación o dosis entre la FDA y la agencia local se
  marcan explícitamente (p. ej. "Aprobado por FDA; no aprobado en México").

---

## 4. Contenido de la monografía (formato vademécum)

La monografía es **una ficha breve de vademécum**, en español y pensada para leerse en segundos,
no una copia de la ficha técnica. Orden:

1. **Cabecera**: DCI, ATC, grupo terapéutico, **nombres comerciales por país**, fecha de última
   actualización, revisor e indicador de frescura.
2. **Alerta destacada** (*boxed warning*), si existe.
3. **Mecanismo de acción**.
4. **Indicaciones** (aprobadas; *off-label* claramente marcado).
5. **Posología en tabla de pautas**: indicación · población · dosis · vía · frecuencia · duración ·
   dosis máxima · notas.
6. **Modo de administración**.
7. **Contraindicaciones**.
8. **Advertencias y precauciones**.
9. **Insuficiencia renal** (por ClCr/TFG, diálisis) · 10. **Insuficiencia hepática** (Child-Pugh).
11. **Interacciones**.
12. **Embarazo** · 13. **Lactancia**.
14. **Reacciones adversas**.
15. **Sobredosis y antídoto**.
16. **Consideraciones perioperatorias** (suspensión/reinicio, anestesia neuraxial…).
    *Fase 2:* **Evidencia reciente** (PubMed / ClinicalTrials.gov, ver §3.4).
17. **Referencias** numeradas al final, estilo Vancouver, con enlace, DOI/PMID y fecha de consulta.

Cada sección y cada pauta muestran sus citas `[1]`, `[2]`, y cada sección su fecha de verificación.
La ficha técnica oficial completa (texto original) queda accesible aparte, como fuente.

## 5. Disponibilidad por región

Vista de tabla filtrable por país (el país del usuario viene preseleccionado):

| País | Nombre comercial | Laboratorio | Presentación | Registro | Estado | Verificado |
|---|---|---|---|---|---|---|
| 🇲🇽 MX | Ejemplo® | Lab X | comp. 500 mg | 123M2020 SSA | Vigente | 2026-09-01 |
| 🇨🇴 CO | Ejemplo® | Lab Y | sol. iny. 1 g | INVIMA 2020M-000000 | Vigente | 2026-08-20 |

- Indica si existe **genérico/intercambiable** (p. ej. *GI* COFEPRIS,
  *EFG* AEMPS, *bioequivalente* ISP).
- Estados: vigente, suspendido, cancelado, desabastecimiento.
- Búsqueda inversa: al escribir un nombre comercial de otro país, la app muestra
  el principio activo y **sus equivalentes en el país del usuario**.

---

## 6. Modelo de datos (resumen)

```
PrincipioActivo (id, dci, sinónimos[], atc[], grupo_terapéutico)
  └─ Monografía (id, principio_activo_id, versión, estado, publicada_en, revisores[])
       └─ Sección (id, monografía_id, tipo, contenido, fecha_verificación)
            └─ Cita (sección_id, referencia_id, posición)
Referencia (id, tipo[ficha|guía|artículo|web], título, autores, url, doi, pmid,
            fecha_publicación, fecha_acceso)
ProductoComercial (id, nombre_comercial, laboratorio, país, n_registro, estado,
                   es_genérico, fuente_id, fecha_extracción)
  └─ Presentación (forma, concentración, vía, envase)
  └─ ProductoComponente (producto_id, principio_activo_id, dosis)  ← combinaciones
AlertaSeguridad (id, agencia, país, fecha, título, url, principios_activos[])
EvidenciaReciente (id, principio_activo_id, tipo[metaanálisis|ECA|ensayo_en_curso],
                   pmid | nct_id, título, fecha, estado[propuesta|aprobada|descartada])
FuenteDatos (id, nombre, agencia, país, url, licencia, última_sincronización)
Auditoría (entidad, entidad_id, acción, usuario, fecha, diff)
```

---

## 7. Arquitectura técnica

```
[PWA web / móvil]  ──HTTPS──▶  [API Gateway + Auth]
                                   │
                    ┌──────────────┼──────────────────┐
                    ▼              ▼                  ▼
              [API de lectura] [Panel editorial]  [Motor de búsqueda]
                    │              │                  │
                    └──────▶ [PostgreSQL] ◀───────────┘
                                   ▲
                         [ETL / conectores por agencia]
                         (jobs programados + detección de cambios)
```

**Stack propuesto**

| Capa | Tecnología | Motivo |
|---|---|---|
| Frontend | Next.js (React) + TypeScript, **PWA** | Web y móvil con un solo código; modo offline para favoritos. |
| Backend | **FastAPI (Python 3.12+)** + Pydantic v2 | Decidido. Documentación OpenAPI automática; el mismo lenguaje para API y ETL. |
| ORM / migraciones | SQLAlchemy 2 + Alembic | Estándar en el ecosistema FastAPI. |
| ETL | Conectores en Python (`httpx`, `pandas`); scraping con `playwright` solo si no hay alternativa | Un módulo por agencia con interfaz común. |
| Tareas programadas | APScheduler al inicio → Celery/Arq + Redis cuando crezca | Simple para un solo desarrollador. |
| Base de datos | PostgreSQL | Relacional, versionado con tablas históricas, `pg_trgm`. |
| Búsqueda | Meilisearch u OpenSearch | Tolerancia a errores, sinónimos, multilingüe (es/pt/en). |
| Infra | Contenedores + nube con región en LATAM | Latencia y cumplimiento de leyes de datos. |
| Observabilidad | Logs estructurados, métricas, alertas | Detectar fallos de sincronización de fuentes. |

**Idiomas:** interfaz en español (prioritario); portugués e inglés preparados con i18n desde el inicio.

**Estructura inicial del repositorio (propuesta):**

```
backend/
  app/
    api/          # routers FastAPI (búsqueda, monografías, disponibilidad, editorial)
    models.py     # SQLAlchemy (se dividirá en paquete al crecer)
    schemas.py    # Pydantic
    services/     # lógica de negocio, versionado, auditoría
    connectors/   # openfda.py, dailymed.py, rxnorm.py, aemps_cima.py, invima_cum.py,
                  # anmat.py, isp.py, digemid.py, cofepris.py
    evidence/     # pubmed.py, clinicaltrials.py
    core/         # config, seguridad, auth
  alembic/
  tests/
frontend/         # Next.js PWA
data/             # listas semilla (principios_activos_iniciales.csv)
docs/
```

---

## 8. Seguridad y privacidad

- **Autenticación:** e-mail + contraseña con **MFA**, o SSO institucional (OIDC).
- **Verificación profesional** (opcional/por niveles): CRM (Brasil), cédula
  profesional (México), matrícula (Argentina), colegiado (España), etc.
- **Autorización por roles:** lector, editor, revisor farmacéutico, revisor
  médico, administrador. Al inicio existe un único usuario **administrador**
  (el propietario), que puede invitar colaboradores por e-mail y asignarles
  rol; los permisos se definen por rol, no por persona, para escalar sin
  cambiar código.
- **Integridad del contenido:** solo roles revisores publican; toda edición
  queda en el registro de auditoría inmutable; firma/hash por versión publicada.
- **Protección técnica:** TLS 1.2+, cifrado en reposo, OWASP ASVS, cabeceras de
  seguridad (CSP, HSTS), *rate limiting*, escaneo de dependencias, backups
  cifrados y probados.
- **Privacidad:** no se almacenan datos de pacientes; datos mínimos del médico;
  cumplimiento de **RGPD/LOPDGDD** (España), **LFPDPPP** (México),
  **Ley 25.326** (Argentina), **Ley 1581 de 2012** (Colombia),
  **Ley 29733** (Perú) y **Ley 19.628 / Ley 21.719** (Chile).
- **Regulatorio:** como fuente de referencia de información de medicamentos no
  debería clasificarse como dispositivo médico; si en el futuro se añaden
  calculadoras de dosis o recomendaciones individualizadas, evaluar la
  clasificación como *Software as a Medical Device* (MDR en la UE/España y la normativa de cada agencia latinoamericana).

---

## 9. UX clave

- Búsqueda en la pantalla principal con autocompletado (genérico ↔ comercial).
- Selector de país persistente.
- Monografía con índice fijo y secciones plegables; lo crítico (alertas, *boxed
  warnings*, contraindicaciones) siempre visible.
- Badge de frescura: 🟢 < 6 meses · 🟡 6–12 meses · 🔴 > 12 meses o fuente cambiada.
- Favoritos y disponibilidad offline.
- Botón **"Reportar error"** en cada sección → va a la cola editorial.
- Modo oscuro y lectura rápida en móvil (uso en guardia/quirófano).

---

## 10. Hoja de ruta

| Fase | Duración estimada | Entregables |
|---|---|---|
| **0. Fundaciones** | 2–3 semanas | Repositorio, CI, modelo de datos, autenticación, i18n, diseño UI. |
| **1. MVP** | 8–10 semanas | API FastAPI; búsqueda; primeros 100 principios activos; monografía con fechas y referencias; conectores EE. UU. (openFDA/DailyMed/RxNorm), ES (CIMA) y CO (CUM); referencias automáticas desde PubMed; carga asistida de AR, CL, PE y MX; panel editorial para un editor. |
| **2. Conectores y colaboradores** | 4–6 semanas | Conectores automáticos AR, CL, PE, MX donde sea posible; invitación de colaboradores y doble revisión; sección "Evidencia reciente" (PubMed + ClinicalTrials.gov); alertas de farmacovigilancia de las 7 agencias; historial de versiones visible. |
| **3. Clínica avanzada** | 6–8 semanas | Verificador de interacciones entre varios fármacos; ajustes renales con calculadora de ClCr; sección perioperatoria completa. |
| **4. Expansión** | continuo | Brasil, Portugal, UE; apps iOS/Android; API para integraciones (HIS/HCE); analítica de uso. |

---

## 11. Criterios de aceptación del MVP

- [ ] Toda monografía publicada tiene fecha de última actualización visible y ≥ 1 referencia por sección clínica.
- [ ] Referencias numeradas al final, con enlace funcional y fecha de acceso.
- [ ] Búsqueda por DCI, nombre comercial y ATC con resultados en < 300 ms (p95).
- [ ] Tabla de disponibilidad por país con registro sanitario y fecha de verificación.
- [ ] Ningún contenido se publica sin aprobación de un revisor (autorrevisión con checklist mientras haya un solo editor).
- [ ] Disponibilidad cargada para los 7 países prioritarios en los 100 principios activos iniciales.
- [ ] Auditoría completa de cambios y MFA para roles editoriales.
- [ ] Aviso legal visible en cada monografía.

---

## 12. Riesgos y mitigaciones

| Riesgo | Mitigación |
|---|---|
| Fuentes sin API o que cambian de formato | Conectores aislados, pruebas de contrato, alertas cuando falla una sincronización. |
| Información desactualizada | Detección de cambios en fichas oficiales + badge de frescura + SLA de revisión. |
| Licencias/derechos de contenido | Usar fuentes públicas/abiertas; redactar monografías propias citando fuentes; revisión legal. |
| Error clínico en el contenido | Checklist obligatorio de revisión, reporte de errores por usuarios, versionado; doble revisión en cuanto haya colaboradores. |
| Un solo editor (cuello de botella) | Priorizar las secciones críticas (posología, ajustes, contraindicaciones, interacciones); automatizar la importación de fichas oficiales como borradores. |
| Nombres comerciales ambiguos entre países | Mapeo siempre vía principio activo + país; mostrar país en cada resultado. |

---

## 13. Próximos pasos

1. Validar la lista de [`data/principios_activos_iniciales.csv`](../data/principios_activos_iniciales.csv) (añadir o quitar según la práctica clínica).
2. ~~Crear el esqueleto del backend FastAPI (modelos, migraciones, búsqueda) y cargar la lista semilla.~~ Hecho — ver [`backend/README.md`](../backend/README.md). Pendiente: usuarios con roles y MFA.
3. Implementar los conectores con API pública: ~~openFDA NDC (EE. UU.)~~, ~~fichas de DailyMed + flujo editorial de monografías~~, ~~AEMPS CIMA (medicamentos y fichas en español)~~, ~~INVIMA CUM (Colombia)~~ y ~~LINAME de Bolivia (AGEMED)~~ hechos; pendientes Argentina (ANMAT), Chile (ISP), Perú (DIGEMID), México (COFEPRIS), RxNorm (EE. UU.) y el importador de referencias desde PubMed.
4. Definir la checklist de revisión editorial y redactar la primera monografía piloto (p. ej. enoxaparina, que incluye ajuste renal y manejo perioperatorio).
5. Prototipar la pantalla de monografía y la tabla de disponibilidad.
