import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime, time_diff_in_seconds


class TerminalStatus(Document):
    def compute_health_status(self):
        settings = frappe.get_cached_doc("Happy Or Not Settings")
        stale = (settings.stale_threshold_minutes or 10) * 60
        offline = (settings.offline_threshold_minutes or 30) * 60
        if not self.last_heartbeat:
            self.health_status = "offline"
            return
        age = time_diff_in_seconds(now_datetime(), self.last_heartbeat)
        if age < stale:
            self.health_status = "online"
        elif age < offline:
            self.health_status = "stale"
        else:
            self.health_status = "offline"
