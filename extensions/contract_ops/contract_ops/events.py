import frappe
from frappe.utils import flt
from contract_ops.rules import validate_limit


def locked_contract(name):
    # Serialize invoice submissions on the same contract, including parallel requests.
    rows = frappe.db.sql("SELECT name FROM `tabSales Contract` WHERE name=%s FOR UPDATE", (name,))
    if not rows:
        frappe.throw("销售合同不存在")
    contract = frappe.get_doc("Sales Contract", name)
    contract.check_permission("read")
    if contract.docstatus != 1:
        frappe.throw("必须选择已提交的销售合同")
    return contract


def check_party(doc, contract):
    for field in ("company", "customer", "currency"):
        if doc.get(field) != contract.get(field):
            frappe.throw("发票的公司、客户和币种必须与合同一致")


def validate_sales_invoice(doc, method=None):
    if not doc.get("custom_sales_contract"):
        return
    contract = locked_contract(doc.custom_sales_contract)
    check_party(doc, contract)
    if contract.get("contract_group"):
        start = doc.get("custom_billing_period_start")
        end = doc.get("custom_billing_period_end")
        if not start or not end:
            frappe.throw("合同组协议的应收发票必须填写计费开始日期和计费结束日期")
        if start > end:
            frappe.throw("计费结束日期不得早于计费开始日期")
    if doc.is_return:
        frappe.throw("第一期暂不支持合同红字应收发票，请先完成业务冲销方案")
    if doc.get("is_pos"):
        frappe.throw("合同应收请使用标准销售发票与 Payment Entry 收款")
    # Some scanned legacy agreements state an annual unit price but no reliable
    # contract-period total.  Their displayed amount is explicitly marked as a
    # nominal annual reference, so it must not block source-verified invoices.
    if "合同总额未能从原件确认" in (contract.get("notes") or ""):
        return
    existing = frappe.db.sql(
        """SELECT COALESCE(SUM(CASE WHEN rounded_total != 0 THEN rounded_total ELSE grand_total END),0)
        FROM `tabSales Invoice` WHERE custom_sales_contract=%s AND docstatus=1 AND name!=%s""",
        (contract.name, doc.name or ""),
    )[0][0]
    try:
        validate_limit(contract.contract_amount, existing, flt(doc.rounded_total) or doc.grand_total)
    except ValueError as exc:
        frappe.throw(str(exc))
