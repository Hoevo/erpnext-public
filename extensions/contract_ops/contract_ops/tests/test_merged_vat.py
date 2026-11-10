from contract_ops.tests.helpers import make_contract
"""Combined tax bills across contracts and payments, on the isolated clone only."""
import frappe
from contract_ops.tests.test_planning import must_fail
from contract_ops.vat_links import prepare_merged_vat, get_vat_relationships, get_merge_candidates
from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry


def run():
    assert frappe.local.site=='erp.merged-test.localhost'
    frappe.set_user('Administrator')
    company=frappe.get_doc('Company','Contract Ops Test Company');customer='Contract Ops Test Customer'
    def contract(title,flow='先款后票',amount=1000):
        group=frappe.get_doc(dict(doctype='Contract Group',group_name='TEST Merge '+title)).insert()
        c=make_contract(dict(doctype='Contract',contract_title='TEST Merge '+title,billing_flow=flow,
            company=company.name,customer=customer,currency='CNY',contract_amount=amount,contract_group=group.name,
            start_date='2026-01-01',end_date='2026-12-31')).insert();c.submit();return c
    def collection(c,amount):
        return frappe.get_doc(dict(doctype='Contract Collection Plan',sales_contract=c.name,plan_title='TEST Merge '+c.name,
            period_start='2026-01-01',period_end='2026-12-31',receipt_due_date='2026-11-06',planned_amount=amount,
            invoice_trigger='本期收齐',invoice_days=2,day_type='自然日')).insert()
    def advance(cp,amount):
        pe=frappe.get_doc(dict(doctype='Payment Entry',payment_type='Receive',company=company.name,posting_date='2026-11-09',
            party_type='Customer',party=customer,paid_from=company.default_receivable_account,paid_to=company.default_bank_account,
            paid_from_account_currency='CNY',paid_to_account_currency='CNY',paid_amount=amount,received_amount=amount,
            source_exchange_rate=1,target_exchange_rate=1,reference_no='TEST-MERGE-'+frappe.generate_hash(length=6),
            reference_date='2026-11-06',custom_actual_receipt_date='2026-11-06',
            custom_collection_allocations=[dict(collection_plan=cp.name,allocated_amount=amount)])).insert();pe.submit();return pe
    def funded(c,amounts):
        cp=collection(c,sum(amounts));payments=[advance(cp,a) for a in amounts]
        bp=frappe.db.get_value('Contract Billing Plan',{'source_collection_plan':cp.name,'source_cancelled':0},'name')
        return bp,payments
    def tax(rows,**overrides):
        data=prepare_merged_vat(company.name,customer,'CNY',rows,invoice_date='2026-11-09')
        data.update(doctype='VAT Invoice Record',invoice_number='TEST-MERGE-VAT-'+frappe.generate_hash(length=8),tax_amount=0)
        data.update(overrides)
        vat=frappe.get_doc(data).insert();vat.submit();return vat
    ca=contract('A');pa,pays_a=funded(ca,[400,600])
    cb=contract('B');pb,pays_b=funded(cb,[300,700])
    candidates=get_merge_candidates(company.name,customer)
    assert {pa,pb}.issubset({r['billing_plan'] for r in candidates})
    rows=[dict(billing_plan=pa,allocated_amount=1000),dict(billing_plan=pb,allocated_amount=400)]
    must_fail(lambda:prepare_merged_vat(company.name,'Guest','CNY',rows))
    must_fail(lambda:prepare_merged_vat(company.name,customer,'USD',rows))
    must_fail(lambda:prepare_merged_vat('Wrong Company',customer,'CNY',rows))
    must_fail(lambda:prepare_merged_vat(company.name,customer,'CNY',rows,invoice_date='2026-11-05'))
    merged=tax(rows,payment_allocations=[dict(payment_entry=pays_a[0].name,allocated_amount=9999)])
    assert not merged.sales_contract and not merged.contract_group
    assert len(merged.plan_allocations)==2 and len(merged.payment_allocations)==4
    assert sum(r.allocated_amount for r in merged.payment_allocations)==1400
    assert len({r.payment_entry for r in merged.payment_allocations})==4
    trace=get_vat_relationships(merged.name)
    assert len(trace['contracts'])==2 and len(trace['payment_allocations'])==4
    from contract_ops.api import get_contract_group_summary
    assert get_contract_group_summary(ca.contract_group)['totals']['vat_invoices']==1000
    assert get_contract_group_summary(cb.contract_group)['totals']['vat_invoices']==400
    assert frappe.db.count('Contract Receipt Plan',{'source_vat':merged.name})==0
    rest=tax([dict(billing_plan=pb,allocated_amount=600)])
    assert sum(r.allocated_amount for r in rest.payment_allocations)==600
    assert len(rest.payment_allocations)==1 and rest.payment_allocations[0].payment_entry==pays_b[1].name
    must_fail(lambda:tax([dict(billing_plan=pb,allocated_amount=1)]))
    must_fail(lambda:frappe.get_doc('Payment Entry',pays_a[0].name).cancel())
    merged.cancel()
    assert any(r['billing_plan']==pa and r['available_amount']==1000 for r in get_merge_candidates(company.name,customer))
    pays_a[0].reload();pays_a[0].cancel()
    assert frappe.db.get_value('Contract Billing Plan',pa,'source_cancelled')==1
    must_fail(lambda:tax([dict(billing_plan=pa,allocated_amount=1)]))

    # Pre-upgrade bills may have funding evidence but no stored payment-allocation rows.
    old_c=contract('Legacy',amount=200);old_plan,old_pays=funded(old_c,[80,120])
    old=tax([dict(billing_plan=old_plan,allocated_amount=100)])
    frappe.db.delete('VAT Payment Allocation',{'parent':old.name})
    legacy=get_vat_relationships(old.name)
    assert sum(r['allocated_amount'] for r in legacy['payment_allocations'])==100
    assert frappe.db.count('VAT Payment Allocation',{'parent':old.name})==0
    later=tax([dict(billing_plan=old_plan,allocated_amount=100)])
    assert len(later.payment_allocations)==1 and later.payment_allocations[0].payment_entry==old_pays[1].name
    assert later.payment_allocations[0].allocated_amount==100

    # One period may cover multiple receivables and collect through multiple payments.
    first=contract('Invoice First','先票后款',500)
    plan=frappe.get_doc(dict(doctype='Contract Billing Plan',sales_contract=first.name,plan_title='TEST Merge AR Split',
        period_start='2026-01-01',period_end='2026-12-31',invoice_due_date='2026-11-06',planned_amount=500,
        due_rule='开票后自然日',due_days=15)).insert()
    def si(amount):
        d=frappe.get_doc(dict(doctype='Sales Invoice',company=company.name,customer=customer,currency='CNY',
            custom_sales_contract=first.name,custom_billing_period_start='2026-01-01',custom_billing_period_end='2026-12-31',
            posting_date='2026-11-06',due_date='2026-11-21',debit_to=company.default_receivable_account,disable_rounded_total=1,
            items=[dict(item_code='CONTRACT-OPS-TEST-SERVICE',qty=1,rate=amount,income_account=company.default_income_account,cost_center=company.cost_center)])).insert();d.submit();return d
    i1,i2=si(200),si(300)
    vat=tax([dict(billing_plan=plan.name,allocated_amount=200,sales_invoice=i1.name),dict(billing_plan=plan.name,allocated_amount=300,sales_invoice=i2.name)],
        invoice_allocations=[dict(sales_invoice=i1.name,allocated_amount=200),dict(sales_invoice=i2.name,allocated_amount=300)])
    must_fail(lambda:frappe.get_doc(dict(doctype='VAT Invoice Record',company=company.name,customer=customer,currency='CNY',
        invoice_date='2026-11-09',invoice_number='TEST-MERGE-BYPASS-'+frappe.generate_hash(length=6),gross_amount=1,tax_amount=0,
        invoice_allocations=[dict(sales_invoice=i1.name,allocated_amount=1)])).insert())
    assert frappe.db.count('Contract Receipt Plan',{'source_vat':vat.name})==2
    assert sum(r.expected_amount for r in frappe.get_all('Contract Receipt Plan',filters={'source_vat':vat.name},fields=['expected_amount']))==500
    for inv,amount in [(i1,100),(i1,100),(i2,300)]:
        pe=get_payment_entry('Sales Invoice',inv.name,party_amount=amount,bank_account=company.default_bank_account,bank_amount=amount)
        pe.reference_no='TEST-MERGE-AR-'+frappe.generate_hash(length=6);pe.reference_date='2026-11-09';pe.insert();pe.submit()
    ar_trace=get_vat_relationships(vat.name)
    assert len(ar_trace['invoice_settlements'])==3
    assert sum(r['allocated_to_invoice'] for r in ar_trace['invoice_settlements'])==500
    assert frappe.db.get_value('Contract Billing Plan',plan.name,'received_amount')==500
    ui_contract=contract('UI '+frappe.generate_hash(length=5),amount=200)
    ui_plan,ui_payments=funded(ui_contract,[80,120])
    frappe.db.commit()
    return dict(status='passed',candidate_contracts=[cb.name],candidate_plan=pb,paid_contract=cb.name,
        merged_vat=merged.name,shared_vat=vat.name,ui_plan=ui_plan,company=company.name,customer=customer,
        checks=['customer candidate filter','cross-group merge','same company/customer/currency guards','bank date guard',
            'one tax four payments two contracts','server-managed payment sources','source trace drilldown',
            'no circular receipt plan','partial reuse of one payment','no duplicate funding','cancel tax releases budget',
            'cancel payment protected','cancelled source blocked','legacy source reserve','legacy read without rewrite',
            'one period two receivables','two separate receipt plans','multiple payments settle one tax invoice'])
