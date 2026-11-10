from frappe.model.document import Document
from contract_ops.prepayment import validate_collection_plan


class ContractCollectionPlan(Document):
    def validate(self):validate_collection_plan(self)
