"""API whitelisted para el stack de terminales Happy Or Not.

N8N es el unico consumidor esperado. Valida X-Terminal-Secret contra el hash
en Happy Or Not Settings. El firmware NO habla directo con ERPNext: siempre
pasa por N8N (capa de desacoplamiento y rate limiting).
"""

import frappe
from frappe import _
from frappe.utils import now_datetime

from happy_or_not.utils.auth import require_terminal_secret


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
            "vote_timestamp": data.get("timestamp_iso") or now_datetime(),
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
        trigger_alert = _should_trigger_alert(terminal_id)
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


def _should_trigger_alert(terminal_id: str) -> bool:
    settings = frappe.get_cached_doc("Happy Or Not Settings")
    cooldown = (settings.negative_vote_cooldown_minutes or 10) * 60
    recent = frappe.db.sql(
        """
        SELECT name FROM `tabEncuesta Satisfaccion`
        WHERE terminal_id = %(tid)s
          AND vote = 'negative'
          AND alert_sent = 1
          AND TIMESTAMPDIFF(SECOND, alert_sent_at, NOW()) < %(cd)s
        LIMIT 1
        """,
        {"tid": terminal_id, "cd": cooldown},
    )
    return not recent
