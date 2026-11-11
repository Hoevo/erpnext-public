from frappe.model.document import Document
from contract_ops.income import validate_contract_link, refresh_project_status

class PublicIncomeContractLink(Document):
    def validate(self):validate_contract_link(self)
    def on_update(self):refresh_project_status(self)
