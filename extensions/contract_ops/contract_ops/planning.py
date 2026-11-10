"""Plan lifecycle v1. Plans are projections, never receivable or cash postings."""
import hashlib

import frappe
from frappe.utils import add_days, getdate, cint, nowdate

from contract_ops.planning_rules import money, allocate_fifo, business_due_date

FIXED = '固定日期'
INVOICE_DAYS = '开票后自然日'
RECEIPT_DAYS = '确认收票后工作日'
RECEIPT_CALENDAR_DAYS = '确认收票后自然日'
INVOICE_WORK_DAYS = '开票后工作日'


def changed(doc, before, field):
    kind = doc.meta.get_field(field).fieldtype
    left, right = before.get(field), doc.get(field)
    if kind in ('Currency', 'Float', 'Percent'):
        return money(left) != money(right)
    if kind in ('Int', 'Check'):
        return cint(left) != cint(right)
    if kind == 'Date':
        return (getdate(left) if left else None) != (getdate(right) if right else None)
    return (left or '') != (right or '')


def lock_contract(name):
    rows = frappe.db.sql('SELECT name FROM `tabContract` WHERE name=%s FOR UPDATE', name)
    if not rows:
        frappe.throw('销售合同不存在')
    contract = frappe.get_doc('Contract', name)
    from contract_ops.native_contract import require_sales_contract
    require_sales_contract(contract)
    if contract.docstatus != 1:
        frappe.throw('计划必须关联已提交的销售合同')
    return contract


def validate_billing_plan(doc):
    contract = lock_contract(doc.sales_contract)
    contract.check_permission('read')
    if doc.get('source_collection_plan'):
        from contract_ops.prepayment import validate_generated_plan
        validate_generated_plan(doc,contract)
        return
    if contract.get('billing_flow') == '先款后票':
        frappe.throw('先款后票合同请先建立收款计划，实际到账后自动生成开票计划')
    doc.currency = contract.currency
    if money(doc.planned_amount) <= 0:
        frappe.throw('计划金额必须大于零')
    if getdate(doc.period_start) > getdate(doc.period_end):
        frappe.throw('计费结束日期不得早于开始日期')
    historical_execution = "历史执行补充计划" in (doc.get("notes") or "")
    if not historical_execution and ((contract.start_date and getdate(doc.period_start) < getdate(contract.start_date)) or (contract.end_date and getdate(doc.period_end) > getdate(contract.end_date))):
        frappe.throw('计费期间必须在合同有效期内')
    if doc.due_rule not in (FIXED, INVOICE_DAYS, RECEIPT_DAYS,RECEIPT_CALENDAR_DAYS,INVOICE_WORK_DAYS) or not 0 <= cint(doc.due_days) <= 3650:
        frappe.throw('收款到期规则或间隔天数无效')
    if doc.due_rule == FIXED and not doc.fixed_due_date:
        frappe.throw('请填写固定收款日期')
    if doc.due_rule.endswith('工作日') and not doc.holiday_list:
        frappe.throw('收票后工作日规则必须选择已配置的工作日历')
    if not doc.is_new():
        before = doc.get_doc_before_save()
        if before and frappe.db.exists('Contract Receipt Plan', {'billing_plan': doc.name}):
            protected = ('sales_contract','period_start','period_end','planned_amount','due_rule','due_days','fixed_due_date','holiday_list')
            if any(changed(doc, before, f) for f in protected):
                frappe.throw('已生成收款计划的期次不可改写金额、期间和规则；请保留原记录并新建后续计划')
    other = frappe.db.sql('SELECT COALESCE(SUM(planned_amount),0) FROM `tabContract Billing Plan` WHERE sales_contract=%s AND name!=%s AND disabled=0', (doc.sales_contract, doc.name or ''))[0][0]
    total_unknown = "合同总额未能从原件确认" in (contract.get("notes") or "")
    if not total_unknown and not doc.disabled and money(other) + money(doc.planned_amount) > money(contract.contract_amount):
        frappe.throw('有效开票计划合计不能超过合同金额')
    # These are managed projections, not editable facts.
    doc.billed_amount, doc.received_amount = plan_totals(doc.name) if not doc.is_new() else (0, 0)
    doc.status = plan_status(doc)


def plan_totals(name):
    source=frappe.db.get_value('Contract Billing Plan',name,['source_collection_plan','planned_amount','source_cancelled'],as_dict=True)
    if source and source.source_collection_plan:
        billed=frappe.db.sql('''SELECT COALESCE(SUM(a.allocated_amount),0) FROM `tabVAT Plan Allocation` a
            JOIN `tabVAT Invoice Record` v ON v.name=a.parent WHERE v.docstatus=1 AND a.billing_plan=%s''',name)[0][0]
        return money(billed),money(0 if source.source_cancelled else source.planned_amount)
    rows = frappe.get_all('Contract Receipt Plan', filters={'billing_plan': name, 'cancelled': 0}, fields=['expected_amount','received_amount'])
    return (sum((money(r.expected_amount) for r in rows), money(0)), sum((money(r.received_amount) for r in rows), money(0)))


def plan_status(doc):
    if doc.disabled:
        return '已停用'
    if doc.get('source_collection_plan'):
        return '已开齐' if money(doc.billed_amount)>=money(doc.planned_amount) else ('部分开票' if money(doc.billed_amount) else '待开票')
    if money(doc.received_amount) >= money(doc.planned_amount):
        return '已收齐'
    if money(doc.billed_amount) >= money(doc.planned_amount):
        return '已开齐待收款'
    return '部分开票' if money(doc.billed_amount) else '待开票'


def refresh_billing_plan(name):
    doc = frappe.get_doc('Contract Billing Plan', name)
    billed, received = plan_totals(name)
    doc.billed_amount, doc.received_amount = billed, received
    frappe.db.set_value(doc.doctype, name, dict(billed_amount=billed, received_amount=received, status=plan_status(doc)), update_modified=False)


def validate_invoice_link(name, contract):
    invoice = frappe.get_doc('Sales Invoice', name)
    invoice.check_permission('read')
    if invoice.docstatus != 1 or invoice.get('custom_sales_contract') != contract.name:
        frappe.throw('应收发票必须已提交且属于本期销售合同')
    if any(invoice.get(f) != contract.get('party_name' if f == 'customer' else f) for f in ('company','customer','currency')):
        frappe.throw('应收发票主体、客户或币种与合同不一致')
    return invoice


def validate_vat_allocations(doc):
    rows = doc.get('plan_allocations') or []
    if not rows:
        new_submission = doc.is_new() or doc.docstatus == 0 or (doc.docstatus == 1 and frappe.db.get_value(doc.doctype, doc.name, 'docstatus') != 1)
        has_plans = doc.sales_contract and frappe.db.exists('Contract Billing Plan', {'sales_contract': doc.sales_contract, 'disabled': 0})
        if doc.sales_contract and frappe.db.get_value('Contract',doc.sales_contract,'billing_flow')=='先款后票':
            has_plans=True
        if doc.contract_group and not has_plans:
            has_plans = frappe.db.sql('''SELECT p.name FROM `tabContract Billing Plan` p JOIN `tabContract` c ON c.name=p.sales_contract
                WHERE c.contract_group=%s AND p.disabled=0 LIMIT 1''', doc.contract_group)
            if not has_plans:
                has_plans=frappe.db.exists('Contract',{'contract_group':doc.contract_group,'billing_flow':'先款后票','docstatus':1})
        if new_submission and has_plans:
            frappe.throw('本合同已设置开票计划，请分配本次税票到对应期次')
        return
    if len({(r.billing_plan,r.sales_invoice or doc.sales_invoice or '') for r in rows}) != len(rows):
        frappe.throw('同一税票中相同期次及应收的分配请合并；不同应收可分别列行')
    if sum((money(r.allocated_amount) for r in rows), money(0)) != money(doc.gross_amount):
        frappe.throw('期次分配金额合计必须等于税票价税合计')
    # Serialize all submissions for each affected contract in a stable order.
    plans = {r.billing_plan: frappe.get_doc('Contract Billing Plan', r.billing_plan) for r in rows}
    current_amounts={name:sum((money(r.allocated_amount) for r in rows if r.billing_plan==name),money(0)) for name in plans}
    contracts = {n: lock_contract(n) for n in sorted({p.sales_contract for p in plans.values()})}
    invoices = {}
    for row in rows:
        plan = plans[row.billing_plan]
        row.currency=plan.currency
        plan.check_permission('read')
        contract = contracts[plan.sales_contract]
        contract.check_permission('read')
        if plan.disabled or money(row.allocated_amount) <= 0:
            frappe.throw('期次已停用或分配金额无效')
        if any(doc.get(f) != contract.get('party_name' if f == 'customer' else f) for f in ('company','customer','currency')):
            frappe.throw('税票主体、客户和币种必须与所有分配期次一致')
        if doc.sales_contract and doc.sales_contract != contract.name:
            frappe.throw('税票分配期次不属于所选合同')
        if doc.contract_group and contract.get('contract_group') != doc.contract_group:
            frappe.throw('税票分配期次不属于所选合同组')
        prior = frappe.db.sql('''SELECT COALESCE(SUM(a.allocated_amount),0) FROM `tabVAT Plan Allocation` a
            JOIN `tabVAT Invoice Record` v ON v.name=a.parent WHERE v.docstatus=1 AND a.billing_plan=%s AND v.name!=%s''', (plan.name, doc.name or ''))[0][0]
        if money(prior) + current_amounts[plan.name] > money(plan.planned_amount):
            frappe.throw('本期累计开票不能超过计划金额')
        invoice_name = row.sales_invoice or doc.sales_invoice
        if invoice_name:
            invoice = validate_invoice_link(invoice_name, contract)
            invoices.setdefault(invoice.name, [invoice, money(0)])[1] += money(row.allocated_amount)
    for name, (invoice, amount) in sorted(invoices.items()):
        frappe.db.sql('SELECT name FROM `tabSales Invoice` WHERE name=%s FOR UPDATE', name)
        used = frappe.db.sql('SELECT COALESCE(SUM(expected_amount),0) FROM `tabContract Receipt Plan` WHERE sales_invoice=%s AND cancelled=0 AND source_vat!=%s', (name, doc.name or ''))[0][0]
        if frappe.db.get_value('Contract',invoice.custom_sales_contract,'billing_flow')=='先款后票':
            used=frappe.db.sql('''SELECT COALESCE(SUM(a.allocated_amount),0) FROM `tabVAT Plan Allocation` a
                JOIN `tabVAT Invoice Record` v ON v.name=a.parent WHERE v.docstatus=1 AND v.name!=%s
                AND COALESCE(NULLIF(a.sales_invoice,''),v.sales_invoice)=%s''',(doc.name or '',name))[0][0]
        if money(used) + amount > money(invoice.rounded_total or invoice.grand_total):
            frappe.throw('关联应收发票的收款计划合计不能超过应收金额')


def receipt_due(doc):
    if doc.due_rule == FIXED:
        return doc.fixed_due_date
    start=doc.invoice_date if doc.due_rule in (INVOICE_DAYS,INVOICE_WORK_DAYS) else doc.received_on
    if not start:
        return None
    if doc.due_rule in (INVOICE_DAYS,RECEIPT_CALENDAR_DAYS):
        return add_days(start,cint(doc.due_days))
    calendar = frappe.get_doc('Holiday List', doc.holiday_list)
    try:
        return business_due_date(getdate(start), cint(doc.due_days), {getdate(h.holiday_date) for h in calendar.holidays}, getdate(calendar.from_date), getdate(calendar.to_date))
    except ValueError as exc:
        frappe.throw(str(exc))


def receipt_status(doc):
    if doc.cancelled:
        return '已作废'
    if money(doc.received_amount) >= money(doc.expected_amount):
        return '已收齐'
    if not doc.sales_invoice:
        return '待关联应收'
    if money(doc.received_amount):
        return '部分收款'
    return '待确认收票' if doc.due_rule in (RECEIPT_DAYS,RECEIPT_CALENDAR_DAYS) and not doc.received_on else '待收款'


def validate_receipt_plan(doc):
    before = doc.get_doc_before_save()
    if doc.is_new() and not doc.flags.from_vat:
        frappe.throw('收款计划只能由已提交税票自动生成')
    if before:
        protected = ('source_key','source_vat','billing_plan','sales_contract','currency','expected_amount','invoice_date','due_rule','due_days','fixed_due_date','holiday_list','cancelled','received_amount','outstanding_amount','status')
        if any(changed(doc, before, f) for f in protected):
            frappe.throw('来源、金额、规则和核销状态由系统维护，不可手工改写')
        if before.cancelled and (str(before.received_on or '') != str(doc.received_on or '') or before.sales_invoice != doc.sales_invoice):
            frappe.throw('已作废收款计划不能修改')
        if before.sales_invoice != doc.sales_invoice and money(before.received_amount):
            frappe.throw('已有收款的计划不能更换应收发票')
    if doc.received_on and getdate(doc.received_on) < getdate(doc.invoice_date):
        frappe.throw('确认收票日期不能早于实际开票日期')
    if doc.received_on and getdate(doc.received_on) > getdate(nowdate()):
        frappe.throw('实际收票确认日期不能晚于今天')
    source = frappe.get_doc('VAT Invoice Record', doc.source_vat)
    candidates=[r for r in source.plan_allocations if r.billing_plan==doc.billing_plan]
    source_row=next((r for r in candidates if (r.sales_invoice or source.sales_invoice or '')==(doc.sales_invoice or '')),None)
    if not source_row:
        source_row=next((r for r in candidates if not (r.sales_invoice or source.sales_invoice)),None)
    if not source_row:
        frappe.throw('来源税票缺少该计划分配记录')
    original_invoice = source_row.sales_invoice or source.sales_invoice
    if original_invoice and doc.sales_invoice != original_invoice:
        frappe.throw('收款计划不能更换来源税票已指定的应收发票')
    if doc.sales_invoice:
        contract = frappe.get_doc('Contract', doc.sales_contract)
        invoice = validate_invoice_link(doc.sales_invoice, contract)
        frappe.db.sql('SELECT name FROM `tabSales Invoice` WHERE name=%s FOR UPDATE', invoice.name)
        other = frappe.db.sql('SELECT COALESCE(SUM(expected_amount),0) FROM `tabContract Receipt Plan` WHERE sales_invoice=%s AND cancelled=0 AND name!=%s', (invoice.name, doc.name or ''))[0][0]
        if money(other) + money(doc.expected_amount) > money(invoice.rounded_total or invoice.grand_total):
            frappe.throw('收款计划合计超过应收发票金额')
    doc.due_date = receipt_due(doc)
    doc.outstanding_amount = money(doc.expected_amount) - money(doc.received_amount)
    doc.status = receipt_status(doc)


def create_receipt_plans(doc):
    invoices, plans = set(), set()
    for row in doc.get('plan_allocations') or []:
        plan = frappe.get_doc('Contract Billing Plan', row.billing_plan)
        if plan.get('source_collection_plan'):
            refresh_billing_plan(plan.name)
            continue
        suffix=''
        if sum(1 for r in doc.plan_allocations if r.billing_plan==plan.name)>1:
            suffix='|'+(row.sales_invoice or doc.sales_invoice or 'UNLINKED')
        key = 'CRP-' + hashlib.sha256(f'{doc.name}|{plan.name}{suffix}'.encode()).hexdigest()[:24]
        if not frappe.db.exists('Contract Receipt Plan', key):
            receipt = frappe.get_doc(dict(doctype='Contract Receipt Plan', source_key=key, source_vat=doc.name,
                billing_plan=plan.name, sales_contract=plan.sales_contract, sales_invoice=row.sales_invoice or doc.sales_invoice,
                currency=plan.currency, expected_amount=row.allocated_amount, invoice_date=doc.invoice_date,
                due_rule=plan.due_rule, due_days=plan.due_days, fixed_due_date=plan.fixed_due_date, holiday_list=plan.holiday_list,
                received_amount=0))
            receipt.flags.from_vat = True
            receipt.insert(ignore_permissions=True)
        invoice = row.sales_invoice or doc.sales_invoice
        if invoice:
            invoices.add(invoice)
        plans.add(plan.name)
    for invoice in sorted(invoices):
        refresh_invoice_receipts(invoice)
    for plan in plans:
        refresh_billing_plan(plan)


@frappe.whitelist()
def repair_vat_receipt_plans(vat_invoice):
    """Idempotently restore receipt-plan projections for a submitted VAT record.

    Historical migrations may have moved a VAT allocation to a replacement
    cabinet plan after the VAT's original submit hook ran.  This repairs only
    missing projection rows; it never creates or changes tax invoices,
    receivables, Payment Entries, or their allocation amounts.
    """
    vat = frappe.get_doc('VAT Invoice Record', vat_invoice)
    vat.check_permission('write')
    if vat.docstatus != 1:
        frappe.throw('只能修复已提交税票的收款计划')
    if not vat.get('plan_allocations'):
        frappe.throw('税票没有期次分配，无法修复收款计划')
    before = frappe.get_all('Contract Receipt Plan', filters={'source_vat': vat.name, 'cancelled': 0}, pluck='name')
    invoices, plans = set(), set()
    for row in vat.plan_allocations:
        plan = frappe.get_doc('Contract Billing Plan', row.billing_plan)
        suffix = ''
        if sum(1 for candidate in vat.plan_allocations if candidate.billing_plan == plan.name) > 1:
            suffix = '|' + (row.sales_invoice or vat.sales_invoice or 'UNLINKED')
        key = 'CRP-' + hashlib.sha256(f'{vat.name}|{plan.name}{suffix}'.encode()).hexdigest()[:24]
        existing = frappe.get_all('Contract Receipt Plan', filters={'source_vat': vat.name,
            'billing_plan': plan.name, 'sales_invoice': row.sales_invoice or vat.sales_invoice,
            'cancelled': 0}, fields=['name', 'expected_amount'])
        if len(existing) == 1 and money(existing[0].expected_amount) == money(row.allocated_amount):
            key = existing[0].name
        if not frappe.db.exists('Contract Receipt Plan', key):
            # The VAT allocation itself is the immutable source of these values.
            # This historical repair is intentionally limited to the derived
            # projection row, so avoid revalidating an unrelated legacy receipt
            # plan that might already share this Sales Invoice.
            receipt = frappe.get_doc(dict(doctype='Contract Receipt Plan', source_key=key, source_vat=vat.name,
                billing_plan=plan.name, sales_contract=plan.sales_contract,
                sales_invoice=row.sales_invoice or vat.sales_invoice, currency=plan.currency,
                expected_amount=row.allocated_amount, invoice_date=vat.invoice_date,
                due_rule=plan.due_rule, due_days=plan.due_days, fixed_due_date=plan.fixed_due_date,
                holiday_list=plan.holiday_list, received_amount=0))
            receipt.flags.from_vat = True
            receipt.flags.ignore_validate = True
            receipt.insert(ignore_permissions=True)
        # Legacy migration rows may use an older source-key formula.  Keep the
        # canonical projection derived from the current VAT/plan relation and
        # retire only a zero-received duplicate; paid duplicates require manual
        # investigation instead of an automatic choice.
        duplicates = frappe.get_all('Contract Receipt Plan', filters={
            'source_vat': vat.name, 'billing_plan': plan.name,
            'sales_invoice': row.sales_invoice or vat.sales_invoice,
            'cancelled': 0,
        }, fields=['name', 'received_amount'])
        for duplicate in duplicates:
            if duplicate.name == key:
                continue
            if money(duplicate.received_amount):
                frappe.throw('存在已核销的重复历史收款计划，请人工核对：' + duplicate.name)
            frappe.db.set_value('Contract Receipt Plan', duplicate.name,
                                {'cancelled': 1, 'status': '已作废', 'outstanding_amount': 0},
                                update_modified=False)
        invoice = row.sales_invoice or vat.sales_invoice
        if invoice:
            invoices.add(invoice)
        plans.add(plan.name)
    for invoice in sorted(invoices):
        refresh_invoice_receipts(invoice)
    for plan in plans:
        refresh_billing_plan(plan)
    after = frappe.get_all('Contract Receipt Plan', filters={'source_vat': vat.name, 'cancelled': 0}, pluck='name')
    return {'vat_invoice': vat.name, 'created': len(set(after) - set(before)), 'receipt_plans': sorted(after)}


def refresh_invoice_receipts(invoice_name):
    # Shared invoice lock prevents two concurrent payments assigning the same cash twice.
    frappe.db.sql('SELECT name FROM `tabSales Invoice` WHERE name=%s FOR UPDATE', invoice_name)
    rows = frappe.get_all('Contract Receipt Plan', filters={'sales_invoice': invoice_name, 'cancelled': 0},
        fields=['name','expected_amount','billing_plan'], order_by='invoice_date asc, source_vat asc, billing_plan asc, name asc')
    paid = frappe.db.sql('''SELECT COALESCE(SUM(CASE WHEN pe.payment_type='Receive' THEN r.allocated_amount ELSE -r.allocated_amount END),0)
        FROM `tabPayment Entry Reference` r JOIN `tabPayment Entry` pe ON pe.name=r.parent
        WHERE pe.docstatus=1 AND r.reference_doctype='Sales Invoice' AND r.reference_name=%s
        AND pe.payment_type IN ('Receive','Pay')''', invoice_name)[0][0]
    allocated = allocate_fifo(paid, [r.expected_amount for r in rows])
    for row, amount in zip(rows, allocated):
        doc = frappe.get_doc('Contract Receipt Plan', row.name)
        doc.received_amount = amount
        frappe.db.set_value(doc.doctype, doc.name, dict(received_amount=amount,
            outstanding_amount=money(doc.expected_amount)-amount, status=receipt_status(doc)), update_modified=False)
    for name in {r.billing_plan for r in rows}:
        refresh_billing_plan(name)


def payment_changed(doc, method=None):
    from contract_ops.prepayment import payment_changed as update_prepayment
    update_prepayment(doc)
    for name in sorted({r.reference_name for r in doc.references if r.reference_doctype == 'Sales Invoice'}):
        refresh_invoice_receipts(name)


def cancel_receipt_plans(doc):
    rows = frappe.get_all('Contract Receipt Plan', filters={'source_vat': doc.name, 'cancelled': 0}, fields=['name','billing_plan','sales_invoice','received_amount'])
    for invoice in sorted({r.sales_invoice for r in rows if r.sales_invoice}):
        refresh_invoice_receipts(invoice)
    for row in rows:
        if money(frappe.db.get_value('Contract Receipt Plan', row.name, 'received_amount')):
            frappe.throw('税票已有核销收款，请先处理关联收款，不能直接取消')
    for row in rows:
        frappe.db.set_value('Contract Receipt Plan', row.name, dict(cancelled=1, status='已作废', outstanding_amount=0))
    for name in {r.billing_plan for r in rows}:
        refresh_billing_plan(name)


@frappe.whitelist()
def backfill_historical_vat(billing_plan, vat_invoice, sales_invoice, allocated_amount):
    """Controlled history-only link; never creates a second tax, AR, or payment document."""
    plan = frappe.get_doc('Contract Billing Plan', billing_plan)
    contract = lock_contract(plan.sales_contract)
    plan.check_permission('write')
    vat = frappe.get_doc('VAT Invoice Record', vat_invoice)
    vat.check_permission('write')
    if vat.docstatus != 1 or plan.source_collection_plan:
        frappe.throw('只能向普通开票计划回填已提交的历史税票')
    invoice = validate_invoice_link(sales_invoice, contract)
    amount = money(allocated_amount)
    if amount <= 0:
        frappe.throw('回填金额必须大于零')
    if any(row.billing_plan == plan.name for row in vat.plan_allocations):
        frappe.throw('该税票已关联此期次')
    used = sum((money(row.allocated_amount) for row in vat.plan_allocations), money(0))
    if used + amount > money(vat.gross_amount):
        frappe.throw('回填金额超过税票尚未分配金额')
    if money(plan.billed_amount) + amount > money(plan.planned_amount):
        frappe.throw('回填金额超过本期开票计划金额')
    vat.append('plan_allocations', {'billing_plan': plan.name, 'sales_invoice': invoice.name,
                                     'currency': plan.currency, 'allocated_amount': amount})
    vat.save()
    create_receipt_plans(vat)
    refresh_billing_plan(plan.name)
    return {'billing_plan': plan.name, 'vat_invoice': vat.name, 'sales_invoice': invoice.name,
            'allocated_amount': amount}


@frappe.whitelist()
def backfill_historical_vat_bulk(vat_invoice, allocations):
    """Atomically attach one historical tax invoice to all of its plan/invoice shares."""
    rows = frappe.parse_json(allocations) if isinstance(allocations, str) else allocations
    vat = frappe.get_doc('VAT Invoice Record', vat_invoice)
    vat.check_permission('write')
    if vat.docstatus != 1 or vat.get('plan_allocations'):
        frappe.throw('税票必须已提交且尚未完成计划回填')
    if not rows or sum((money(x.get('allocated_amount')) for x in rows), money(0)) != money(vat.gross_amount):
        frappe.throw('历史税票回填分配合计必须等于税票金额')
    for row in rows:
        plan = frappe.get_doc('Contract Billing Plan', row.get('billing_plan'))
        contract = lock_contract(plan.sales_contract)
        invoice = validate_invoice_link(row.get('sales_invoice'), contract)
        amount = money(row.get('allocated_amount'))
        if amount <= 0 or money(plan.billed_amount) + amount > money(plan.planned_amount):
            frappe.throw('回填金额无效或超过期次计划金额')
        vat.append('plan_allocations', {'billing_plan': plan.name, 'sales_invoice': invoice.name,
                                         'currency': plan.currency, 'allocated_amount': amount})
    vat.save()
    create_receipt_plans(vat)
    return {'vat_invoice': vat.name, 'allocations': len(rows)}


@frappe.whitelist()
def migrate_historical_plan_links(rows):
    """Move existing historical VAT/receipt-plan links to replacement cabinet plans.

    This is intentionally a narrow data-migration operation: it neither creates
    accounting documents nor changes a VAT, Sales Invoice, Payment Entry, or
    allocated amount.  It is used when a legacy source-document contract is
    split into one Contract per physical cabinet.
    """
    rows = frappe.parse_json(rows) if isinstance(rows, str) else rows
    if not isinstance(rows, list) or not rows:
        frappe.throw('请提供至少一条历史期次迁移记录')
    changed_plans = set()
    for row in rows:
        allocation_name = row.get('vat_plan_allocation')
        new_plan_name = row.get('billing_plan')
        receipt_plan_name = row.get('receipt_plan')
        if not allocation_name or not new_plan_name:
            frappe.throw('历史期次迁移缺少税票分配或目标开票计划')
        allocation = frappe.get_doc('VAT Plan Allocation', allocation_name)
        vat = frappe.get_doc('VAT Invoice Record', allocation.parent)
        if vat.docstatus != 1:
            frappe.throw('只能迁移已提交税票的历史期次')
        new_plan = frappe.get_doc('Contract Billing Plan', new_plan_name)
        contract = lock_contract(new_plan.sales_contract)
        invoice_name = allocation.get('sales_invoice') or vat.get('sales_invoice')
        invoice = validate_invoice_link(invoice_name, contract) if invoice_name else None
        if invoice and money(allocation.allocated_amount) > money(invoice.rounded_total or invoice.grand_total):
            frappe.throw('税票分配金额超过关联应收金额')
        old_plan_name = allocation.billing_plan
        if old_plan_name != new_plan.name:
            frappe.db.set_value('VAT Plan Allocation', allocation.name, 'billing_plan', new_plan.name,
                                update_modified=False)
            changed_plans.update((old_plan_name, new_plan.name))
        if receipt_plan_name:
            receipt = frappe.get_doc('Contract Receipt Plan', receipt_plan_name)
            if receipt.source_vat != vat.name or receipt.sales_invoice != invoice_name:
                frappe.throw('收款计划与税票分配/应收发票不一致')
            frappe.db.set_value('Contract Receipt Plan', receipt.name,
                                {'billing_plan': new_plan.name, 'sales_contract': contract.name},
                                update_modified=False)
    for plan_name in changed_plans:
        if plan_name:
            refresh_billing_plan(plan_name)
    return {'migrated': len(rows), 'plans_refreshed': len(changed_plans)}


@frappe.whitelist()
def get_contract_planning(sales_contract):
    contract = frappe.get_doc('Contract', sales_contract)
    contract.check_permission('read')
    from contract_ops.native_contract import require_sales_contract
    require_sales_contract(contract)
    def visible(doctype, fields):
        return frappe.get_list(doctype, filters={'sales_contract': contract.name}, fields=fields, limit_page_length=0, order_by='creation asc')
    return dict(currency=contract.currency,
        billing_flow=contract.get('billing_flow') or '先票后款',
        collection_plans=visible('Contract Collection Plan',['name','plan_title','receipt_due_date','planned_amount','received_amount','outstanding_amount','invoice_trigger','invoice_days','day_type','status']),
        billing_plans=visible('Contract Billing Plan', ['name','plan_title','period_start','period_end','invoice_due_date','planned_amount','billed_amount','received_amount','status','source_collection_plan']),
        receipt_plans=visible('Contract Receipt Plan', ['name','source_vat','billing_plan','sales_invoice','invoice_date','received_on','due_date','expected_amount','received_amount','outstanding_amount','status','cancelled']))
