"""Payment-first workflow v1: evidence-backed advances release invoice obligations."""
import hashlib
import json

import frappe
from frappe.utils import getdate, nowdate, cint, add_days

from contract_ops.planning_rules import money, business_due_date

PAY_FIRST = '先款后票'
INVOICE_FIRST = '先票后款'


def validate_flow_change(doc):
    flow = doc.get('billing_flow') or INVOICE_FIRST
    if flow not in (PAY_FIRST, INVOICE_FIRST):
        frappe.throw('票款顺序无效')
    before = doc.get_doc_before_save()
    if not before or (before.get('billing_flow') or INVOICE_FIRST) == flow:
        return
    for dt, field in [('Contract Billing Plan','sales_contract'),('Contract Collection Plan','sales_contract')]:
        if frappe.db.exists(dt, {field:doc.name}):
            frappe.throw('已有计划的合同不能切换票款顺序；请保留历史并建立后续协议')


def validate_collection_plan(doc):
    from contract_ops.planning import lock_contract, changed
    contract = lock_contract(doc.sales_contract)
    contract.check_permission('read')
    if contract.get('billing_flow') != PAY_FIRST:
        frappe.throw('先款后票收款计划必须关联先款后票合同')
    doc.currency = contract.currency
    if money(doc.planned_amount) <= 0 or not 0 <= cint(doc.invoice_days) <= 3650:
        frappe.throw('计划金额必须大于零，开票间隔为0至3650天')
    if doc.invoice_trigger not in ('每笔到账','本期收齐') or doc.day_type not in ('自然日','工作日'):
        frappe.throw('开票起算方式或天数口径无效')
    if doc.day_type == '工作日' and not doc.holiday_list:
        frappe.throw('工作日规则必须指定已维护的工作日历')
    if getdate(doc.period_start) > getdate(doc.period_end) or (contract.start_date and getdate(doc.period_start)<getdate(contract.start_date)) or (contract.end_date and getdate(doc.period_end)>getdate(contract.end_date)):
        frappe.throw('计费期间必须有序且在合同有效期内')
    before = doc.get_doc_before_save()
    if before and frappe.db.exists('Contract Collection Allocation', {'collection_plan':doc.name}):
        fields=('sales_contract','planned_amount','period_start','period_end','invoice_trigger','invoice_days','day_type','holiday_list')
        if any(changed(doc,before,f) for f in fields):
            frappe.throw('已有到账分配的期次不能覆盖金额、期间或开票规则')
    other=frappe.db.sql('''SELECT COALESCE(SUM(planned_amount),0) FROM `tabContract Collection Plan`
        WHERE sales_contract=%s AND name!=%s AND disabled=0''',(doc.sales_contract,doc.name or ''))[0][0]
    if not doc.disabled and money(other)+money(doc.planned_amount)>money(contract.contract_amount):
        frappe.throw('先款后票收款计划合计不能超过合同金额')
    facts=active_funding(doc.name) if not doc.is_new() else []
    doc.received_amount=sum((money(r.funded_amount) for r in facts),money(0))
    doc.outstanding_amount=money(doc.planned_amount)-doc.received_amount
    doc.status=collection_status(doc)


def collection_status(doc):
    if doc.disabled:return '已停用'
    if money(doc.received_amount)>=money(doc.planned_amount):return '已收齐'
    return '部分收款' if money(doc.received_amount) else '待收款'


def active_funding(name):
    return frappe.db.sql('''SELECT pe.name AS source_payment,a.name AS allocation_row,
            pe.custom_actual_receipt_date AS actual_receipt_date,a.allocated_amount AS funded_amount
        FROM `tabContract Collection Allocation` a JOIN `tabPayment Entry` pe ON pe.name=a.parent
        WHERE a.collection_plan=%s AND pe.docstatus=1 AND pe.payment_type='Receive'
        ORDER BY pe.custom_actual_receipt_date,pe.name,a.idx''',name,as_dict=True)


def validate_payment_allocations(doc, method=None):
    rows=doc.get('custom_collection_allocations') or []
    if not rows:return
    if doc.payment_type!='Receive' or doc.party_type!='Customer':
        frappe.throw('先款后票分配只能用于客户收款')
    if not doc.get('custom_actual_receipt_date'):
        frappe.throw('请填写银行实际到账日期，不以记账日期代替')
    if getdate(doc.custom_actual_receipt_date)>getdate(nowdate()):
        frappe.throw('实际到账日期不能晚于今天')
    if len({r.collection_plan for r in rows})!=len(rows):
        frappe.throw('同一收款单的同一期次分配请合并成一行')
    total=sum((money(r.allocated_amount) for r in rows),money(0))
    if total<=0 or total>money(doc.paid_amount):
        frappe.throw('先款后票分配合计必须为正且不超过本笔收款')
    # A plan allocation describes this cash; it is not a second ledger allocation.
    # It may overlap invoice references for the SAME contract, or use the remaining advance pool.
    prior=frappe.db.get_value('Payment Entry',doc.name,'docstatus') if not doc.is_new() else None
    plans={r.collection_plan:frappe.get_doc('Contract Collection Plan',r.collection_plan) for r in rows}
    from contract_ops.planning import lock_contract
    contracts={n:lock_contract(n) for n in sorted({p.sales_contract for p in plans.values()})}
    if prior!=1:
        invoice_budgets={}
        for ref in doc.references:
            if ref.reference_doctype=='Sales Invoice' and money(ref.allocated_amount)>0:
                owner=frappe.db.get_value('Sales Invoice',ref.reference_name,'custom_sales_contract')
                if owner:
                    invoice_budgets[owner]=invoice_budgets.get(owner,money(0))+money(ref.allocated_amount)
        period_totals={}
        for row in rows:
            owner=plans[row.collection_plan].sales_contract
            period_totals[owner]=period_totals.get(owner,money(0))+money(row.allocated_amount)
        advance=max(money(doc.paid_amount)-sum((money(r.allocated_amount) for r in doc.references),money(0)),money(0))
        required=sum((max(amount-invoice_budgets.get(owner,money(0)),money(0)) for owner,amount in period_totals.items()),money(0))
        if required>advance:
            frappe.throw('期次分配必须对应本合同应收核销或本笔未分配预收款，不能占用其他合同核销款')
    for row in rows:
        plan=plans[row.collection_plan];plan.check_permission('read')
        row.currency=plan.currency
        contract=contracts[plan.sales_contract];contract.check_permission('read')
        if contract.get('billing_flow')!=PAY_FIRST or plan.disabled or money(row.allocated_amount)<=0:
            frappe.throw('收款期次已停用、票款顺序错误或分配金额无效')
        if doc.company!=contract.company or doc.party!=contract.customer or doc.paid_from_account_currency!=contract.currency:
            frappe.throw('收款公司、客户及往来币种必须与合同一致')
        used=sum((money(r.funded_amount) for r in active_funding(plan.name) if r.source_payment!=doc.name),money(0))
        if used+money(row.allocated_amount)>money(plan.planned_amount):
            frappe.throw('累计到账分配不能超过该期收款计划金额，多余款项请另行分配')


def funding_signature(facts):
    return [(r.source_payment,r.allocation_row,str(getdate(r.actual_receipt_date)),str(money(r.funded_amount))) for r in facts]


def invoice_date_for(plan, actual):
    if plan.day_type=='自然日':return getdate(add_days(actual,cint(plan.invoice_days)))
    calendar=frappe.get_doc('Holiday List',plan.holiday_list)
    try:
        return business_due_date(getdate(actual),cint(plan.invoice_days),{getdate(h.holiday_date) for h in calendar.holidays},getdate(calendar.from_date),getdate(calendar.to_date))
    except ValueError as exc:frappe.throw(str(exc))


def validate_generated_plan(doc,contract):
    from contract_ops.planning import changed, plan_totals, plan_status
    if contract.get('billing_flow')!=PAY_FIRST:
        frappe.throw('到账生成的开票计划必须属于先款后票合同')
    if doc.is_new() and not doc.flags.from_collection:
        frappe.throw('先款后票开票计划只能由实际到账自动生成')
    source=frappe.get_doc('Contract Collection Plan',doc.source_collection_plan)
    if source.sales_contract!=contract.name:
        frappe.throw('到账来源计划不属于本合同')
    before=doc.get_doc_before_save()
    if before:
        fields=('sales_contract','period_start','period_end','invoice_due_date','planned_amount','source_collection_plan','source_payment','source_key','source_cancelled','disabled','due_rule','due_days','fixed_due_date','holiday_list')
        if any(changed(doc,before,f) for f in fields) or funding_signature(before.funding_evidence)!=funding_signature(doc.funding_evidence):
            frappe.throw('实际到账生成的开票义务及资金依据不能手工改写')
    if not doc.funding_evidence or sum((money(r.funded_amount) for r in doc.funding_evidence),money(0))!=money(doc.planned_amount):
        frappe.throw('到账依据快照与开票金额不一致')
    doc.currency=contract.currency
    doc.billed_amount,doc.received_amount=plan_totals(doc.name) if not doc.is_new() else (0,money(doc.planned_amount))
    doc.status=plan_status(doc)


def has_invoicing(plan_name):
    return frappe.db.sql('''SELECT v.name FROM `tabVAT Plan Allocation` a JOIN `tabVAT Invoice Record` v ON v.name=a.parent
        WHERE a.billing_plan=%s AND v.docstatus=1 LIMIT 1''',plan_name)


def sync_collection(name):
    from contract_ops.planning import lock_contract
    plan=frappe.get_doc('Contract Collection Plan',name)
    lock_contract(plan.sales_contract)
    facts=active_funding(name)
    total=sum((money(r.funded_amount) for r in facts),money(0))
    plan.received_amount=total
    frappe.db.set_value(plan.doctype,plan.name,dict(received_amount=total,
        outstanding_amount=money(plan.planned_amount)-total,status=collection_status(plan)),update_modified=False)
    batches=[[f] for f in facts] if plan.invoice_trigger=='每笔到账' else ([facts] if total==money(plan.planned_amount) else [])
    keys=set()
    for batch in batches:
        key='FUNDED-'+hashlib.sha256((name+'|'+json.dumps(funding_signature(batch))).encode()).hexdigest()[:32]
        keys.add(key)
        if frappe.db.exists('Contract Billing Plan',{'source_key':key}):continue
        amount=sum((money(r.funded_amount) for r in batch),money(0))
        doc=frappe.get_doc(dict(doctype='Contract Billing Plan',sales_contract=plan.sales_contract,
            plan_title=plan.plan_title+' · 到账后开票',period_start=plan.period_start,period_end=plan.period_end,
            planned_amount=amount,invoice_due_date=invoice_date_for(plan,max(getdate(r.actual_receipt_date) for r in batch)),
            due_rule='固定日期',fixed_due_date=plan.receipt_due_date,source_collection_plan=name,
            source_payment=batch[0].source_payment if len(batch)==1 else None,source_key=key,
            funding_evidence=[dict(source_payment=r.source_payment,allocation_row=r.allocation_row,
                actual_receipt_date=r.actual_receipt_date,funded_amount=r.funded_amount,currency=plan.currency) for r in batch]))
        doc.flags.from_collection=True;doc.insert(ignore_permissions=True)
    for old in frappe.get_all('Contract Billing Plan',filters={'source_collection_plan':name,'source_cancelled':0},fields=['name','source_key']):
        if old.source_key not in keys:
            if has_invoicing(old.name):frappe.throw('来源收款变化会撤销已开票的资金依据，请先处理关联税票')
            frappe.db.set_value('Contract Billing Plan',old.name,dict(source_cancelled=1,disabled=1,received_amount=0,status='来源收款已取消'))


def payment_changed(doc):
    for name in sorted({r.collection_plan for r in doc.get('custom_collection_allocations') or []}):
        sync_collection(name)


def before_payment_cancel(doc,method=None):
    from contract_ops.planning import lock_contract
    names=sorted({r.collection_plan for r in doc.get('custom_collection_allocations') or []})
    contracts=sorted({frappe.db.get_value('Contract Collection Plan',n,'sales_contract') for n in names})
    for name in contracts:lock_contract(name)
    generated=frappe.db.sql('''SELECT DISTINCT e.parent FROM `tabContract Funding Evidence` e
        JOIN `tabContract Billing Plan` b ON b.name=e.parent WHERE e.source_payment=%s AND b.source_cancelled=0''',doc.name)
    for (name,) in generated:
        if has_invoicing(name):frappe.throw('该收款已触发实际开票，请先取消对应税票，不能直接取消收款')
    doc.ignore_linked_doctypes=tuple(set(getattr(doc,'ignore_linked_doctypes',[]) or [])|{'Contract Billing Plan','Contract Funding Evidence','VAT Payment Allocation','VAT Invoice Record'})
