"""API whitelisted para el stack de terminales Happy Or Not.

N8N es el unico consumidor esperado. Valida X-Terminal-Secret contra el hash
en Happy Or Not Settings. El firmware NO habla directo con ERPNext: siempre
pasa por N8N (capa de desacoplamiento y rate limiting).
"""

from datetime import datetime

import frappe
from frappe import _
from frappe.utils import now_datetime

from happy_or_not.utils.auth import require_terminal_secret


def _parse_vote_timestamp(ts_str):
    """Normaliza el timestamp_iso enviado por el firmware.

    - Si viene vacio o invalido -> now_datetime() del site.
    - Si el firmware aun no sincronizo NTP (manda "2000-01-01T..."), descarta
      el valor del firmware y usa now_datetime() para no contaminar reportes.
    - Si viene con timezone offset (ej. "-06:00"), lo remueve: Frappe guarda
      datetime naive en el timezone del site y MariaDB rechaza el offset.
    """
    if not ts_str:
        return now_datetime()
    try:
        dt = datetime.fromisoformat(ts_str)
    except ValueError:
        return now_datetime()
    if dt.year < 2020:
        return now_datetime()
    return dt.replace(tzinfo=None) if dt.tzinfo else dt


@frappe.whitelist(allow_guest=True, methods=["POST"])
def ingest_vote():
    require_terminal_secret()

    data = frappe.request.get_json(force=True) or {}
    terminal_id = (data.get("terminal_id") or "").strip()
    vote = (data.get("vote") or "").strip().lower()

    if not terminal_id or not vote:
        frappe.throw(_("terminal_id y vote son requeridos"), frappe.ValidationError)
    if vote not in ("positive", "regular", "negative"):
        frappe.throw(
            _("vote debe ser positive/regular/negative"), frappe.ValidationError
        )

    if not frappe.db.exists("Terminal Status", terminal_id):
        frappe.local.response["http_status_code"] = 404
        return {"ok": False, "error": "terminal_id_unknown", "terminal_id": terminal_id}

    ts = frappe.get_doc("Terminal Status", terminal_id)
    if not ts.is_active:
        frappe.local.response["http_status_code"] = 403
        return {"ok": False, "error": "terminal_inactive"}

    doc = frappe.get_doc(
        {
            "doctype": "Encuesta Satisfaccion",
            "terminal_id": terminal_id,
            "vote": vote,
            "vote_timestamp": _parse_vote_timestamp(data.get("timestamp_iso")),
            "received_at": now_datetime(),
            "was_queued": 1 if data.get("queued") else 0,
            "firmware_version": data.get("firmware_version"),
            "wifi_rssi": data.get("wifi_rssi"),
        }
    ).insert(ignore_permissions=True)

    ts.last_vote_at = now_datetime()
    if data.get("firmware_version"):
        ts.firmware_version = data.get("firmware_version")
    ts.save(ignore_permissions=True)

    trigger_alert = False
    if vote == "negative":
        trigger_alert = _should_trigger_alert(terminal_id, exclude=doc.name)
        if trigger_alert:
            doc.db_set("alert_sent", 1)
            doc.db_set("alert_sent_at", now_datetime())

    frappe.db.commit()
    frappe.local.response["http_status_code"] = 201
    return {"ok": True, "doc_id": doc.name, "trigger_alert": trigger_alert}


@frappe.whitelist(allow_guest=True, methods=["POST"])
def ingest_heartbeat():
    require_terminal_secret()

    data = frappe.request.get_json(force=True) or {}
    terminal_id = (data.get("terminal_id") or "").strip()
    if not terminal_id:
        frappe.throw(_("terminal_id es requerido"), frappe.ValidationError)

    if frappe.db.exists("Terminal Status", terminal_id):
        ts = frappe.get_doc("Terminal Status", terminal_id)
    else:
        ts = frappe.get_doc(
            {
                "doctype": "Terminal Status",
                "terminal_id": terminal_id,
                "is_active": 1,
            }
        )

    ts.last_heartbeat = now_datetime()
    ts.firmware_version = data.get("firmware_version") or ts.firmware_version
    ts.wifi_rssi = data.get("wifi_rssi")
    ts.uptime_seconds = data.get("uptime_seconds")
    ts.free_memory_kb = data.get("free_memory_kb")
    ts.queued_votes = data.get("queued_votes")
    ts.ntp_synced = 1 if data.get("ntp_synced") else 0
    ts.compute_health_status()
    ts.save(ignore_permissions=True)
    frappe.db.commit()
    return {"ok": True, "health_status": ts.health_status}


@frappe.whitelist(allow_guest=True, methods=["GET"])
def get_ota_manifest():
    require_terminal_secret()
    current_version = frappe.request.args.get("current_version") or "0.0.0"
    # Proxy a GitHub Releases de chavadoor/happy_or_not_firmware.
    # Por ahora placeholder: no hay release publicado, responde "sin actualizacion".
    return {
        "version": current_version,
        "firmware_url": None,
        "sha256": None,
        "note": "Sin release publicado aun en chavadoor/happy_or_not_firmware",
    }


def _should_trigger_alert(terminal_id: str, exclude: str) -> bool:
    """Primera alerta por rafaga: si ya hay CUALQUIER voto negativo reciente de
    la misma terminal (dentro del cooldown), no re-alertar — aunque la alerta
    previa haya fallado o aun no se marque alert_sent=1 (evita race).
    El `exclude` es el doc.name recien insertado — se descarta del check."""
    settings = frappe.get_cached_doc("Happy Or Not Settings")
    cooldown = (settings.negative_vote_cooldown_minutes or 10) * 60
    recent = frappe.db.sql(
        """
        SELECT name FROM `tabEncuesta Satisfaccion`
        WHERE terminal_id = %(tid)s
          AND vote = 'negative'
          AND name != %(ex)s
          AND TIMESTAMPDIFF(SECOND, received_at, NOW()) < %(cd)s
        LIMIT 1
        """,
        {"tid": terminal_id, "ex": exclude, "cd": cooldown},
    )
    return not recent
