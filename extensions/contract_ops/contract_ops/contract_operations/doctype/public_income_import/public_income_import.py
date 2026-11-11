import frappe
from frappe.model.document import Document

class PublicIncomeImport(Document):
    def validate(self):
        if self.is_new() and self.flags.from_income_import:return
        frappe.throw('来源批次为不可改写的原始证据，请通过清单导入建立新批次')
    def on_trash(self):frappe.throw('原始来源批次不可删除')
