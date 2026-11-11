from frappe.model.document import Document
from contract_ops.planning import validate_receipt_plan, refresh_invoice_receipts


class ContractReceiptPlan(Document):
    def validate(self):
        validate_receipt_plan(self)

    def on_update(self):
        before = self.get_doc_before_save()
        if self.sales_invoice:
            refresh_invoice_receipts(self.sales_invoice)
        if before and before.sales_invoice and before.sales_invoice != self.sales_invoice:
            refresh_invoice_receipts(before.sales_invoice)
