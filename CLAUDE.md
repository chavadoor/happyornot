# Proyecto `happy_or_not` — App Frappe/ERPNext v16 (Backend)

**Repositorio:** `chavadoor/happy_or_not`
**Parte de:** Proyecto `happy_or_not` (firmware + Frappe app + workflows N8N)

App Frappe custom que modela las encuestas de satisfacción de terminales físicas tipo HappyOrNot en ERPNext v16 de Club Deportivo Atenea. Recibe datos vía N8N (que a su vez recibe del firmware ESP32) y provee dashboards, reportes y alertas operativas.

**Componentes del proyecto `happy_or_not` (repos separados):**

| Componente | Repo | Propósito |
|---|---|---|
| Firmware ESP32 | `chavadoor/happy_or_not_firmware` | MicroPython, captura votos |
| App Frappe backend | `chavadoor/happy_or_not` | **ESTE REPO** — DocTypes, API, dashboards |
| Workflows N8N | Tag `happy_or_not` en N8N | Orquesta comunicación firmware ↔ ERPNext ↔ WhatsApp |

---

## 1. Contexto del negocio

**Empresa:** Productos y Servicios Deportivos Club Deportivo Atenea S. de R.L. de C.V. (RFC: PSD190405PS4, RESICO Moral)
**Sucursal:** Chihuahua, México
**Terminales físicas:** 5 unidades (Spinning, Aeróbicos, Pesas, Salida, Recepción).
**Frecuencia esperada de votos:** ~150–300 votos/día totales entre las 5 terminales.
**Usuarios del dashboard:** Owner (Salvador), Gerente de gimnasio, RH.

### Stack existente (NO modificar, integrar)

- **ERPNext v16** en IONOS VPS `10.66.66.1` vía WireGuard
- **Apps Frappe bajo `chavadoor` ya instaladas:** `nomina_mexico`, `servicio_cliente`, `contabilidad_mexico`
- **N8N** en `n8n.atenea.uk` — orquesta webhooks
- **WhatsApp:** Meta Cloud API direct

### Rol de esta app

1. Almacenar históricamente cada voto recibido como `Encuesta Satisfaccion`.
2. Mantener estado de salud de cada terminal como `Terminal Status` (actualizado por heartbeat).
3. Exponer endpoints whitelisted para que N8N inserte votos y heartbeats con validación de `api_secret`.
4. Proveer dashboards nativos en ERPNext: votos por terminal, por día de semana, por hora, tendencias.
5. Generar reportes semanales automáticos enviados por WhatsApp al grupo de gerencia.

---

## 2. Arquitectura de integración

```
┌─────────────┐        ┌──────┐       ┌──────────────────────────┐
│  5× ESP32   │ HTTPS  │      │ HTTP  │      ERPNext v16         │
│  Terminales │───────→│ N8N  │──────→│  App `happy_or_not`      │
│ (firmware)  │        │      │       │  - Encuesta Satisfaccion │
└─────────────┘        │      │       │  - Terminal Status       │
                       │      │       └──────────────────────────┘
                       │      │              │
                       │      │ voto negativo ▼
                       │      │       ┌──────────────┐
                       │      │──────→│ Meta WA API  │──→ Grupo RH
                       └──────┘       └──────────────┘
```

**Principios:**

- El firmware NUNCA habla directo con ERPNext. Siempre pasa por N8N (capa de desacoplamiento + rate limiting + transformación).
- Esta app expone endpoints REST whitelisted (`@frappe.whitelist(allow_guest=True)`) que validan `api_secret` en header.
- Cero dependencia de `servicio_cliente` u otras apps — app autocontenida.

---

## 3. DocTypes

### 3.1 `Encuesta Satisfaccion`

Un registro por voto recibido. **Inmutable** (no se edita después de creado).

| Campo | Tipo | Opciones | Notas |
|---|---|---|---|
| `naming_series` | Data | `ENC-.YYYY.-.MM.-.####` | Autogenerado |
| `terminal_id` | Link → Terminal Status | | Obligatorio |
| `location_name` | Data | | Denormalizado desde el firmware |
| `vote` | Select | `positive`, `regular`, `negative` | Obligatorio |
| `vote_color` | Data | | Auto: "verde", "amarillo", "rojo" |
| `vote_timestamp` | Datetime | | Hora del voto según ESP32 (NTP) |
| `received_at` | Datetime | | Hora de recepción en ERPNext |
| `was_queued` | Check | | True si vino de cola offline del firmware |
| `firmware_version` | Data | | Versión del firmware que lo envió |
| `wifi_rssi` | Int | | Signal strength al momento del voto |
| `alert_sent` | Check | | True si votos negativos dispararon WhatsApp |
| `alert_sent_at` | Datetime | | Timestamp del envío WhatsApp |

**Permisos:**
- Role `System Manager`: read/write/delete
- Role `HR User`: read
- Role `Sales Manager`: read
- Role `Guest`: sin acceso directo (solo vía API whitelisted)

**Indexes:**
- `vote_timestamp` (para reportes por rango de fechas)
- `terminal_id + vote_timestamp` (para dashboards por terminal)
- `vote` (para filtrar rápido positivos/negativos)

### 3.2 `Terminal Status`

Un registro por terminal física. Single-record-per-terminal, actualizado por heartbeat.

| Campo | Tipo | Opciones | Notas |
|---|---|---|---|
| `terminal_id` | Data | unique, primary | `terminal-spinning`, etc. |
| `location_name` | Data | | "Salón Spinning" |
| `is_active` | Check | default=1 | Manual toggle para excluir de reportes |
| `last_heartbeat` | Datetime | | Actualizado por cada heartbeat |
| `last_vote_at` | Datetime | | Actualizado por cada voto recibido |
| `firmware_version` | Data | | Última versión reportada |
| `wifi_rssi` | Int | | Last known |
| `uptime_seconds` | Int | | Last known |
| `free_memory_kb` | Int | | Last known |
| `queued_votes` | Int | | Votos pendientes en cola offline |
| `ntp_synced` | Check | | Last known |
| `health_status` | Select | `online`, `stale`, `offline` | Computed |
| `ip_address` | Data | | IP LAN del ESP32 (para acceso diagnóstico) |
| `notes` | Small Text | | Notas manuales del admin |

**Computed `health_status`:**
- `online`: último heartbeat hace < 10 min
- `stale`: último heartbeat entre 10–30 min
- `offline`: último heartbeat > 30 min

Se recalcula vía scheduled job cada minuto (ver §5).

**Permisos:** igual que `Encuesta Satisfaccion`.

### 3.3 `Happy Or Not Settings` (Single DocType)

Configuración global de la app.

| Campo | Tipo | Notas |
|---|---|---|
| `api_secret_hash` | Password | SHA256 del secret que valida requests de N8N |
| `alert_whatsapp_group_id` | Data | ID del grupo WhatsApp "RH Atenea" |
| `negative_vote_cooldown_minutes` | Int | Default 10 — no re-alerta en esta ventana por misma terminal |
| `stale_threshold_minutes` | Int | Default 10 |
| `offline_threshold_minutes` | Int | Default 30 |
| `weekly_report_enabled` | Check | Default 1 |
| `weekly_report_day` | Select | `Monday`…`Sunday`, default `Monday` |
| `weekly_report_time` | Time | Default `09:00` |
| `weekly_report_recipients` | Small Text | Números/grupos WhatsApp separados por coma |

---

## 4. API endpoints (whitelisted para N8N)

Todos bajo `happy_or_not.api.*`, accesibles vía `/api/method/happy_or_not.api.<endpoint>`.

### 4.1 `ingest_vote`

**Método:** POST
**Auth:** Header `X-Terminal-Secret: <secret>` validado contra `api_secret_hash`.

**Payload esperado** (igual al del firmware):
```json
{
  "terminal_id": "terminal-spinning",
  "location_name": "Salón Spinning",
  "vote": "positive",
  "timestamp_iso": "2026-04-21T14:23:45-06:00",
  "firmware_version": "1.0.0",
  "wifi_rssi": -67,
  "uptime_seconds": 123456,
  "queued": false
}
```

**Lógica:**
1. Validar secret. Si inválido → 401.
2. Validar que `terminal_id` existe y está `is_active=1`. Si no → 404.
3. Crear `Encuesta Satisfaccion` con los datos.
4. Actualizar `Terminal Status.last_vote_at` y métricas.
5. Si `vote == "negative"` y no hay otro voto negativo en cooldown → retornar flag `trigger_alert: true` a N8N (N8N manda el WhatsApp).
6. Retornar 201 con `{"ok": true, "doc_id": "ENC-2026-04-0123", "trigger_alert": true|false}`.

### 4.2 `ingest_heartbeat`

**Método:** POST
**Auth:** mismo header.

**Payload esperado:**
```json
{
  "type": "heartbeat",
  "terminal_id": "terminal-spinning",
  "firmware_version": "1.0.0",
  "wifi_rssi": -67,
  "uptime_seconds": 123456,
  "free_memory_kb": 87,
  "queued_votes": 0,
  "last_vote_ago_seconds": 3400,
  "ntp_synced": true
}
```

**Lógica:**
1. Validar secret.
2. Upsert `Terminal Status` (crear si no existe).
3. Actualizar `last_heartbeat` a `now()`.
4. Recalcular `health_status`.
5. Retornar 200 con `{"ok": true}`.

### 4.3 `get_ota_manifest`

**Método:** GET
**Auth:** mismo header + query `?current_version=1.0.0`.

**Lógica:**
1. Validar secret.
2. Consultar GitHub Releases API de `chavadoor/happy_or_not_firmware`.
3. Retornar manifest del último release:
```json
{
  "version": "1.1.0",
  "firmware_url": "https://github.com/chavadoor/happy_or_not_firmware/releases/download/v1.1.0/firmware.tar.gz",
  "sha256": "abc123...",
  "min_free_memory_kb": 60,
  "release_notes_url": "https://github.com/chavadoor/happy_or_not_firmware/releases/tag/v1.1.0"
}
```

---

## 5. Scheduled jobs

En `hooks.py` → `scheduler_events`:

| Job | Frecuencia | Propósito |
|---|---|---|
| `recompute_health_status` | Cada 1 min | Marca terminales como `stale`/`offline` |
| `alert_offline_terminals` | Cada 5 min | WhatsApp si una terminal pasa a `offline` |
| `send_weekly_report` | Cron según settings | Reporte consolidado WhatsApp |
| `purge_old_votes` | Diario 03:00 | Archivar votos > 2 años (opcional) |

---

## 6. Dashboards en ERPNext

### 6.1 Dashboard `Satisfacción — Vista General`

**Widgets:**

1. **Number Card: Votos hoy** — total + split verde/amarillo/rojo.
2. **Number Card: NPS semanal** — score `(positive% − negative%)` de últimos 7 días.
3. **Number Card: Terminales activas** — `online / total`.
4. **Chart (barras apiladas):** Votos por día, últimos 30 días, apilado por color.
5. **Chart (barras horizontales):** Votos por terminal, últimos 7 días, ordenado por volumen.
6. **Heatmap:** Votos por hora × día de semana (últimos 30 días).
7. **Chart (pie):** Distribución global verde/amarillo/rojo últimos 7 días.

### 6.2 Report Builder: `Votos Negativos Recientes`

Filtros: terminal, rango fechas, solo `vote=negative`.
Columnas: timestamp, terminal, location, firmware version, alert_sent.
**Uso:** el gerente revisa cada lunes qué áreas están teniendo insatisfacción.

### 6.3 Report Builder: `Health de Terminales`

Columnas: terminal_id, location, health_status, last_heartbeat, uptime_seconds, firmware_version, queued_votes.
Ordenado por `health_status` (offline primero).

---

## 7. Estructura de archivos

```
happy_or_not/
├── README.md
├── CLAUDE.md                           # Este archivo
├── setup.py
├── requirements.txt
├── happy_or_not/
│   ├── __init__.py                     # __version__ = "0.1.0"
│   ├── hooks.py                        # scheduler_events, doc_events
│   ├── api.py                          # Endpoints whitelisted
│   ├── patches.txt
│   ├── modules.txt                     # "Happy Or Not"
│   ├── config/
│   │   ├── __init__.py
│   │   └── desktop.py                  # Workspace en ERPNext
│   ├── happy_or_not/
│   │   ├── __init__.py
│   │   └── doctype/
│   │       ├── encuesta_satisfaccion/
│   │       │   ├── encuesta_satisfaccion.json
│   │       │   ├── encuesta_satisfaccion.py
│   │       │   └── test_encuesta_satisfaccion.py
│   │       ├── terminal_status/
│   │       │   ├── terminal_status.json
│   │       │   ├── terminal_status.py
│   │       │   └── test_terminal_status.py
│   │       └── happy_or_not_settings/
│   │           ├── happy_or_not_settings.json
│   │           └── happy_or_not_settings.py
│   ├── tasks.py                        # Scheduled jobs
│   ├── dashboard/                      # Dashboard charts + cards
│   │   └── happy_or_not_dashboard.json
│   ├── report/                         # Report Builder queries
│   │   ├── votos_negativos_recientes/
│   │   └── health_de_terminales/
│   └── utils/
│       ├── __init__.py
│       ├── auth.py                     # Validación de X-Terminal-Secret
│       ├── whatsapp_notifier.py        # Envío de alertas (via Meta API)
│       ├── weekly_report.py            # Generación del reporte semanal
│       └── health_monitor.py           # Recompute health_status
└── docs/
    ├── API_CONTRACT.md                 # Contrato con N8N (payloads exactos)
    ├── INSTALL.md                      # bench get-app + install-app
    └── screenshots/                    # Del dashboard para documentación
```

---

## 8. Fases de implementación

### Fase 1 — Scaffold de la app (30 min)

**Entregables:**
- `bench get-app https://github.com/chavadoor/happy_or_not` desde el VPS.
- `bench new-app happy_or_not` genera estructura.
- `modules.txt` con módulo "Happy Or Not".
- `__version__ = "0.1.0"` en `__init__.py`.
- README y CLAUDE.md commiteados.

**Criterio de aceptación:**
- [ ] `bench --site atenea.local install-app happy_or_not` sin errores.
- [ ] La app aparece en "Installed Apps" en el sitio.
- [ ] Módulo "Happy Or Not" visible en el sidebar del desk.

### Fase 2 — DocTypes base

**Entregables:**
- `Encuesta Satisfaccion`, `Terminal Status`, `Happy Or Not Settings` con JSON schemas.
- Permisos configurados (System Manager, HR User, Sales Manager).
- Índices creados.
- Fixtures con 5 `Terminal Status` semilla (spinning/aerobicos/pesas/salida/recepcion) en `fixtures/terminal_status.json`.

**Criterio de aceptación:**
- [ ] `bench migrate` aplica sin errores.
- [ ] Se pueden crear registros manualmente desde el UI.
- [ ] Los 5 `Terminal Status` aparecen al instalar.

### Fase 3 — Endpoints API whitelisted

**Entregables:**
- `api.py` con `ingest_vote`, `ingest_heartbeat`, `get_ota_manifest`.
- `utils/auth.py` con validación de `X-Terminal-Secret`.
- `docs/API_CONTRACT.md` con payloads exactos, códigos de error, ejemplos cURL.

**Criterio de aceptación:**
- [ ] `curl` con secret válido → 201 crea voto correctamente.
- [ ] `curl` con secret inválido → 401.
- [ ] `curl` a terminal_id inexistente → 404.
- [ ] `curl` con payload malformado → 400 con mensaje claro.

### Fase 4 — Lógica de alertas WhatsApp

**Entregables:**
- `utils/whatsapp_notifier.py` que consume Meta Cloud API (credenciales en `site_config.json`, no hardcoded).
- Cooldown de 10 min por terminal.
- Template de mensaje: `"⚠️ Voto NEGATIVO en {location_name} — {timestamp_hora}. Total rojos hoy: {count}."`

**Criterio de aceptación:**
- [ ] Voto rojo de prueba → alerta llega al grupo RH.
- [ ] Segundo voto rojo de la misma terminal en 5 min → NO re-alerta.
- [ ] Voto rojo de OTRA terminal → sí alerta (cooldown es por terminal).

### Fase 5 — Scheduled jobs y health monitor

**Entregables:**
- `tasks.py` con las 4 funciones programadas.
- `hooks.py → scheduler_events` registrando cada una.
- Log detallado en cada ejecución (tabla `Scheduler Log`).

**Criterio de aceptación:**
- [ ] Simular que una terminal no manda heartbeat 15 min → `health_status` pasa a `stale`.
- [ ] 35 min sin heartbeat → pasa a `offline` + alerta WhatsApp.
- [ ] Cuando la terminal vuelve → `online` + WhatsApp "terminal recuperada".

### Fase 6 — Dashboard y reportes

**Entregables:**
- `happy_or_not_dashboard.json` con los 7 widgets.
- 2 Report Builders guardados.
- Screenshots en `docs/screenshots/`.

**Criterio de aceptación:**
- [ ] Dashboard se renderiza sin errores.
- [ ] Heatmap refleja horarios reales de operación del gym (6am–10pm).
- [ ] Reportes exportan a Excel correctamente.

### Fase 7 — Reporte semanal automático

**Entregables:**
- `utils/weekly_report.py` genera PDF con: NPS, distribución por terminal, tendencia vs. semana anterior, lista de votos negativos.
- Scheduled job lunes 09:00.
- Envío por WhatsApp al grupo configurado en settings.

**Criterio de aceptación:**
- [ ] PDF generado correctamente, formato legible.
- [ ] Llega al grupo WhatsApp configurado.
- [ ] Comparación semana vs. semana anterior funciona.

### Fase 8 — Tests y documentación

**Entregables:**
- `test_encuesta_satisfaccion.py` con unit tests de API endpoints.
- `test_terminal_status.py` con tests de health computation.
- `docs/INSTALL.md` con pasos para reinstalar from scratch.

**Criterio de aceptación:**
- [ ] `bench --site atenea.local run-tests --app happy_or_not` pasa 100%.
- [ ] Coverage > 70%.

---

## 9. Seguridad

- `api_secret` se guarda hasheado (SHA256) en `Happy Or Not Settings`, nunca en texto plano.
- Los endpoints whitelisted validan el secret ANTES de hacer queries a DB.
- Rate limiting básico: máximo 10 votos/min por `terminal_id` (previene abuso si un secret se filtra).
- Meta WhatsApp API token en `site_config.json` (nunca en el repo).
- Los `Terminal Status` no exponen el `api_secret` vía UI ni API.

---

## 10. Consideraciones de performance

- Volumen esperado: 300 votos/día × 365 días = ~110k registros/año. Manageable en MariaDB estándar.
- Índice compuesto `(terminal_id, vote_timestamp DESC)` para queries del dashboard.
- Dashboard queries limitadas a últimos 30 días por default (filtro).
- Purge de votos > 2 años archiva a tabla cold storage (opcional, Fase 9 futura).

---

## 11. Prompt inicial para Claude Code

Copiar esto como primera instrucción:

> Lee `CLAUDE.md` completo. Luego:
> 1. Implementa la Fase 1 (scaffold): comandos exactos de `bench new-app` y `bench install-app`, estructura de carpetas, `__init__.py`, `hooks.py` mínimo, `modules.txt`.
> 2. Implementa la Fase 2 (DocTypes): los JSON schemas de los 3 DocTypes con campos, permisos e índices exactos según §3. Incluye `fixtures/terminal_status.json` con las 5 terminales semilla.
> 3. Genera `docs/API_CONTRACT.md` con ejemplos cURL de cada endpoint, aunque la Fase 3 no la implementes aún.
> 4. Genera `docs/INSTALL.md` con pasos reproducibles desde cero.
> 5. NO avances a la Fase 3 hasta que yo confirme que Fase 1 y 2 están instaladas y migradas en el sitio real de ERPNext.
>
> Asume ERPNext v16, Python 3.11, MariaDB 10.6, bench multi-app. Pregúntame si hay ambigüedad.
