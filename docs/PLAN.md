# Farmacosas — Plan de la aplicación

> Consulta de medicamentos para médicos: información confiable, trazable y segura,
> con fecha de última actualización, referencias al final de cada monografía y
> disponibilidad por nombre genérico (DCI) y nombre comercial por región.

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
| **Revisión humana** | Flujo editorial: *borrador → revisión farmacéutica → revisión médica → publicado*. Ningún contenido importado automáticamente se publica sin aprobación. |
| **Versionado** | Todas las monografías son versionadas (historial consultable, diff entre versiones). |
| **Frescura visible** | Aviso visual si una sección no se verifica hace > 12 meses o si la fuente oficial cambió después de la última revisión. |
| **Alertas de seguridad** | Integración de alertas de farmacovigilancia (FDA, EMA, AEMPS, ANVISA, etc.) destacadas arriba de la monografía. |
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

| Región | Agencia | Fuente |
|---|---|---|
| Brasil | ANVISA | Consulta de registros / Bulário Eletrônico |
| México | COFEPRIS | Registros sanitarios |
| Argentina | ANMAT | Vademécum Nacional de Medicamentos |
| Colombia | INVIMA | Datos abiertos / consulta de registros |
| Chile | ISP | Registro de productos farmacéuticos |
| Perú | DIGEMID | Observatorio / registro sanitario |
| España | AEMPS | CIMA |
| Unión Europea | EMA | Medicamentos autorizados centralizadamente |
| EE. UU. | FDA | Drugs@FDA, Orange Book, NDC Directory |
| Portugal | INFARMED | Infomed |

> Cada conector (ETL) registra la **fecha y hora de extracción**; si la agencia
> no ofrece API se usan descargas oficiales de datos abiertos. Se revisarán los
> términos de uso/licencia de cada fuente antes de integrarla.

---

## 4. Contenido de la monografía

Orden de secciones (pensado para uso clínico rápido):

1. **Encabezado** — DCI, sinónimos, ATC, grupo terapéutico, badge de
   *última actualización* y de *alertas de seguridad*.
2. **Resumen clínico** (3–5 líneas).
3. **Indicaciones** (aprobadas por agencia/región y *off-label* claramente marcado).
4. **Posología** — adulto, pediatría, geriatría.
5. **Ajustes** — insuficiencia renal (por TFG/ClCr), hepática (Child-Pugh), diálisis.
6. **Contraindicaciones**.
7. **Advertencias y precauciones** (incluye *boxed warnings*).
8. **Interacciones** relevantes (gravedad, mecanismo, conducta).
9. **Embarazo y lactancia**.
10. **Reacciones adversas** (frecuentes / graves).
11. **Farmacología** — mecanismo, farmacocinética (t½, metabolismo, excreción).
12. **Consideraciones perioperatorias** (suspensión/reinicio, anticoagulantes,
    antiagregantes, hipoglucemiantes — útil para cirugía).
13. **Sobredosis y antídoto**.
14. **Disponibilidad por región** (tabla, ver §5).
15. **Referencias** — numeradas, estilo Vancouver, con enlace/DOI/PMID y
    fecha de acceso.
16. **Pie de página** — *Última actualización: AAAA-MM-DD · Versión N ·
    Revisado por: …*

Cada sección muestra su propia fecha de verificación y las citas en línea `[1]`,
`[2]` que apuntan a la lista de referencias.

---

## 5. Disponibilidad por región

Vista de tabla filtrable por país (el país del usuario viene preseleccionado):

| País | Nombre comercial | Laboratorio | Presentación | Registro | Estado | Verificado |
|---|---|---|---|---|---|---|
| 🇧🇷 BR | Ejemplo® | Lab X | comp. 500 mg | 1.2345.6789 | Vigente | 2026-09-01 |
| 🇲🇽 MX | Ejemplo® | Lab Y | sol. iny. 1 g | 123M2020 SSA | Vigente | 2026-08-20 |

- Indica si existe **genérico/intercambiable** (p. ej. *genérico* ANVISA,
  *GI* COFEPRIS, *EFG* AEMPS).
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
| Backend | NestJS (TypeScript) o FastAPI (Python) | FastAPI facilita los ETL y el procesamiento de fichas. |
| Base de datos | PostgreSQL | Relacional, versionado con tablas históricas, `pg_trgm`. |
| Búsqueda | Meilisearch u OpenSearch | Tolerancia a errores, sinónimos, multilingüe (es/pt/en). |
| ETL | Jobs programados (cron / colas) | Un conector por agencia, con detección de cambios (hash del documento). |
| Infra | Contenedores + nube con región en LATAM | Latencia y cumplimiento de leyes de datos. |
| Observabilidad | Logs estructurados, métricas, alertas | Detectar fallos de sincronización de fuentes. |

**Idiomas:** interfaz en español, portugués e inglés (i18n desde el inicio).

---

## 8. Seguridad y privacidad

- **Autenticación:** e-mail + contraseña con **MFA**, o SSO institucional (OIDC).
- **Verificación profesional** (opcional/por niveles): CRM (Brasil), cédula
  profesional (México), matrícula (Argentina), colegiado (España), etc.
- **Autorización por roles:** lector, editor, revisor farmacéutico, revisor
  médico, administrador.
- **Integridad del contenido:** solo roles revisores publican; toda edición
  queda en el registro de auditoría inmutable; firma/hash por versión publicada.
- **Protección técnica:** TLS 1.2+, cifrado en reposo, OWASP ASVS, cabeceras de
  seguridad (CSP, HSTS), *rate limiting*, escaneo de dependencias, backups
  cifrados y probados.
- **Privacidad:** no se almacenan datos de pacientes; datos mínimos del médico;
  cumplimiento de **LGPD** (Brasil), **GDPR** (UE) y leyes locales (p. ej.
  Ley 25.326 AR, LFPDPPP MX).
- **Regulatorio:** como fuente de referencia de información de medicamentos no
  debería clasificarse como dispositivo médico; si en el futuro se añaden
  calculadoras de dosis o recomendaciones individualizadas, evaluar la
  clasificación como *Software as a Medical Device* (ANVISA RDC 657/2022, MDR UE).

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
| **1. MVP** | 6–8 semanas | Búsqueda; 100 principios activos más usados; monografía con fechas y referencias; disponibilidad BR, MX, AR, ES, EE. UU.; panel editorial básico. |
| **2. Ampliación regional** | 4–6 semanas | Conectores CO, CL, PE, PT, UE; alertas de farmacovigilancia; historial de versiones visible. |
| **3. Clínica avanzada** | 6–8 semanas | Verificador de interacciones entre varios fármacos; ajustes renales con calculadora de ClCr; sección perioperatoria completa. |
| **4. Móvil nativo / API** | continuo | Apps iOS/Android, API para integraciones (HIS/HCE), analítica de uso. |

---

## 11. Criterios de aceptación del MVP

- [ ] Toda monografía publicada tiene fecha de última actualización visible y ≥ 1 referencia por sección clínica.
- [ ] Referencias numeradas al final, con enlace funcional y fecha de acceso.
- [ ] Búsqueda por DCI, nombre comercial y ATC con resultados en < 300 ms (p95).
- [ ] Tabla de disponibilidad por país con registro sanitario y fecha de verificación.
- [ ] Ningún contenido se publica sin aprobación de un revisor.
- [ ] Auditoría completa de cambios y MFA para roles editoriales.
- [ ] Aviso legal visible en cada monografía.

---

## 12. Riesgos y mitigaciones

| Riesgo | Mitigación |
|---|---|
| Fuentes sin API o que cambian de formato | Conectores aislados, pruebas de contrato, alertas cuando falla una sincronización. |
| Información desactualizada | Detección de cambios en fichas oficiales + badge de frescura + SLA de revisión. |
| Licencias/derechos de contenido | Usar fuentes públicas/abiertas; redactar monografías propias citando fuentes; revisión legal. |
| Error clínico en el contenido | Doble revisión (farmacéutica + médica), reporte de errores por usuarios, versionado. |
| Nombres comerciales ambiguos entre países | Mapeo siempre vía principio activo + país; mostrar país en cada resultado. |

---

## 13. Próximos pasos

1. Validar el alcance del MVP (países y lista de los 100 principios activos iniciales).
2. Elegir backend (NestJS vs. FastAPI) y proveedor de nube.
3. Prototipar la pantalla de monografía y la tabla de disponibilidad.
4. Implementar el primer conector (AEMPS CIMA u openFDA, que tienen API pública)
   y el de ANVISA.
5. Definir el equipo editorial (farmacéutico + médico revisor) y su flujo.
