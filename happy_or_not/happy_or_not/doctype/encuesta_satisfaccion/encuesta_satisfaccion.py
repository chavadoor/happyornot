import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime


class EncuestaSatisfaccion(Document):
    def before_insert(self):
        self.vote_color = {
            "positive": "verde",
            "regular": "amarillo",
            "negative": "rojo",
        }.get(self.vote or "", "")
        if not self.received_at:
            self.received_at = now_datetime()

    def on_update(self):
        if self.get_doc_before_save():
            frappe.throw(
                "Las encuestas son inmutables: no se pueden editar despues de crearse."
            )
