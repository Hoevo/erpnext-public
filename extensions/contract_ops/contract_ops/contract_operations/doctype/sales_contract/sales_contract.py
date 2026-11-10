import frappe
from frappe.model.document import Document
from contract_ops.rules import validate_contract


class SalesContract(Document):
    def validate(self):
        from contract_ops.renewal import validate_contract as validate_renewal
        validate_renewal(self)
        from contract_ops.prepayment import validate_flow_change
        validate_flow_change(self)
        try:
            validate_contract(self.contract_amount, self.start_date, self.end_date)
        except ValueError as exc:
            frappe.throw(str(exc))

    def before_submit(self):
        self.validate()

    def before_update_after_submit(self):
        from contract_ops.renewal import validate_contract as validate_renewal
        validate_renewal(self)
        from contract_ops.prepayment import validate_flow_change
        validate_flow_change(self)

    def on_update(self):
        from contract_ops.renewal import synchronize
        synchronize(self)

    def on_update_after_submit(self):
        self.on_update()

    def on_cancel(self):
        self.on_update()

    def before_cancel(self):
        for name in frappe.get_all('Contract Collection Plan',filters={'sales_contract':self.name},pluck='name'):
            from contract_ops.prepayment import active_funding
            if active_funding(name):
                frappe.throw('合同仍有先款后票的实际到账，请先处理关联收款')
            frappe.db.set_value('Contract Collection Plan',name,dict(disabled=1,status='已停用'))
        for doctype, field in (("Sales Invoice", "custom_sales_contract"), ("VAT Invoice Record", "sales_contract")):
            if frappe.db.exists(doctype, {field: self.name, "docstatus": 1}):
                frappe.throw("请先取消关联合同的已提交发票，再取消合同")
        for name in frappe.get_all('Contract Billing Plan', filters={'sales_contract': self.name}, pluck='name'):
            frappe.db.set_value('Contract Billing Plan', name, dict(disabled=1, status='已停用'))
        self.ignore_linked_doctypes = ('Contract Billing Plan', 'Contract Receipt Plan','Contract Collection Plan')
