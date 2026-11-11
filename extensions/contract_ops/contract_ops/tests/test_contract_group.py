"""Transaction-rolled-back acceptance scenarios for contract group aggregation."""

import json

import frappe
from frappe.utils import flt

from contract_ops.api import get_contract_group_summary


GROUP = "CG-2026-00001"
CONTRACT_A = "SC-2026-00003"
CONTRACT_B = "SC-2026-00004"


def _total(summary, field):
    return flt(summary["totals"][field])


def _invoice(contract, rate, label):
    contract_doc = frappe.get_doc("Contract", contract)
    company = frappe.get_doc("Company", contract_doc.company)
    return frappe.get_doc({
        "doctype": "Sales Invoice",
        "company": contract_doc.company,
        "customer": contract_doc.party_name,
        "currency": "CNY",
        "custom_sales_contract": contract,
        "custom_billing_period_start": "2026-01-01",
        "custom_billing_period_end": "2026-12-31",
        "posting_date": "2026-11-08",
        "due_date": "2026-11-08",
        "set_posting_time": 1,
        "debit_to": "112202 - 小区应收款 - CYWY",
        "disable_rounded_total": 1,
        "remarks": f"合同组汇总验收临时应收：{label}；事务回滚不留存。",
        "items": [{
            "item_code": "FC-SITE-RENT",
            "item_name": "丰巢智能柜场地租金",
            "description": label,
            "qty": 1,
            "rate": rate,
            "income_account": "50011501 - 场地租金 - CYWY",
            "cost_center": company.cost_center,
        }],
    }).insert(ignore_permissions=True)


def _payment(contract_a_invoice, contract_b_invoice):
    contract = frappe.get_doc("Contract", CONTRACT_A)
    return frappe.get_doc({
        "doctype": "Payment Entry",
        "payment_type": "Receive",
        "company": contract.company,
        "posting_date": "2026-11-08",
        "party_type": "Customer",
        "party": contract.party_name,
        "paid_from": "112202 - 小区应收款 - CYWY",
        "paid_to": "100205 - 建行金桥基本户 - CYWY",
        "paid_from_account_currency": "CNY",
        "paid_to_account_currency": "CNY",
        "paid_amount": 250,
        "received_amount": 250,
        "source_exchange_rate": 1,
        "target_exchange_rate": 1,
        "reference_no": "ACCEPTANCE-GROUP-ALLOC",
        "reference_date": "2026-11-08",
        "remarks": "合同组跨协议核销验收临时收款；事务回滚不留存。",
        "references": [
            {"reference_doctype": "Sales Invoice", "reference_name": contract_a_invoice.name,
             "total_amount": 100, "outstanding_amount": 100, "allocated_amount": 100},
            {"reference_doctype": "Sales Invoice", "reference_name": contract_b_invoice.name,
             "total_amount": 200, "outstanding_amount": 200, "allocated_amount": 150},
        ],
    }).insert(ignore_permissions=True)


def _advance():
    contract = frappe.get_doc("Contract", CONTRACT_A)
    return frappe.get_doc({
        "doctype": "Payment Entry",
        "payment_type": "Receive",
        "company": contract.company,
        "posting_date": "2026-11-08",
        "party_type": "Customer",
        "party": contract.party_name,
        "paid_from": "112202 - 小区应收款 - CYWY",
        "paid_to": "100205 - 建行金桥基本户 - CYWY",
        "paid_from_account_currency": "CNY",
        "paid_to_account_currency": "CNY",
        "paid_amount": 25,
        "received_amount": 25,
        "source_exchange_rate": 1,
        "target_exchange_rate": 1,
        "custom_contract_group": GROUP,
        "reference_no": "ACCEPTANCE-GROUP-ADVANCE",
        "reference_date": "2026-11-08",
        "remarks": "合同组未分配预收款验收临时收款；事务回滚不留存。",
    }).insert(ignore_permissions=True)


def run_acceptance():
    """Exercise multi-invoice, cross-contract allocation and cancellation without commits."""
    assert frappe.local.site == 'erp.merged-test.localhost', 'Never run on a business site'
    frappe.db.sql("SAVEPOINT contract_group_acceptance")
    baseline = get_contract_group_summary(GROUP)
    try:
        inv_a = _invoice(CONTRACT_A, 100, "中大苑1号柜验收期")
        inv_a.submit()
        inv_b = _invoice(CONTRACT_B, 200, "中大苑3号柜验收期")
        inv_b.submit()
        payment = _payment(inv_a, inv_b)
        payment.submit()
        advance = _advance()
        advance.submit()
        after_allocate = get_contract_group_summary(GROUP)
        assert _total(after_allocate, "contract_amount") == _total(baseline, "contract_amount")
        assert _total(after_allocate, "vat_invoices") == _total(baseline, "vat_invoices")
        assert _total(after_allocate, "receivables") == _total(baseline, "receivables") + 300
        assert _total(after_allocate, "receipts") == _total(baseline, "receipts") + 250
        assert _total(after_allocate, "outstanding") == _total(baseline, "outstanding") + 50
        assert any(flt(row.unallocated_amount) == 25 for row in after_allocate["unallocated_advances"])

        advance.cancel()
        payment.cancel()
        frappe.get_doc("Sales Invoice", inv_a.name).cancel()
        after_cancel = get_contract_group_summary(GROUP)
        assert _total(after_cancel, "receivables") == _total(baseline, "receivables") + 200
        assert _total(after_cancel, "receipts") == _total(baseline, "receipts")
        assert _total(after_cancel, "outstanding") == _total(baseline, "outstanding") + 200
        assert not after_cancel["unallocated_advances"]
        return {
            "status": "passed",
            "baseline": baseline["totals"],
            "after_allocate": after_allocate["totals"],
            "after_cancel": after_cancel["totals"],
            "rollback": "pending",
        }
    finally:
        frappe.db.sql("ROLLBACK TO SAVEPOINT contract_group_acceptance")
