"""Regression cases for the pre-merge audit, restricted to the disposable site."""
import frappe

from contract_ops import planning
from contract_ops.tests.helpers import make_contract
from contract_ops.tests.test_planning import must_fail


def run():
    assert frappe.local.site == 'erp.merged-test.localhost', 'Never run on a business site'
    frappe.set_user('Administrator')
    checks = []
    token = frappe.generate_hash(length=8)
    company = 'Contract Ops Test Company'
    customer = 'Contract Ops Test Customer'
    user = frappe.get_doc(dict(doctype='User', email=f'audit-{token}@example.invalid',
        first_name='Audit Tester', enabled=1, send_welcome_email=0, user_type='System User',
        roles=[dict(role='Accounts Manager')])).insert()

    def blocked(label, fn):
        must_fail(fn)
        checks.append(label)

    frappe.set_user(user.name)
    try:
        for label, fn in (
            ('migration requires administrator', lambda: planning.migrate_historical_plan_links([])),
            ('repair requires administrator', lambda: planning.repair_vat_receipt_plans('nonexistent')),
            ('single backfill requires administrator', lambda: planning.backfill_historical_vat('', '', '', 1)),
            ('bulk backfill requires administrator', lambda: planning.backfill_historical_vat_bulk('', [])),
        ):
            blocked(label, fn)
    finally:
        frappe.set_user('Administrator')

    def contract(**overrides):
        values = dict(contract_title='TEST Audit '+token, company=company, customer=customer,
            currency='CNY', contract_amount=1000, start_date='2026-01-01', end_date='2026-12-31')
        values.update(overrides)
        return make_contract(values)

    for field in ('legacy_sales_contract', 'legacy_source_sha256', 'legacy_source_snapshot'):
        blocked('cannot forge '+field, lambda field=field: contract(**{field: 'forged'}).insert())
    c = contract().insert(); c.submit()

    def plan(amount):
        return frappe.get_doc(dict(doctype='Contract Billing Plan', sales_contract=c.name,
            plan_title='TEST Audit '+token, planned_amount=amount, period_start='2026-01-01',
            period_end='2026-12-31', invoice_due_date='2026-11-09',
            due_rule='固定日期', fixed_due_date='2026-12-31')).insert()

    source, small, target = plan(100), plan(50), plan(850)
    vat = frappe.get_doc(dict(doctype='VAT Invoice Record', sales_contract=c.name,
        company=company, customer=customer, currency='CNY', invoice_date='2026-11-09',
        invoice_number='TEST-AUDIT-'+token, gross_amount=100, tax_amount=0,
        plan_allocations=[dict(billing_plan=source.name, allocated_amount=100)])).insert()
    vat.submit()
    receipt = frappe.db.get_value('Contract Receipt Plan', {'source_vat': vat.name}, 'name')
    row = dict(vat_plan_allocation=vat.plan_allocations[0].name, billing_plan=small.name,
               receipt_plan=receipt)
    blocked('migration respects target capacity', lambda: planning.migrate_historical_plan_links([row]))
    row['billing_plan'] = target.name
    # Omitting the matching receipt would leave the cash projection on the old period.
    blocked('migration requires matching receipt', lambda: planning.migrate_historical_plan_links([
        dict(vat_plan_allocation=row['vat_plan_allocation'], billing_plan=target.name)]))
    planning.migrate_historical_plan_links([row])
    assert frappe.db.get_value('Contract Receipt Plan', receipt, 'billing_plan') == target.name
    assert frappe.db.get_value('Contract Billing Plan', source.name, 'billed_amount') == 0
    assert frappe.db.get_value('Contract Billing Plan', target.name, 'billed_amount') == 100
    checks.append('authorized migration preserves matching projections')

    # An existing submitted invoice must never be linked to a different tax party.
    invoice = frappe.get_all('Sales Invoice', filters={'company': company, 'docstatus': 1},
                            fields=['name', 'customer', 'currency'], limit_page_length=1)[0]
    for field, value in (('company', 'other company'), ('customer', 'other customer'), ('currency', 'USD')):
        data = dict(doctype='VAT Invoice Record', contract_group=frappe.get_all('Contract Group', pluck='name')[0],
            company=company, customer=invoice.customer, currency=invoice.currency,
            sales_invoice=invoice.name, invoice_number='TEST-AUDIT-PARTY-'+token,
            gross_amount=1, tax_amount=0, invoice_date='2026-11-09')
        data[field] = value
        blocked('linked invoice rejects mismatched '+field, lambda data=data: frappe.get_doc(data).validate())

    # Receipt repair must not create a circular receivable plan for cash-first contracts.
    funded = frappe.db.sql('''SELECT DISTINCT v.name FROM `tabVAT Invoice Record` v
        JOIN `tabVAT Plan Allocation` a ON a.parent=v.name
        JOIN `tabContract Billing Plan` b ON b.name=a.billing_plan
        WHERE v.docstatus=1 AND b.source_collection_plan IS NOT NULL AND b.source_collection_plan!=''
        AND NOT EXISTS (SELECT 1 FROM `tabContract Billing Plan` other
            JOIN `tabVAT Plan Allocation` oa ON oa.billing_plan=other.name
            WHERE oa.parent=v.name AND COALESCE(other.source_collection_plan,'')='') LIMIT 1''')
    assert funded, 'Run the two-flow/merged VAT suite first'
    before = frappe.db.count('Contract Receipt Plan', {'source_vat': funded[0][0]})
    planning.repair_vat_receipt_plans(funded[0][0])
    assert frappe.db.count('Contract Receipt Plan', {'source_vat': funded[0][0]}) == before == 0
    checks.append('cash-first repair does not create circular receipt plans')
    from contract_ops.api import get_contract_group_summary
    group = frappe.get_doc(dict(doctype='Contract Group', group_name='TEST Audit Currency '+token)).insert()
    for currency in ('CNY', 'USD'):
        agreement = contract(currency=currency, contract_group=group.name).insert()
        agreement.submit()
    blocked('mixed currency totals are rejected', lambda: get_contract_group_summary(group.name))
    usd_group = frappe.get_doc(dict(doctype='Contract Group', group_name='TEST Audit USD '+token)).insert()
    usd = contract(currency='USD', contract_group=usd_group.name).insert(); usd.submit()
    summary = get_contract_group_summary(usd_group.name)
    assert summary['currency'] == 'USD' and summary['totals']['contract_amount'] == 1000
    checks.append('single currency is carried into summary')
    linked = frappe.db.sql('''SELECT DISTINCT si.name FROM `tabSales Invoice` si
        JOIN `tabContract Receipt Plan` rp ON rp.sales_invoice=si.name
        WHERE si.docstatus=1 AND si.company=%s LIMIT 1''', company)[0][0]
    def detach_invoice():
        doc = frappe.get_doc('Sales Invoice', linked)
        doc.custom_sales_contract = None
        doc.save()
    blocked('submitted linked invoice cannot lose contract', detach_invoice)
    def rewrite_period():
        doc = frappe.get_doc('Sales Invoice', linked)
        doc.custom_billing_period_start = '2026-02-01'
        doc.save()
    blocked('submitted linked invoice cannot change billing period', rewrite_period)
    frappe.db.commit()
    return dict(passed=len(checks), checks=checks)
