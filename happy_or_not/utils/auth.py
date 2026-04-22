"""Autenticacion de N8N contra happy_or_not.api.*

El header `X-Terminal-Secret` contiene el api_secret en claro; se compara su
SHA256 contra el valor almacenado en `Happy Or Not Settings.api_secret_hash`.

Diseno de seguridad:
- Comparacion en tiempo constante (hmac.compare_digest).
- Sin logs del secret.
- 401 en cualquier fallo (misma respuesta para header faltante/invalido para no
  filtrar cual de los dos caso fue).
"""

import hashlib
import hmac

import frappe
from frappe import _


def _get_header_secret() -> str:
    req = getattr(frappe.local, "request", None) or getattr(frappe, "request", None)
    if req is None:
        return ""
    return (
        req.headers.get("X-Terminal-Secret")
        or req.headers.get("x-terminal-secret")
        or ""
    )


def require_terminal_secret() -> None:
    provided = _get_header_secret()
    if not provided:
        _unauthorized()

    settings = frappe.get_cached_doc("Happy Or Not Settings")
    stored_hash = (
        settings.get_password("api_secret_hash", raise_exception=False) or ""
    ).strip()
    if not stored_hash:
        _unauthorized()

    provided_hash = hashlib.sha256(provided.encode("utf-8")).hexdigest()
    if not hmac.compare_digest(provided_hash, stored_hash):
        _unauthorized()


def _unauthorized():
    frappe.local.response["http_status_code"] = 401
    raise frappe.AuthenticationError(_("Invalid or missing X-Terminal-Secret"))
