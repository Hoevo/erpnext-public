"""Lifecycle acceptance on the explicitly isolated plan-test site only."""
import frappe
from frappe.utils import getdate
from contract_ops.planning import create_receipt_plans, get_contract_planning, receipt_due


def must_fail(fn):
    frappe.db.savepoint('expected_plan_error')
    try:
        fn()
    except (frappe.ValidationError, frappe.PermissionError):
        frappe.db.rollback(save_point='expected_plan_error')
        return
    raise AssertionError('Expected validation failure')


def run():
    assert frappe.local.site in ('erp.plan-test.localhost','erp.flows-test.localhost','erp.merged-test.localhost'), 'Never run on a business site'
    frappe.set_user('Administrator')
    company = frappe.get_doc('Company', 'Contract Ops Test Company')
    customer = 'Contract Ops Test Customer'
    contract = frappe.get_doc(dict(doctype='Sales Contract',contract_title='TEST Planning Lifecycle',
        company=company.name,customer=customer,currency='CNY',contract_amount=1000,signing_date='2026-11-09',
        start_date='2026-01-01',end_date='2026-12-31',notes='ISOLATED TEST DATA')).insert()
    contract.submit()
    calendar = frappe.get_doc(dict(doctype='Holiday List',holiday_list_name='TEST Plan Calendar '+frappe.generate_hash(length=6),
        from_date='2026-01-01',to_date='2027-12-31',holidays=[
            dict(holiday_date='2026-11-07',description='TEST Weekend'),dict(holiday_date='2026-11-08',description='TEST Weekend')])).insert()
    plans=[]
    for i, (start,end) in enumerate([('01-01','03-31'),('04-01','06-30'),('07-01','09-30'),('10-01','12-31')]):
        plans.append(frappe.get_doc(dict(doctype='Contract Billing Plan',sales_contract=contract.name,
            plan_title=f'TEST Quarter {i+1}',period_start='2026-'+start,period_end='2026-'+end,
            invoice_due_date='2026-'+start,planned_amount=250,due_rule='确认收票后工作日',due_days=1,holiday_list=calendar.name)).insert())

    def invoice(amount):
        doc=frappe.get_doc(dict(doctype='Sales Invoice',company=company.name,customer=customer,currency='CNY',
            custom_sales_contract=contract.name,posting_date='2026-11-06',due_date='2026-11-06',set_posting_time=1,
            debit_to=company.default_receivable_account,disable_rounded_total=1,
            items=[dict(item_code='CONTRACT-OPS-TEST-SERVICE',qty=1,rate=amount,
                income_account=company.default_income_account,cost_center=company.cost_center)])).insert()
        doc.submit()
        return doc

    inv=invoice(250)
    def vat(amount, allocations, inv_name=inv.name):
        doc=frappe.get_doc(dict(doctype='VAT Invoice Record',sales_contract=contract.name,company=company.name,
            customer=customer,currency='CNY',invoice_number='TEST-PLAN-'+frappe.generate_hash(length=12),
            invoice_date='2026-11-06',gross_amount=amount,tax_amount=0,sales_invoice=inv_name,
            plan_allocations=[dict(billing_plan=p,allocated_amount=a,sales_invoice=n) for p,a,n in allocations])).insert()
        doc.submit()
        return doc

    must_fail(lambda: vat(100,[]))
    must_fail(lambda: vat(100,[(plans[0].name,90,inv.name)]))
    first=vat(100,[(plans[0].name,100,inv.name)])
    second=vat(150,[(plans[0].name,150,inv.name)])
    assert frappe.db.count('Contract Receipt Plan',{'source_vat':first.name})==1
    create_receipt_plans(first)
    assert frappe.db.count('Contract Receipt Plan',{'source_vat':first.name})==1
    rp_name=frappe.db.get_value('Contract Receipt Plan',{'source_vat':first.name},'name')
    rp=frappe.get_doc('Contract Receipt Plan',rp_name)
    assert rp.status=='待确认收票' and not rp.due_date
    rp.received_on='2026-11-06';rp.save()
    assert getdate(rp.due_date)==getdate('2026-11-09')
    assert getdate(receipt_due(frappe._dict(due_rule='固定日期',fixed_due_date='2026-12-01')))==getdate('2026-12-01')
    assert getdate(receipt_due(frappe._dict(due_rule='开票后自然日',invoice_date='2026-11-06',due_days=15)))==getdate('2026-11-21')
    future=frappe.get_doc('Contract Receipt Plan',rp.name);future.received_on='2099-01-01'
    must_fail(future.save)
    must_fail(lambda: vat(1,[(plans[0].name,1,inv.name)]))
    altered=frappe.get_doc('Contract Billing Plan',plans[0].name);altered.due_days=2
    must_fail(altered.save)

    from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry
    payment=get_payment_entry('Sales Invoice',inv.name,party_amount=120,bank_account=company.default_bank_account,bank_amount=120)
    payment.reference_no='TEST-PLAN-PARTIAL';payment.reference_date='2026-11-09';payment.posting_date='2026-11-09'
    payment.insert();payment.submit()
    amounts=frappe.get_all('Contract Receipt Plan',filters={'sales_invoice':inv.name},fields=['received_amount'],order_by='invoice_date asc, source_vat asc, billing_plan asc, name asc')
    assert [r.received_amount for r in amounts]==[100,20], amounts
    assert sum(r.received_amount for r in amounts)==120
    must_fail(lambda: frappe.get_doc('VAT Invoice Record',first.name).cancel())
    payment.reload();payment.cancel()
    assert not frappe.db.get_value('Contract Receipt Plan',rp_name,'received_amount')
    frappe.get_doc('VAT Invoice Record',first.name).cancel()
    assert frappe.db.get_value('Contract Receipt Plan',rp_name,'cancelled')==1

    # One invoice covering two plan periods, with no duplicate cash attribution.
    inv2=invoice(200)
    moved=frappe.get_doc('Contract Receipt Plan',rp.name);moved.sales_invoice=inv2.name
    must_fail(moved.save)
    combined=vat(200,[(plans[1].name,100,inv2.name),(plans[2].name,100,inv2.name)],inv2.name)
    assert frappe.db.count('Contract Receipt Plan',{'source_vat':combined.name})==2
    inv3=invoice(50)
    unlinked=vat(50,[(plans[3].name,50,None)],None)
    unlinked_rp=frappe.get_doc('Contract Receipt Plan',frappe.db.get_value('Contract Receipt Plan',{'source_vat':unlinked.name},'name'))
    assert unlinked_rp.status=='待关联应收'
    unlinked_rp.sales_invoice=inv3.name;unlinked_rp.save()
    assert unlinked_rp.status=='待确认收票'
    receipt=frappe.get_doc('Contract Receipt Plan',unlinked_rp.name);receipt.expected_amount=999
    must_fail(receipt.save)
    result=get_contract_planning(contract.name)
    assert len(result['billing_plans'])==4 and len(result['receipt_plans'])==5
    assert sum(r.billed_amount for r in result['billing_plans'])==400
    # A group tax invoice may span two contracts, but its period allocation is mandatory.
    group=frappe.get_doc(dict(doctype='Contract Group',group_name='TEST Plan Combined Invoice')).insert()
    contract.reload();contract.contract_group=group.name;contract.save()
    other=frappe.get_doc(dict(doctype='Sales Contract',contract_title='TEST Planning Second Contract',
        company=company.name,customer=customer,currency='CNY',contract_group=group.name,
        contract_amount=500,signing_date='2026-11-09',start_date='2026-01-01',end_date='2026-12-31')).insert()
    other.submit()
    other_plan=frappe.get_doc(dict(doctype='Contract Billing Plan',sales_contract=other.name,
        plan_title='TEST Other Contract Period',period_start='2026-01-01',period_end='2026-12-31',
        invoice_due_date='2026-01-01',planned_amount=500,due_rule='开票后自然日',due_days=15)).insert()
    grouped=frappe.get_doc(dict(doctype='VAT Invoice Record',contract_group=group.name,company=company.name,
        customer=customer,currency='CNY',invoice_number='TEST-GROUP-'+frappe.generate_hash(length=10),
        invoice_date='2026-11-06',gross_amount=100,tax_amount=0))
    must_fail(grouped.insert)
    # A rolled-back insert already set the in-memory name flag; request a fresh series value.
    grouped.name=None
    grouped.flags.name_set=False
    grouped.plan_allocations=[]
    grouped.append('plan_allocations',dict(billing_plan=plans[3].name,allocated_amount=50))
    grouped.append('plan_allocations',dict(billing_plan=other_plan.name,allocated_amount=50))
    grouped.insert();grouped.submit()
    assert frappe.db.count('Contract Receipt Plan',{'source_vat':grouped.name})==2
    frappe.set_user('Guest')
    try:
        must_fail(lambda: get_contract_planning(contract.name))
    finally:
        frappe.set_user('Administrator')
    frappe.db.commit()
    return dict(status='passed',contract=contract.name,calendar=calendar.name,
        checks=['quarterly plans','missing allocation','allocation mismatch','split invoicing','idempotency',
            'unconfirmed receipt date','evidenced working calendar','overbilling','immutable rule snapshot',
            'partial receipts FIFO','blocked cancel after payment','cancel payment rollback',
            'cancel VAT audit retention','one VAT multiple periods','late invoice link','protected generated amount',
            'fixed/calendar due rules','future receipt rejection','source invoice binding','guest permission denial',
            'cross-contract group invoice','group allocation cannot be bypassed'])
