"""Contract-based reminders, immutable period snapshots and explicit human decisions."""
import hashlib
import json
from datetime import timedelta
from html import escape

import frappe
from frappe.utils import getdate, nowdate
from contract_ops.renewal_rules import MODES, OPEN, OUTCOMES, deadline, due_stage, next_period, offsets

CYCLE = 'Contract Renewal Cycle'
NOTICE = 'Contract Renewal Notice'
RULE_FIELDS = ('renewal_mode', 'renewal_notice_days', 'renewal_months', 'renewal_review_date', 'renewal_leads', 'renewal_clause')


def key(*parts):
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str, ensure_ascii=False).encode('utf-8')).hexdigest()


def internal_save(doc):
    doc.flags.renewal_internal = True
    return doc.insert(ignore_permissions=True) if doc.is_new() else doc.save(ignore_permissions=True)


def recipient_issue(contract):
    owner = contract.renewal_owner
    if not owner:
        return '缺少负责人'
    user = frappe.db.get_value('User', owner, ['enabled', 'user_type'], as_dict=True)
    if not user or not user.enabled or user.user_type != 'System User':
        return '负责人已停用或非系统用户'
    if contract.name and not contract.is_new() and not frappe.has_permission('Contract', 'read', doc=contract, user=owner):
        return '负责人无合同查看权限'
    if not frappe.has_permission(CYCLE, 'read', user=owner):
        return '负责人无续签记录查看权限'
    return None


def configuration_issue(doc):
    if doc.renewal_mode not in MODES or doc.renewal_mode == '待核实':
        return '续签方式待核实'
    if not doc.renewal_clause or not doc.renewal_clause.strip():
        return '缺少条款依据'
    try:
        days = int(doc.renewal_notice_days or 0)
        if days < 0 or days > 3660:
            return '提前通知天数必须在0至3660之间'
        due = deadline(doc.renewal_mode, doc.end_date, days, doc.renewal_review_date)
        offsets(doc.renewal_leads)
        if doc.renewal_mode != '无固定期限':
            if not doc.start_date:
                return '缺少履行开始日期'
            if due < getdate(doc.start_date):
                return '办理截止日早于合同开始日期，请核对条款'
        if doc.renewal_mode == '自动续期':
            next_period(doc.end_date, doc.renewal_months or 0)
    except (ValueError, TypeError, OverflowError) as exc:
        return str(exc)
    return recipient_issue(doc)


def validate_contract(doc):
    before = doc.get_doc_before_save()
    if before and any(str(before.get(f) or '') != str(doc.get(f) or '') for f in RULE_FIELDS):
        if frappe.db.exists(CYCLE, {'sales_contract': doc.name, 'status': ['in', OUTCOMES[1:]]}):
            frappe.throw('已有完成的续签周期，不能改写其条款；条款变更请登记新合同。负责人和提醒开关仍可调整。')
    issue = configuration_issue(doc)
    doc.renewal_health = issue or ('已启用' if doc.renewal_enabled else '未启用')
    if doc.renewal_enabled and issue:
        frappe.throw('无法启用续签提醒：' + issue)


def close_todo(cycle):
    if cycle.todo and frappe.db.exists('ToDo', cycle.todo):
        todo = frappe.get_doc('ToDo', cycle.todo)
        if todo.status == 'Open':
            todo.status = 'Closed'
            todo.save(ignore_permissions=True)
    cycle.todo = None


def projected(cycle, today):
    today = getdate(today)
    due = getdate(cycle.deadline)
    sent = set(frappe.get_all(NOTICE, filters={'cycle': cycle.name, 'recipient': cycle.responsible_user}, pluck='stage'))
    stage = due_stage(today, due, cycle.leads)
    if stage and stage not in sent:
        return today
    if today > due:
        bucket = ((today - due).days - 1) // 7
        return due + timedelta(days=1 + (bucket + 1) * 7)
    future = [due - timedelta(days=n) for n in offsets(cycle.leads) if due - timedelta(days=n) > today]
    return min(future) if future else due + timedelta(days=1)


def new_cycle(contract, start, end, previous=None):
    signature = key(contract.name, start, end, *[contract.get('party_name' if f == 'customer' else f) for f in RULE_FIELDS])
    existing = frappe.db.get_value(CYCLE, {'rule_key': signature}, 'name')
    if existing:
        cycle = frappe.get_doc(CYCLE, existing)
        if cycle.status in ('已停用', '规则已替换'):
            cycle.status = '待处理'
            internal_save(cycle)
        return cycle
    cycle = frappe.get_doc(dict(doctype=CYCLE, sales_contract=contract.name, contract_title=contract.contract_title,
        company=contract.company, customer=contract.party_name, period_start=start, period_end=end,
        mode=contract.renewal_mode, deadline=deadline(contract.renewal_mode, end, contract.renewal_notice_days, contract.renewal_review_date),
        responsible_user=contract.renewal_owner, notice_days=contract.renewal_notice_days,
        months=contract.renewal_months, leads=contract.renewal_leads, clause=contract.renewal_clause,
        rule_key=signature, previous_cycle=previous, status='待处理'))
    internal_save(cycle)
    cycle.next_reminder = projected(cycle, nowdate())
    return internal_save(cycle)


def synchronize(contract, method=None):
    """Called after save and by the scheduler, including disabled/cancelled contracts."""
    if contract.is_new():
        return
    frappe.db.sql('SELECT name FROM `tabContract` WHERE name=%s FOR UPDATE', contract.name)
    active = [frappe.get_doc(CYCLE, name) for name in frappe.get_all(CYCLE,
        filters={'sales_contract': contract.name, 'status': ['in', OPEN]}, pluck='name')]
    issue = configuration_issue(contract)
    if contract.docstatus != 1 or not contract.renewal_enabled or issue:
        for cycle in active:
            cycle.health = issue or '提醒已关闭'
            cycle.next_reminder = None
            if contract.docstatus == 2:
                cycle.status = '已停用'
            close_todo(cycle)
            internal_save(cycle)
        frappe.db.set_value('Contract', contract.name, 'renewal_health', issue or '未启用', update_modified=False)
        return
    expected = key(contract.name, contract.start_date, contract.end_date, *[contract.get('party_name' if f == 'customer' else f) for f in RULE_FIELDS])
    # Completed periods remain historical. Auto-renewal creates its successor explicitly in handle_cycle().
    completed = frappe.db.exists(CYCLE, {'sales_contract': contract.name, 'status': ['in', OUTCOMES[1:]]})
    if not completed:
        for cycle in active:
            if cycle.rule_key != expected:
                close_todo(cycle)
                cycle.status, cycle.health, cycle.next_reminder = '规则已替换', '规则已替换', None
                internal_save(cycle)
        new_cycle(contract, contract.start_date, contract.end_date)
    for name in frappe.get_all(CYCLE, filters={'sales_contract': contract.name, 'status': ['in', OPEN]}, pluck='name'):
        cycle = frappe.get_doc(CYCLE, name)
        if cycle.responsible_user != contract.renewal_owner:
            close_todo(cycle)
            cycle.responsible_user = contract.renewal_owner
        cycle.health = '已逾期' if getdate(cycle.deadline) < getdate(nowdate()) else '正常'
        cycle.next_reminder = projected(cycle, nowdate())
        internal_save(cycle)
    frappe.db.set_value('Contract', contract.name, 'renewal_health', '已启用', update_modified=False)


def send_due(cycle, today):
    stage = due_stage(today, cycle.deadline, cycle.leads)
    if not stage:
        return False
    unique = key(cycle.name, cycle.responsible_user, stage)
    if frappe.db.exists(NOTICE, {'notice_key': unique}):
        # Closing a generic ToDo does not complete the business cycle.
        ensure_todo(cycle)
        return False
    subject = f'合同续签／到期提醒：{cycle.contract_title}（办理截止 {cycle.deadline}）'
    content = escape(f'{cycle.customer}；{cycle.mode}；本周期 {cycle.period_start or "未固定"} 至 {cycle.period_end or "未固定"}；办理截止 {cycle.deadline}。请打开续签周期登记办理结果。')
    ensure_todo(cycle, content)
    notification = frappe.get_doc(dict(doctype='Notification Log', type='Contract Renewal',
        for_user=cycle.responsible_user, from_user='Administrator', subject=escape(subject),
        email_content=content, document_type=CYCLE, document_name=cycle.name)).insert(ignore_permissions=True)
    internal_save(frappe.get_doc(dict(doctype=NOTICE, cycle=cycle.name, recipient=cycle.responsible_user,
        stage=stage, sent_on=today, notification=notification.name, notice_key=unique)))
    cycle.next_reminder = projected(cycle, today)
    internal_save(cycle)
    return True


def ensure_todo(cycle, content=None):
    if cycle.todo and frappe.db.get_value('ToDo', cycle.todo, 'status') == 'Open':
        return
    todo = frappe.get_doc(dict(doctype='ToDo', allocated_to=cycle.responsible_user,
        assigned_by='Administrator', reference_type=CYCLE, reference_name=cycle.name,
        date=cycle.deadline, priority='High', description=content or escape(f'请办理合同 {cycle.contract_title} 的续签／到期事项，截止 {cycle.deadline}。'))).insert(ignore_permissions=True)
    cycle.todo = todo.name
    internal_save(cycle)


def daily():
    """One contract per transaction: locking plus unique event keys prevent duplicates."""
    today = nowdate()
    sent, failed = 0, 0
    names = set(frappe.get_all('Contract', filters={'party_type': 'Customer', 'contract_ops_enabled': 1, 'renewal_enabled': 1}, pluck='name'))
    names.update(frappe.get_all(CYCLE, filters={'status': ['in', OPEN]}, pluck='sales_contract'))
    for name in sorted(names):
        try:
            contract_sent = 0
            frappe.db.sql('SELECT name FROM `tabContract` WHERE name=%s FOR UPDATE', name)
            contract = frappe.get_doc('Contract', name)
            synchronize(contract)
            if contract.docstatus == 1 and contract.renewal_enabled and not configuration_issue(contract):
                for cycle_name in frappe.get_all(CYCLE, filters={'sales_contract': name, 'status': ['in', OPEN]}, pluck='name'):
                    contract_sent += int(send_due(frappe.get_doc(CYCLE, cycle_name), today))
            frappe.db.commit()
            sent += contract_sent
        except Exception:
            frappe.db.rollback()
            failed += 1
            frappe.log_error(title=f'合同续签提醒失败：{name}')
    return dict(sent=sent, failed=failed)


@frappe.whitelist()
def get_cycles(sales_contract):
    contract = frappe.get_doc('Contract', sales_contract)
    contract.check_permission('read')
    from contract_ops.native_contract import require_sales_contract
    require_sales_contract(contract)
    rows = frappe.get_all(CYCLE, filters={'sales_contract': sales_contract}, fields=['*'], order_by='creation asc')
    return dict(health=configuration_issue(contract) or ('已启用' if contract.renewal_enabled else '未启用'), cycles=rows)


@frappe.whitelist(methods=['POST'])
def handle_cycle(cycle_name, outcome, evidence, handled_on, new_contract=None, evidence_file=None):
    name = frappe.db.get_value(CYCLE, cycle_name, 'sales_contract')
    if not name:
        frappe.throw('续签周期不存在')
    frappe.db.sql('SELECT name FROM `tabContract` WHERE name=%s FOR UPDATE', name)
    contract = frappe.get_doc('Contract', name)
    contract.check_permission('write')
    from contract_ops.native_contract import require_sales_contract
    require_sales_contract(contract)
    cycle = frappe.get_doc(CYCLE, cycle_name)
    if contract.docstatus != 1 or cycle.status not in OPEN or not contract.renewal_enabled:
        frappe.throw('本周期已完成或提醒未启用，不能继续办理')
    if outcome not in OUTCOMES or not str(evidence or '').strip():
        frappe.throw('请选择办理结果并填写处理说明')
    if not handled_on or getdate(handled_on) > getdate(nowdate()):
        frappe.throw('处理日期必填且不能晚于今天')
    if cycle.period_start and getdate(handled_on) < getdate(cycle.period_start):
        frappe.throw('处理日期不能早于本周期开始日期')
    if outcome == '已确认按条款续期' and cycle.mode != '自动续期':
        frappe.throw('仅自动续期合同可以确认按条款续期')
    if outcome == '已终止' and cycle.period_end and getdate(handled_on) < getdate(cycle.period_end):
        frappe.throw('周期尚未结束；提前终止请登记新合同或保留终止协议后按实际情况处理')
    if outcome == '已确认按条款续期' and getdate(handled_on) <= getdate(cycle.period_end):
        frappe.throw('尚未进入下一周期，不能提前确认自动续期；请先登记洽谈中')
    if outcome == '已签新合同':
        if not new_contract or new_contract == name:
            frappe.throw('请选择已提交的续签新合同')
        successor = frappe.get_doc('Contract', new_contract)
        successor.check_permission('read')
        require_sales_contract(successor)
        if successor.docstatus != 1 or successor.company != contract.company or successor.party_name != contract.party_name:
            frappe.throw('新合同须已提交且公司、客户与原合同一致')
        if not successor.start_date or (cycle.period_end and getdate(successor.start_date) <= getdate(cycle.period_end)):
            frappe.throw('续签新合同必须在本周期结束之后开始')
    elif new_contract:
        frappe.throw('只有已签新合同状态可关联新合同')
    if evidence_file:
        files = frappe.get_all('File', filters={'file_url': evidence_file}, pluck='name')
        if not files or not any(frappe.has_permission('File', 'read', doc=frappe.get_doc('File', f)) for f in files):
            frappe.throw('处理附件不存在或无权访问')
    cycle.add_comment('Comment', escape(f'{outcome}；处理日期 {handled_on}；{evidence}'))
    cycle.status, cycle.evidence, cycle.handled_on, cycle.handled_by = outcome, evidence, handled_on, frappe.session.user
    cycle.new_contract, cycle.evidence_file = new_contract, evidence_file
    if outcome != '洽谈中':
        close_todo(cycle)
        cycle.next_reminder, cycle.health = None, '已完成'
    internal_save(cycle)
    if outcome == '已确认按条款续期':
        start, end = next_period(cycle.period_end, cycle.months)
        new_cycle(contract, start, end, cycle.name)
    return cycle.name


def cycle_permission(doc, ptype='read', user=None):
    if ptype not in ('read', 'report', 'export', 'print'):
        return False
    return frappe.has_permission('Contract', 'read', doc=frappe.get_doc('Contract', doc.sales_contract), user=user)


def cycle_query(user=None):
    from frappe.desk.reportview import build_match_conditions
    cond = build_match_conditions('Contract', user=user)
    return 'EXISTS (SELECT name FROM `tabContract` WHERE name=`tabContract Renewal Cycle`.sales_contract' + (f' AND ({cond})' if cond else '') + ')'


def notice_permission(doc, ptype='read', user=None):
    return cycle_permission(frappe.get_doc(CYCLE, doc.cycle), ptype, user)


def notice_query(user=None):
    condition = cycle_query(user)
    return f'EXISTS (SELECT name FROM `tabContract Renewal Cycle` WHERE name=`tabContract Renewal Notice`.cycle AND ({condition}))'
