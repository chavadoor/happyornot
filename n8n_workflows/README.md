# Workflows N8N para `happy_or_not`

Tres workflows que exponen webhooks en `n8n.atenea.uk` y los puentean contra el API REST de ERPNext (`happy_or_not.api.*`).

## Archivos

| Archivo | Webhook | Destino ERPNext |
|---|---|---|
| `happyornot-vote.json` | `POST /webhook/happyornot-vote` | `happy_or_not.api.ingest_vote` + alerta WhatsApp si voto negativo |
| `happyornot-heartbeat.json` | `POST /webhook/happyornot-heartbeat` | `happy_or_not.api.ingest_heartbeat` |
| `happyornot-ota-manifest.json` | `GET /webhook/happyornot-ota-manifest` | `happy_or_not.api.get_ota_manifest` |

## Import en N8N

1. Entrar a `https://n8n.atenea.uk` y autenticar.
2. Workflows > **Import from File** > seleccionar cada uno de los 3 JSONs.
3. Al importar cada uno:
   - Revisar que el tag `happy_or_not` este presente (si no, agregarlo).
   - En el nodo `HTTP Request` (ERPNext call):
     - Confirmar URL: `http://10.66.66.1:8080/api/method/happy_or_not.api.ingest_vote` (o la variante correspondiente).
     - Header `X-Terminal-Secret`: setear al valor del api_secret que vive en el `config.json` del firmware (actual: `2c0a12c1ad8725a2ce74354db0560c69`). Idealmente crear credencial N8N tipo "Header Auth" llamada `ATENEA_TERMINAL_SECRET` y referenciarla.
   - Solo en `happyornot-vote.json`, nodo `Send WhatsApp alert`:
     - URL de Evolution API: el valor de `whatsapp_api_url` del `site_config.json` de ERPNext (actual: `http://api-qh347mzm2r01s0n70ftsjyek:8080`).
     - `apikey` header: el valor de `whatsapp_api_key`.
     - Path del endpoint: `/message/sendText/{instance}` donde `instance` = `whatsapp_instance` del config (`atenea-whatsapp`).
     - Body `number`: `whatsapp_rh_group` (`5216142450123-1486000127@g.us`).
4. **Activar** cada workflow con el switch de la esquina superior derecha.
5. Verificar que el webhook URL generado matcha con lo que el firmware espera:
   - `https://n8n.atenea.uk/webhook/happyornot-vote`
   - `https://n8n.atenea.uk/webhook/happyornot-heartbeat`
   - `https://n8n.atenea.uk/webhook/happyornot-ota-manifest`

## Test rapido post-import

Desde cualquier laptop con acceso a n8n.atenea.uk:

```bash
# Voto positive
curl -X POST https://n8n.atenea.uk/webhook/happyornot-vote \
  -H "Content-Type: application/json" \
  -H "X-Terminal-Secret: 2c0a12c1ad8725a2ce74354db0560c69" \
  -d '{"terminal_id":"riberas-recepcion","vote":"positive","firmware_version":"1.0.0"}'
# Esperado: {"message":{"ok":true,"doc_id":"ENC-...","trigger_alert":false}}

# Heartbeat
curl -X POST https://n8n.atenea.uk/webhook/happyornot-heartbeat \
  -H "Content-Type: application/json" \
  -H "X-Terminal-Secret: 2c0a12c1ad8725a2ce74354db0560c69" \
  -d '{"terminal_id":"riberas-recepcion","firmware_version":"1.0.0","wifi_rssi":-70}'
# Esperado: {"message":{"ok":true,"health_status":"online"}}
```

## Notas de disen~o

- El `X-Terminal-Secret` viaja **del firmware a N8N** y **de N8N a ERPNext** con el mismo valor. N8N es transparente en ese aspecto.
- La validacion de secret se hace en ERPNext (comparando contra el hash en `Happy Or Not Settings`), **no** en N8N. N8N solo filtra malformed requests.
- El alert de WhatsApp se dispara **despues** de que ERPNext confirma la insercion del voto, no antes. Esto asegura que todo voto registrado tenga su fila en `Encuesta Satisfaccion`, incluso si WhatsApp falla.
- El cooldown de alertas (default 10 min por terminal) vive en ERPNext — si N8N recibe 5 votos negativos seguidos de la misma terminal en 2 minutos, solo el primero obtendra `trigger_alert: true`.
