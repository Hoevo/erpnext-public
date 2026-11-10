from frappe.model.document import Document
from contract_ops.planning import validate_billing_plan


class ContractBillingPlan(Document):
    def validate(self):
        validate_billing_plan(self)
