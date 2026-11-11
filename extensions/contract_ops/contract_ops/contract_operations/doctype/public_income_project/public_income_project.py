from frappe.model.document import Document
from contract_ops.income import validate_project

class PublicIncomeProject(Document):
    def validate(self):validate_project(self)
