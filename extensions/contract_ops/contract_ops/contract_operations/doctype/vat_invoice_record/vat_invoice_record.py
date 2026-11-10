import frappe
from frappe.model.document import Document
from contract_ops.events import locked_contract, check_party
from contract_ops.rules import validate_limit, validate_tax_amount
from contract_ops.planning import validate_vat_allocations, create_receipt_plans, cancel_receipt_plans
from contract_ops.vat_links import validate_invoice_shares, sync_payment_sources


class VATInvoiceRecord(Document):
    def validate(self):
        if not self.sales_contract and not self.contract_group and not self.get('plan_allocations') and not self.get('invoice_allocations'):
            frappe.throw("请关联销售合同、合同组，或填写合并开票分配明细")
        contract = locked_contract(self.sales_contract) if self.sales_contract else None
        if contract:
            check_party(self, contract)
        if self.contract_group:
            group = frappe.get_doc("Contract Group", self.contract_group)
            if contract and contract.contract_group != group.name:
                frappe.throw("销售合同不属于所选合同组")
        self.invoice_number = (self.invoice_number or "").strip()
        if not self.invoice_number:
            frappe.throw("请填写外部税票号码")
        if self.sales_invoice:
            invoice = frappe.get_doc("Sales Invoice", self.sales_invoice)
            invoice.check_permission("read")
            if invoice.docstatus != 1:
                frappe.throw("关联应收发票必须已提交")
            if contract and invoice.get("custom_sales_contract") != contract.name:
                frappe.throw("关联应收发票必须属于本合同")
            if self.contract_group:
                invoice_contract = frappe.get_doc("Sales Contract", invoice.get("custom_sales_contract"))
                if invoice_contract.contract_group != self.contract_group:
                    frappe.throw("关联应收发票必须属于本合同组")
        validate_invoice_shares(self)
        existing = 0
        if contract:
            existing = frappe.db.sql(
                """SELECT COALESCE(SUM(gross_amount),0) FROM `tabVAT Invoice Record`
                WHERE sales_contract=%s AND docstatus=1 AND name!=%s""",
                (contract.name, self.name or ""),
            )[0][0]
        try:
            validate_tax_amount(self.gross_amount, self.tax_amount)
            # Legacy scanned agreements can prove a yearly unit price but not a
            # reliable whole-contract total.  A source-verified historical tax
            # invoice must not be rejected merely because that reference amount
            # is smaller than the accumulated real invoicing history.
            if contract and "合同总额未能从原件确认" not in (contract.get("notes") or ""):
                validate_limit(contract.contract_amount, existing, self.gross_amount)
        except ValueError as exc:
            frappe.throw(str(exc))
        validate_vat_allocations(self)
        sync_payment_sources(self)

    def before_submit(self):
        self.validate()

    def on_submit(self):
        create_receipt_plans(self)

    def before_cancel(self):
        cancel_receipt_plans(self)
        self.ignore_linked_doctypes = ('Contract Receipt Plan',)

    def on_cancel(self):
        from contract_ops.planning import refresh_billing_plan
        for name in {r.billing_plan for r in self.get('plan_allocations') or []}:
            refresh_billing_plan(name)
