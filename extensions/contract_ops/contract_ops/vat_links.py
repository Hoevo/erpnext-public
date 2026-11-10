"""One tax invoice, many agreement/period/payment shares; no new accounting postings."""
from collections import defaultdict
import frappe
from frappe.utils import getdate, nowdate
from contract_ops.planning_rules import money


def invoice_shares(vat):
    shares=defaultdict(lambda:money(0))
    if vat.get('invoice_allocations'):
        for row in vat.invoice_allocations:shares[row.sales_invoice]+=money(row.allocated_amount)
    elif vat.get('plan_allocations'):
        for row in vat.plan_allocations:
            name=row.sales_invoice or vat.sales_invoice
            if name:shares[name]+=money(row.allocated_amount)
    elif vat.sales_invoice:
        shares[vat.sales_invoice]=money(vat.gross_amount)
    return dict(shares)


def group_vat_rows(group, contract_names):
    """Count only this group's share of a shared tax invoice."""
    candidates=frappe.db.sql('''SELECT DISTINCT v.name FROM `tabVAT Invoice Record` v
        LEFT JOIN `tabVAT Plan Allocation` a ON a.parent=v.name
        LEFT JOIN `tabContract Billing Plan` p ON p.name=a.billing_plan
        LEFT JOIN `tabVAT Invoice Allocation` ia ON ia.parent=v.name
        LEFT JOIN `tabSales Invoice` si ON si.name=COALESCE(ia.sales_invoice,v.sales_invoice)
        WHERE v.docstatus=1 AND (v.contract_group=%(group)s OR v.sales_contract IN %(contracts)s
            OR p.sales_contract IN %(contracts)s OR si.custom_sales_contract IN %(contracts)s)
        ORDER BY v.name''',dict(group=group,contracts=tuple(contract_names)),as_dict=True)
    result=[]
    for candidate in candidates:
        vat=frappe.get_doc('VAT Invoice Record',candidate.name);vat.check_permission('read')
        shares=defaultdict(lambda:money(0))
        if vat.get('plan_allocations'):
            for row in vat.plan_allocations:
                owner=frappe.db.get_value('Contract Billing Plan',row.billing_plan,'sales_contract')
                shares[owner]+=money(row.allocated_amount)
        elif invoice_shares(vat):
            for name,amount in invoice_shares(vat).items():
                owner=frappe.db.get_value('Sales Invoice',name,'custom_sales_contract')
                shares[owner]+=amount
        elif vat.sales_contract:
            shares[vat.sales_contract]=money(vat.gross_amount)
        amount=sum((value for owner,value in shares.items() if owner in contract_names),money(0))
        if not shares and vat.contract_group==group:amount=money(vat.gross_amount)
        if amount:
            result.append(frappe._dict(name=vat.name,invoice_number=vat.invoice_number,sales_contract=vat.sales_contract,
                gross_amount=float(amount),invoice_gross_amount=vat.gross_amount,invoice_date=vat.invoice_date))
    return sorted(result,key=lambda r:(str(r.invoice_date),r.name))


def validate_invoice_shares(vat):
    rows=vat.get('invoice_allocations') or []
    if not rows:return
    if len({r.sales_invoice for r in rows})!=len(rows) or any(money(r.allocated_amount)<=0 for r in rows):
        frappe.throw('应收分配不能重复或填写非正金额')
    if sum((money(r.allocated_amount) for r in rows),money(0))!=money(vat.gross_amount):
        frappe.throw('税票对应应收分配合计必须等于价税合计')
    invoice_contracts=defaultdict(lambda:money(0))
    # Lock agreements before receivables, matching the plan submission lock order.
    owners={frappe.db.get_value('Sales Invoice',r.sales_invoice,'custom_sales_contract') for r in rows}
    from contract_ops.planning import lock_contract
    for owner in sorted(n for n in owners if n):lock_contract(owner)
    for name in sorted(r.sales_invoice for r in rows):
        frappe.db.sql('SELECT name FROM `tabSales Invoice` WHERE name=%s FOR UPDATE',name)
    for row in rows:
        invoice=frappe.get_doc('Sales Invoice',row.sales_invoice);invoice.check_permission('read')
        if invoice.docstatus!=1 or any(invoice.get(f)!=vat.get(f) for f in ('company','customer','currency')):
            frappe.throw('合并应收必须已提交，且开票公司、客户和币种一致')
        contract_name=invoice.get('custom_sales_contract')
        if not contract_name:frappe.throw('应收发票未关联销售合同，不能确认合并归属')
        contract=frappe.get_doc('Sales Contract',contract_name);contract.check_permission('read')
        new_submission=vat.is_new() or vat.docstatus==0 or (vat.docstatus==1 and frappe.db.get_value(vat.doctype,vat.name,'docstatus')!=1)
        if new_submission and not vat.get('plan_allocations') and (contract.get('billing_flow')=='先款后票' or frappe.db.exists('Contract Billing Plan',{'sales_contract':contract.name,'disabled':0})):
            frappe.throw('对应合同已有票款计划，请填写期次分配，不能只关联应收绕过计划及付款校验')
        if vat.sales_contract and vat.sales_contract!=contract_name:
            frappe.throw('合并应收包含其他合同，请清空单一合同快捷字段并在明细中分配')
        if vat.contract_group and contract.get('contract_group')!=vat.contract_group:
            frappe.throw('合并应收不属于所选合同组')
        if money(row.allocated_amount)>money(invoice.rounded_total or invoice.grand_total):
            frappe.throw('税票分配金额超过对应应收金额')
        prior=frappe.db.sql('''SELECT DISTINCT v.name FROM `tabVAT Invoice Record` v
            LEFT JOIN `tabVAT Invoice Allocation` a ON a.parent=v.name
            LEFT JOIN `tabVAT Plan Allocation` p ON p.parent=v.name
            WHERE v.docstatus=1 AND v.name!=%s AND (v.sales_invoice=%s OR a.sales_invoice=%s OR p.sales_invoice=%s)''',
            (vat.name or '',invoice.name,invoice.name,invoice.name),as_dict=True)
        used=sum((invoice_shares(frappe.get_doc('VAT Invoice Record',r.name)).get(invoice.name,money(0)) for r in prior),money(0))
        if used+money(row.allocated_amount)>money(invoice.rounded_total or invoice.grand_total):
            frappe.throw('同一应收累计税票分配超过应收金额，请核对已有税票')
        row.currency=vat.currency
        invoice_contracts[contract_name]+=money(row.allocated_amount)
    if vat.get('plan_allocations'):
        plan_contracts=defaultdict(lambda:money(0))
        plan_invoices=defaultdict(lambda:money(0))
        for row in vat.plan_allocations:
            owner=frappe.db.get_value('Contract Billing Plan',row.billing_plan,'sales_contract')
            plan_contracts[owner]+=money(row.allocated_amount)
            if row.sales_invoice and money(row.allocated_amount)>invoice_shares(vat).get(row.sales_invoice,money(0)):
                frappe.throw('期次应收关联与本票应收分配不一致')
            effective=row.sales_invoice or vat.sales_invoice
            if effective:plan_invoices[effective]+=money(row.allocated_amount)
        if dict(plan_contracts)!=dict(invoice_contracts):
            frappe.throw('开票期次与应收分配的逐合同金额必须一致')
        if any(amount>invoice_shares(vat).get(name,money(0)) for name,amount in plan_invoices.items()):
            frappe.throw('开票期次对应应收的合计超过本票应收分配')


def funded_payments(plan, exclude_vat=None, stop_before=None):
    """Remaining funding in evidenced order. Reserve historical bills without rewriting them."""
    budgets={}
    for row in plan.funding_evidence:
        key=row.source_payment
        if key not in budgets:budgets[key]=dict(amount=money(0),date=row.actual_receipt_date)
        budgets[key]['amount']+=money(row.funded_amount)
    history=frappe.db.sql('''SELECT v.name,SUM(a.allocated_amount) AS allocated_amount FROM `tabVAT Plan Allocation` a
        JOIN `tabVAT Invoice Record` v ON v.name=a.parent WHERE v.docstatus=1 AND a.billing_plan=%s AND v.name!=%s
        GROUP BY v.name,v.creation ORDER BY v.creation,v.name''',(plan.name,exclude_vat or ''),as_dict=True)
    for old in history:
        if old.name==stop_before:break
        explicit=frappe.get_all('VAT Payment Allocation',filters={'parent':old.name,'billing_plan':plan.name},fields=['payment_entry','allocated_amount'])
        if explicit:
            for row in explicit:
                if row.payment_entry not in budgets:frappe.throw('历史付款分配来源与期次资金快照冲突')
                budgets[row.payment_entry]['amount']-=money(row.allocated_amount)
        else:
            left=money(old.allocated_amount)
            for budget in budgets.values():
                used=min(max(budget['amount'],money(0)),left);budget['amount']-=used;left-=used
            if left>0:frappe.throw('历史开票超过期次资金依据，请先核对原记录')
    if any(v['amount']<0 for v in budgets.values()):frappe.throw('历史付款分配重复占用期次资金')
    return budgets


def build_payment_sources(vat):
    rows=[]
    cached_budgets={}
    for share in vat.get('plan_allocations') or []:
        plan=frappe.get_doc('Contract Billing Plan',share.billing_plan)
        if not plan.get('source_collection_plan'):continue
        left=money(share.allocated_amount)
        if plan.name not in cached_budgets:cached_budgets[plan.name]=funded_payments(plan,vat.name)
        for payment,budget in cached_budgets[plan.name].items():
            amount=min(max(budget['amount'],money(0)),left)
            if amount<=0:continue
            frappe.db.sql('SELECT name FROM `tabPayment Entry` WHERE name=%s FOR UPDATE',payment)
            pe=frappe.get_doc('Payment Entry',payment);pe.check_permission('read')
            if pe.docstatus!=1 or pe.payment_type!='Receive' or pe.party_type!='Customer' or pe.party!=vat.customer or pe.company!=vat.company or pe.paid_from_account_currency!=vat.currency:
                frappe.throw('来源付款必须为同主体、同客户、同币种的有效客户收款')
            actual=pe.get('custom_actual_receipt_date')
            if not actual or getdate(actual)!=getdate(budget['date']):frappe.throw('付款到账日期与原资金依据不一致')
            if getdate(vat.invoice_date)<getdate(actual):frappe.throw('先款后票的开票日期不能早于对应实际到账日')
            rows.append(dict(sales_contract=plan.sales_contract,billing_plan=plan.name,payment_entry=payment,
                sales_invoice=share.sales_invoice or vat.sales_invoice,
                allocated_amount=amount,actual_receipt_date=actual,currency=vat.currency))
            left-=amount
            budget['amount']-=amount
        if left>0:frappe.throw('本期开票金额超过剩余可用付款来源')
    return rows


def sync_payment_sources(vat):
    # Existing submitted history is read through relationships, never silently rewritten.
    if vat.docstatus==2 or (vat.docstatus==1 and frappe.db.get_value(vat.doctype,vat.name,'docstatus')==1):return
    expected=build_payment_sources(vat)
    vat.set('payment_allocations',[])
    for row in expected:vat.append('payment_allocations',row)


@frappe.whitelist()
def get_merge_candidates(company,customer,currency='CNY'):
    contracts=frappe.get_list('Sales Contract',filters={'docstatus':1,'company':company,'customer':customer,'currency':currency},
        fields=['name','external_number','contract_title','contract_group'],limit_page_length=0)
    owners={r.name:r for r in contracts}
    if not owners:return []
    rows=frappe.get_list('Contract Billing Plan',filters={'sales_contract':['in',list(owners)],'disabled':0,'source_cancelled':0,'currency':currency},
        fields=['name','sales_contract','plan_title','planned_amount','invoice_due_date','source_collection_plan'],limit_page_length=1000,order_by='invoice_due_date asc,name asc')
    if not rows:return []
    used_rows=frappe.db.sql('''SELECT a.billing_plan,SUM(a.allocated_amount) AS amount FROM `tabVAT Plan Allocation` a
        JOIN `tabVAT Invoice Record` v ON v.name=a.parent WHERE v.docstatus=1 AND a.billing_plan IN %(plans)s GROUP BY a.billing_plan''',
        {'plans':tuple(r.name for r in rows)},as_dict=True)
    used={r.billing_plan:money(r.amount) for r in used_rows}
    result=[]
    for row in rows:
        contract=owners[row.sales_contract]
        available=money(row.planned_amount)-used.get(row.name,money(0))
        if available<=0:continue
        result.append(dict(billing_plan=row.name,plan_title=row.plan_title,sales_contract=contract.name,
            external_number=contract.external_number,contract_title=contract.contract_title,contract_group=contract.contract_group,
            available_amount=available,allocated_amount=available,invoice_due_date=row.invoice_due_date,include=0))
    return result


@frappe.whitelist()
def prepare_merged_vat(company,customer,currency,allocations,invoice_date=None):
    rows=frappe.parse_json(allocations) if isinstance(allocations,str) else allocations
    if not isinstance(rows,list) or not rows:frappe.throw('请选择至少一个开票期次')
    if len(rows)>200:frappe.throw('一次最多合并200个期次')
    contracts={}
    for row in rows:
        plan=frappe.get_doc('Contract Billing Plan',row.get('billing_plan'));plan.check_permission('read')
        contract=frappe.get_doc('Sales Contract',plan.sales_contract);contract.check_permission('read');contracts[contract.name]=contract
    parents=list(contracts.values());groups={c.contract_group for c in parents}
    vat=frappe.get_doc(dict(doctype='VAT Invoice Record',company=company,customer=customer,currency=currency,
        invoice_date=invoice_date or nowdate(),gross_amount=sum((money(r.get('allocated_amount')) for r in rows),money(0)),
        sales_contract=parents[0].name if len(parents)==1 else None,
        contract_group=next(iter(groups)) if len(groups)==1 and next(iter(groups)) else None,
        plan_allocations=rows))
    from contract_ops.planning import validate_vat_allocations
    validate_vat_allocations(vat)
    return dict(company=company,customer=customer,currency=currency,invoice_date=vat.invoice_date,gross_amount=vat.gross_amount,
        sales_contract=vat.sales_contract,contract_group=vat.contract_group,
        plan_allocations=[{f:r.get(f) for f in ('billing_plan','allocated_amount','sales_invoice','currency')} for r in vat.plan_allocations],
        payment_allocations=build_payment_sources(vat))


@frappe.whitelist()
def get_vat_relationships(vat_invoice):
    vat=frappe.get_doc('VAT Invoice Record',vat_invoice);vat.check_permission('read')
    amounts=defaultdict(lambda:money(0))
    for row in vat.get('plan_allocations') or []:
        plan=frappe.get_doc('Contract Billing Plan',row.billing_plan);plan.check_permission('read')
        amounts[plan.sales_contract]+=money(row.allocated_amount)
    shares=invoice_shares(vat)
    if not amounts:
        for name,amount in shares.items():
            invoice=frappe.get_doc('Sales Invoice',name);invoice.check_permission('read')
            if invoice.get('custom_sales_contract'):amounts[invoice.custom_sales_contract]+=amount
        if not amounts and vat.sales_contract:amounts[vat.sales_contract]=money(vat.gross_amount)
    contracts=[]
    for name,amount in amounts.items():
        contract=frappe.get_doc('Sales Contract',name);contract.check_permission('read')
        contracts.append(dict(name=name,title=contract.contract_title,external_number=contract.external_number,allocated_amount=amount))
    funded=[]
    for row in vat.get('payment_allocations') or []:
        pe=frappe.get_doc('Payment Entry',row.payment_entry);pe.check_permission('read')
        funded.append(dict(payment_entry=row.payment_entry,sales_contract=row.sales_contract,billing_plan=row.billing_plan,
            allocated_amount=row.allocated_amount,actual_receipt_date=row.actual_receipt_date,status=pe.docstatus,basis='本票资金分配'))
    if not funded:
        for share in vat.get('plan_allocations') or []:
            plan=frappe.get_doc('Contract Billing Plan',share.billing_plan)
            if not plan.get('source_collection_plan'):continue
            left=money(share.allocated_amount)
            for name,budget in funded_payments(plan,stop_before=vat.name).items():
                amount=min(max(budget['amount'],money(0)),left)
                if not amount:continue
                pe=frappe.get_doc('Payment Entry',name);pe.check_permission('read')
                funded.append(dict(payment_entry=name,sales_contract=plan.sales_contract,billing_plan=plan.name,
                    allocated_amount=amount,actual_receipt_date=budget['date'],status=pe.docstatus,basis='历史资金依据按期次顺序推导，未改写原记录'))
                left-=amount
    # Preserve the distinction: these references settle AR, and may be shared by multiple tax invoices.
    settlements=[]
    for name,share in shares.items():
        invoice=frappe.get_doc('Sales Invoice',name);invoice.check_permission('read')
        for ref in frappe.db.sql('''SELECT pe.name,pe.posting_date,pe.custom_actual_receipt_date,r.allocated_amount
            FROM `tabPayment Entry Reference` r JOIN `tabPayment Entry` pe ON pe.name=r.parent
            WHERE pe.docstatus=1 AND pe.payment_type='Receive' AND r.reference_doctype='Sales Invoice' AND r.reference_name=%s
            ORDER BY pe.posting_date,pe.name''',name,as_dict=True):
            frappe.get_doc('Payment Entry',ref.name).check_permission('read')
            settlements.append(dict(payment_entry=ref.name,sales_invoice=name,invoice_share=share,
                allocated_to_invoice=ref.allocated_amount,actual_receipt_date=ref.custom_actual_receipt_date,
                posting_date=ref.posting_date,basis='应收核销共享依据，不重复计入本票资金分配'))
    return dict(currency=vat.currency,contracts=contracts,payment_allocations=funded,invoice_settlements=settlements)
