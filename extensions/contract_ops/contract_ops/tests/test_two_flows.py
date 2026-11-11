from contract_ops.tests.helpers import make_contract
"""Isolated acceptance of payment-first lifecycles; never run on the business site."""
import frappe
from frappe.utils import getdate
from contract_ops.tests.test_planning import must_fail
from contract_ops.prepayment import sync_collection
from contract_ops.planning import get_contract_planning, receipt_due


def run():
    assert frappe.local.site in ('erp.flows-test.localhost','erp.merged-test.localhost')
    frappe.set_user('Administrator')
    company=frappe.get_doc('Company','Contract Ops Test Company')
    customer='Contract Ops Test Customer'
    calendar=frappe.get_doc(dict(doctype='Holiday List',holiday_list_name='TEST Flow Calendar '+frappe.generate_hash(length=5),
        from_date='2026-01-01',to_date='2027-12-31',holidays=[dict(holiday_date='2026-11-07',description='rest'),dict(holiday_date='2026-11-08',description='rest')])).insert()
    def contract(label):
        d=make_contract(dict(doctype='Contract',contract_title='TEST Payment First '+label,billing_flow='先款后票',
            company=company.name,customer=customer,currency='CNY',contract_amount=1000,
            start_date='2026-01-01',end_date='2026-12-31')).insert();d.submit();return d
    def collection(c,trigger,day_type='自然日'):
        return frappe.get_doc(dict(doctype='Contract Collection Plan',sales_contract=c.name,plan_title='TEST '+trigger,
            period_start='2026-01-01',period_end='2026-03-31',receipt_due_date='2026-11-06',planned_amount=1000,
            invoice_trigger=trigger,invoice_days=2 if day_type=='自然日' else 1,day_type=day_type,
            holiday_list=calendar.name if day_type=='工作日' else None)).insert()
    def payment(plan,amount,date='2026-11-06'):
        return frappe.get_doc(dict(doctype='Payment Entry',payment_type='Receive',company=company.name,
            posting_date='2026-11-09',party_type='Customer',party=customer,paid_from=company.default_receivable_account,
            paid_to=company.default_bank_account,paid_from_account_currency='CNY',paid_to_account_currency='CNY',
            paid_amount=amount,received_amount=amount,source_exchange_rate=1,target_exchange_rate=1,
            reference_no='TEST-FLOW-'+frappe.generate_hash(length=5),reference_date=date,
            custom_actual_receipt_date=date,custom_collection_allocations=[dict(collection_plan=plan.name,allocated_amount=amount)])).insert()
    def tax(c,plan,amount):
        d=frappe.get_doc(dict(doctype='VAT Invoice Record',sales_contract=c.name,company=company.name,
            customer=customer,currency='CNY',invoice_number='TEST-FLOW-VAT-'+frappe.generate_hash(length=8),
            invoice_date='2026-11-09',gross_amount=amount,tax_amount=0,
            plan_allocations=[dict(billing_plan=plan,allocated_amount=amount)])).insert();d.submit();return d
    def plans(p):return frappe.get_all('Contract Billing Plan',filters={'source_collection_plan':p.name,'source_cancelled':0},fields=['name','planned_amount','invoice_due_date','received_amount','billed_amount','source_key'],order_by='creation asc')

    c=contract('Per Receipt');p=collection(c,'每笔到账')
    assert not plans(p)
    manual=frappe.get_doc(dict(doctype='Contract Billing Plan',sales_contract=c.name,plan_title='invalid',period_start='2026-01-01',
        period_end='2026-03-31',invoice_due_date='2026-11-09',planned_amount=100,due_rule='固定日期',fixed_due_date='2026-11-09'))
    must_fail(manual.insert)
    missing=frappe.get_doc(dict(doctype='VAT Invoice Record',sales_contract=c.name,company=company.name,customer=customer,currency='CNY',
        invoice_number='TEST-EARLY-'+frappe.generate_hash(length=8),invoice_date='2026-11-09',gross_amount=100,tax_amount=0))
    must_fail(missing.insert)
    # Bad bank dates cannot create invoice obligations.
    must_fail(lambda:payment(p,100,None))
    must_fail(lambda:payment(p,100,'2099-01-01'))
    a=payment(p,400);a.submit()
    assert len(plans(p))==1 and plans(p)[0].planned_amount==400
    assert getdate(plans(p)[0].invoice_due_date)==getdate('2026-11-08')
    sync_collection(p.name);assert len(plans(p))==1
    b=payment(p,600,'2026-11-09');b.submit()
    assert len(plans(p))==2 and sum(x.planned_amount for x in plans(p))==1000
    assert getdate(plans(p)[1].invoice_due_date)==getdate('2026-11-11')
    must_fail(lambda:payment(p,1))
    bad=frappe.get_doc('Contract Billing Plan',plans(p)[0].name);bad.invoice_due_date='2026-12-01';must_fail(bad.save)
    invoice=tax(c,plans(p)[0].name,250)
    assert frappe.db.count('Contract Receipt Plan',{'source_vat':invoice.name})==0
    assert frappe.db.get_value('Contract Billing Plan',plans(p)[0].name,'billed_amount')==250
    must_fail(lambda:frappe.get_doc('Payment Entry',a.name).cancel())
    invoice.cancel()
    assert frappe.db.get_value('Contract Billing Plan',plans(p)[0].name,'billed_amount')==0
    a.reload();a.cancel()
    assert len(plans(p))==1
    assert frappe.db.count('Contract Billing Plan',{'source_collection_plan':p.name,'source_cancelled':1})==1
    assert frappe.db.get_value('Contract Collection Plan',p.name,'received_amount')==600
    must_fail(lambda:tax(c,frappe.db.get_value('Contract Billing Plan',{'source_collection_plan':p.name,'source_cancelled':1},'name'),100))
    changed=frappe.get_doc('Contract',c.name);changed.billing_flow='先票后款';must_fail(changed.save)

    full_c=contract('Full Period');full=collection(full_c,'本期收齐','工作日')
    first=payment(full,400);first.submit();assert not plans(full)
    second=payment(full,600);second.submit()
    generated=plans(full);assert len(generated)==1 and generated[0].planned_amount==1000
    assert getdate(generated[0].invoice_due_date)==getdate('2026-11-09')
    evidence=frappe.get_doc('Contract Billing Plan',generated[0].name).funding_evidence
    assert len(evidence)==2 and sum(r.funded_amount for r in evidence)==1000
    second.reload();second.cancel();assert not plans(full)
    replacement=payment(full,600,'2026-11-09');replacement.submit()
    assert len(plans(full))==1 and plans(full)[0].name!=generated[0].name
    assert getdate(plans(full)[0].invoice_due_date)==getdate('2026-11-10')
    # A still-unreceived period is never published as a generated invoice obligation.
    assert len(get_contract_planning(full_c.name)['collection_plans'])==1
    assert getdate(receipt_due(frappe._dict(due_rule='开票后工作日',invoice_date='2026-11-06',due_days=1,holiday_list=calendar.name)))==getdate('2026-11-09')
    assert getdate(receipt_due(frappe._dict(due_rule='确认收票后自然日',invoice_date='2026-11-06',received_on='2026-11-06',due_days=2)))==getdate('2026-11-08')
    # Accounting receivables can exist before the external tax invoice.
    match_c=contract('Matched Receivable');match=collection(match_c,'每笔到账')
    foreign_c=contract('Wrong Receivable');foreign=collection(foreign_c,'每笔到账')
    si=frappe.get_doc(dict(doctype='Sales Invoice',set_posting_time=1,company=company.name,customer=customer,currency='CNY',
        custom_sales_contract=match_c.name,posting_date='2026-11-09',due_date='2026-11-09',
        debit_to=company.default_receivable_account,disable_rounded_total=1,
        items=[dict(item_code='CONTRACT-OPS-TEST-SERVICE',qty=1,rate=250,income_account=company.default_income_account,cost_center=company.cost_center)])).insert()
    si.submit()
    from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry
    pe=get_payment_entry('Sales Invoice',si.name,party_amount=250,bank_account=company.default_bank_account,bank_amount=250)
    pe.reference_no='TEST-MATCH-'+frappe.generate_hash(length=4);pe.reference_date='2026-11-09';pe.custom_actual_receipt_date='2026-11-09'
    pe.append('custom_collection_allocations',dict(collection_plan=foreign.name,allocated_amount=250))
    must_fail(pe.insert)
    pe=get_payment_entry('Sales Invoice',si.name,party_amount=250,bank_account=company.default_bank_account,bank_amount=250)
    pe.reference_no='TEST-MATCH-'+frappe.generate_hash(length=4);pe.reference_date='2026-11-09';pe.custom_actual_receipt_date='2026-11-09'
    pe.append('custom_collection_allocations',dict(collection_plan=match.name,allocated_amount=250));pe.insert();pe.submit()
    assert len(plans(match))==1 and plans(match)[0].planned_amount==250
    assert frappe.db.get_value('Sales Invoice',si.name,'outstanding_amount')==0
    assert frappe.db.count('GL Entry',{'voucher_type':'Payment Entry','voucher_no':pe.name,'is_cancelled':0})==2
    frappe.db.commit()
    return dict(status='passed',per_receipt_contract=c.name,full_period_contract=full_c.name,
        collection_plan=p.name,full_collection_plan=full.name,
        checks=['contract flow selector','no invoice plan before cash','manual invoice bypass blocked',
            'early tax bypass blocked','missing bank date','future bank date','per receipt amount and date',
            'idempotency','partial receipts conserved','over-allocation blocked','generated deadline immutable',
            'no circular receipt plan','tax completion refresh','cancel payment blocked after tax',
            'tax cancellation refresh','cancel cash voids generated plan','void plan cannot invoice',
            'flow switch blocked','full-period waits for complete cash','full-period calendar date',
            'multiple funding evidence','cancel after full cash','replacement preserves old evidence',
            'invoice-first extra date rules','same-contract receivable allocation','cross-contract cash guard','no duplicate ledger posting'])
