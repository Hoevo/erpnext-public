import frappe
from frappe.model.document import Document


class ContractRenewalCycle(Document):
    def validate(self):
        if not self.flags.renewal_internal:
            frappe.throw("续签记录只能通过合同续签办理操作维护")
    def on_trash(self):
        frappe.throw("续签历史记录不能删除")
