"""Read-only contract-group aggregation endpoints."""

import frappe
from frappe.utils import flt


def _sum(rows, field):
    return sum(flt(row.get(field)) for row in rows)


@frappe.whitelist()
def get_contract_group_summary(contract_group: str):
    """Return separately scoped, drill-down-ready totals for one contract group."""
    group = frappe.get_doc("Contract Group", contract_group)
    group.check_permission("read")
    contracts = frappe.get_all(
        "Contract",
        filters={"contract_group": group.name, "docstatus": 1, "party_type": "Customer", "contract_ops_enabled": 1},
        fields=["name", "contract_title", "external_number", "contract_amount", "start_date", "end_date"],
        order_by="external_number asc",
    )
    contract_names = [row.name for row in contracts]
    # A group may span companies: fail closed rather than leak amounts or present partial totals.
    for name in contract_names:
        frappe.get_doc('Contract', name).check_permission('read')
    if not contract_names:
        return {"contracts": [], "vat_invoices": [], "receivables": [], "receipts": [], "unallocated_advances": [],
                "totals": {"contract_amount": 0, "vat_invoices": 0, "receivables": 0, "receipts": 0, "outstanding": 0}}

    from contract_ops.vat_links import group_vat_rows
    vat_invoices = group_vat_rows(group.name, contract_names)
    receivables = frappe.get_all(
        "Sales Invoice",
        filters={"custom_sales_contract": ["in", contract_names], "docstatus": 1},
        fields=["name", "custom_sales_contract", "posting_date", "grand_total", "outstanding_amount",
                "custom_billing_period_start", "custom_billing_period_end"],
        order_by="posting_date asc, name asc",
    )
    invoice_names = [row.name for row in receivables]
    for name in invoice_names:
        frappe.get_doc('Sales Invoice', name).check_permission('read')
    receipts = []
    if invoice_names:
        receipts = frappe.db.sql(
            """SELECT pe.name AS payment_entry, per.reference_name AS sales_invoice,
                      per.allocated_amount, pe.posting_date
                 FROM `tabPayment Entry Reference` per
                 INNER JOIN `tabPayment Entry` pe ON pe.name = per.parent
                WHERE pe.docstatus = 1
                  AND per.reference_doctype = 'Sales Invoice'
                  AND per.reference_name IN %(invoice_names)s
                ORDER BY pe.posting_date ASC, pe.name ASC, per.idx ASC""",
            {"invoice_names": tuple(invoice_names)},
            as_dict=True,
        )
    unallocated_advances = frappe.get_all(
        "Payment Entry",
        filters={"custom_contract_group": group.name, "docstatus": 1, "unallocated_amount": [">", 0]},
        fields=["name", "posting_date", "unallocated_amount"],
        order_by="posting_date asc, name asc",
    )
    for name in {row.payment_entry for row in receipts} | {row.name for row in unallocated_advances}:
        frappe.get_doc('Payment Entry', name).check_permission('read')
    return {
        "contracts": contracts,
        "vat_invoices": vat_invoices,
        "receivables": receivables,
        "receipts": receipts,
        "unallocated_advances": unallocated_advances,
        "totals": {
            "contract_amount": _sum(contracts, "contract_amount"),
            "vat_invoices": _sum(vat_invoices, "gross_amount"),
            "receivables": _sum(receivables, "grand_total"),
            "receipts": _sum(receipts, "allocated_amount"),
            "outstanding": _sum(receivables, "outstanding_amount"),
        },
    }
