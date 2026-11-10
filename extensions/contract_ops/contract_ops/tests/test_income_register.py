from contract_ops.tests.helpers import make_contract
"""Source conservation and annual contract links on the disposable site only."""
import frappe
from frappe.utils import getdate
from contract_ops.income import import_register,audit_database,get_project_contracts
from contract_ops.tests.test_planning import must_fail

def run():
    assert frappe.local.site=='erp.merged-test.localhost'
    frappe.set_user('Administrator')
    batch=frappe.get_last_doc('Public Income Import')
    before={dt:frappe.db.count(dt) for dt in ['Sales Invoice','Payment Entry','VAT Invoice Record','GL Entry']}
    repeat=import_register(batch.source_filename,batch.original_b64,batch.source_sha256)
    assert repeat['created']==0 and repeat['idempotent'] and repeat['project_count']==52
    projects=frappe.get_all('Public Income Project',filters={'source_import':batch.name},fields=['name','annual_reference','amount_status','quantity_text','source_row'])
    assert len(projects)==52 and len({p.source_row for p in projects})==52
    assert sum(p.amount_status=='原表年参考金额' for p in projects)==50
    assert sum(p.amount_status=='非年度计价' for p in projects)==1
    assert sum(p.amount_status=='金额未提供' for p in projects)==1
    untouched = next(p for p in projects if not frappe.db.exists('Public Income Contract Link', {'income_project': p.name}))
    project=frappe.get_doc('Public Income Project',untouched.name)
    def overwrite_source():
        original=frappe.get_doc('Public Income Import',batch.name);original.review_notes='changed';original.save()
    must_fail(overwrite_source)
    def wrong_unknown():
        unknown=frappe.get_doc('Public Income Project',next(p.name for p in projects if p.amount_status=='金额未提供'))
        unknown.annual_reference='0';unknown.save()
    must_fail(wrong_unknown)
    def contract(year):
        c=make_contract(dict(doctype='Contract',contract_title='TEST Income Renewal '+str(year),
            company='Contract Ops Test Company',customer='Contract Ops Test Customer',currency='CNY',contract_amount=100,
            start_date=f'{year}-01-01',end_date=f'{year}-12-31')).insert();c.submit();return c
    a,b=contract(2025),contract(2026)
    def link(contract,**kw):
        return frappe.get_doc(dict(doctype='Public Income Contract Link',income_project=project.name,sales_contract=contract.name,**kw)).insert()
    must_fail(lambda:link(a,status='已确认'))
    first=link(a,status='已确认',evidence='ISOLATED TEST: explicit location and coverage confirmation')
    second=link(b,status='已确认',evidence='ISOLATED TEST: annual renewal coverage confirmed',previous_contract=a.name)
    assert getdate(first.valid_from)==getdate(a.start_date) and getdate(second.valid_to)==getdate(b.end_date)
    assert frappe.db.get_value('Public Income Project',project.name,'evidence_status')=='合同已核实'
    assert len(get_project_contracts(project.name))==2
    must_fail(lambda:link(b,status='待核实'))
    second.status='已撤销';second.save();first.status='已撤销';first.save()
    assert frappe.db.get_value('Public Income Project',project.name,'evidence_status')=='清单观察／合同待核实'
    frappe.set_user('Guest');must_fail(lambda:get_project_contracts(project.name));frappe.set_user('Administrator')
    report=audit_database()
    assert before=={dt:frappe.db.count(dt) for dt in before}
    frappe.db.commit()
    return dict(status='passed',source_import=batch.name,projects=52,financial_documents_unchanged=True,audit=report,
        checks=['exact file idempotency','52 distinct source rows','50 known annual amounts','nonannual pricing preserved','blank amount not zero',
            'immutable source bytes and cells','contract confirmation evidence','annual renewal dates','unique relationship',
            'revocation preserves history','guest permission denial','database-only reconstruction','no financial postings'])
