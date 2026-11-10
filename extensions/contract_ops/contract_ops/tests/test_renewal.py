from contract_ops.tests.helpers import make_contract
"""Integration tests on the disposable site; never run on a business site."""
import frappe
from unittest.mock import patch
from contract_ops import renewal


def run():
    assert frappe.local.site == 'erp.merged-test.localhost'
    frappe.set_user('Administrator')
    checks = []
    token = frappe.generate_hash(length=6)
    before = {dt: frappe.db.count(dt) for dt in ('Sales Invoice', 'Payment Entry', 'VAT Invoice Record', 'GL Entry')}

    def check(label, condition):
        assert condition, label
        checks.append(label)

    def fail(label, fn):
        try:
            fn()
        except frappe.ValidationError:
            checks.append(label)
            return
        raise AssertionError(label)

    def contract(**kw):
        values = dict(doctype='Contract', contract_title='TEST Renewal '+token,
            company='Contract Ops Test Company', customer='Contract Ops Test Customer',
            currency='CNY', contract_amount=100, start_date='2025-01-01', end_date='2026-12-31',
            renewal_mode='自动续期', renewal_enabled=1, renewal_owner='Administrator',
            renewal_notice_days=30, renewal_months=12, renewal_leads='30,7,1,0',
            renewal_clause='ISOLATED TEST: auto-renew annually unless notice 30 calendar days before expiry')
        values.update(kw)
        doc = make_contract(values).insert()
        doc.submit()
        return doc

    with patch('contract_ops.renewal.nowdate', return_value='2026-11-10'):
        fail('missing owner prevents enabling', lambda: contract(renewal_owner=None))
        fail('unknown rules prevent enabling', lambda: contract(renewal_mode='待核实'))
        fail('missing clause prevents enabling', lambda: contract(renewal_clause=''))
        c = contract()
        rows = renewal.get_cycles(c.name)['cycles']
        check('one submitted period', len(rows) == 1)
        cycle = frappe.get_doc(renewal.CYCLE, rows[0].name)
        check('deadline derived from termination notice', str(cycle.deadline) == '2026-12-01')
        check('next reminder catches up today', str(cycle.next_reminder) == '2026-11-10')
        result = renewal.daily()
        check('real scheduler sends successfully', result['sent'] >= 1 and result['failed'] == 0)
        notices = frappe.get_all(renewal.NOTICE, filters={'cycle': cycle.name}, fields=['*'])
        check('one notification for missed most urgent stage', len(notices) == 1 and notices[0].stage == 'before:30')
        check('real notification exists', bool(frappe.db.exists('Notification Log', notices[0].notification)))
        cycle.reload()
        check('real assigned ToDo exists', frappe.db.get_value('ToDo', cycle.todo, 'allocated_to') == 'Administrator')
        renewal.daily()
        check('repeat job is idempotent', frappe.db.count(renewal.NOTICE, {'cycle': cycle.name}) == 1)
        old_todo = cycle.todo
        c.reload(); c.renewal_enabled = 0; c.save()
        check('disable closes only own ToDo', frappe.db.get_value('ToDo', old_todo, 'status') == 'Closed')
        renewal.daily()
        check('disabled does not send', frappe.db.count(renewal.NOTICE, {'cycle': cycle.name}) == 1)
        c.reload(); c.renewal_enabled = 1; c.save()
        renewal.daily(); cycle.reload()
        check('re-enable preserves period and dedupe', frappe.db.count(renewal.CYCLE, {'sales_contract': c.name}) == 1 and frappe.db.count(renewal.NOTICE, {'cycle': cycle.name}) == 1)
        check('re-enable restores open ToDo', frappe.db.get_value('ToDo', cycle.todo, 'status') == 'Open')
        renewal.handle_cycle(cycle.name, '洽谈中', 'ISOLATED TEST: contacting customer', '2026-11-10')
        fail('cannot confirm renewal before actual next period', lambda: renewal.handle_cycle(cycle.name, '已确认按条款续期', 'test', '2026-11-10'))
        fail('cannot silently edit system snapshot', lambda: frappe.get_doc(renewal.CYCLE, cycle.name).save())
        changed = contract(renewal_mode='到期协商续签', renewal_notice_days=0)
        original = frappe.db.get_value(renewal.CYCLE, {'sales_contract': changed.name}, 'name')
        changed.renewal_notice_days = 5; changed.save()
        check('rule edits supersede old snapshot', frappe.db.get_value(renewal.CYCLE, original, 'status') == '规则已替换')
        changed.renewal_enabled = 0; changed.save()
        review = contract(renewal_mode='无固定期限', end_date=None, renewal_review_date='2027-06-01')
        check('open ended review supported', str(renewal.get_cycles(review.name)['cycles'][0].deadline) == '2027-06-01')
        review.renewal_enabled = 0; review.save()
        cancelled = contract()
        cancelled.cancel()
        check('cancel disables reminder period', renewal.get_cycles(cancelled.name)['cycles'][0].status == '已停用')

    with patch('contract_ops.renewal.nowdate', return_value='2026-11-25'):
        renewal.daily()
        check('later stage still sends during negotiations', frappe.db.count(renewal.NOTICE, {'cycle': cycle.name, 'stage': 'before:7'}) == 1)
    with patch('contract_ops.renewal.nowdate', return_value='2026-12-02'):
        renewal.daily(); renewal.daily()
        check('overdue reminder deduped', frappe.db.count(renewal.NOTICE, {'cycle': cycle.name, 'stage': 'overdue:0'}) == 1)
    with patch('contract_ops.renewal.nowdate', return_value='2026-12-09'):
        renewal.daily()
        check('weekly overdue followup', frappe.db.count(renewal.NOTICE, {'cycle': cycle.name, 'stage': 'overdue:1'}) == 1)
    with patch('contract_ops.renewal.nowdate', return_value='2027-01-01'):
        renewal.handle_cycle(cycle.name, '已确认按条款续期', 'ISOLATED TEST: no termination notice, continuation confirmed', '2027-01-01')
        rows = renewal.get_cycles(c.name)['cycles']
        following = next(r for r in rows if r.previous_cycle == cycle.name)
        check('next period calculated without rewriting original', str(following.period_start) == '2027-01-01' and str(following.period_end) == '2027-12-31')
        check('next cancellation deadline follows new period', str(following.deadline) == '2027-12-01')
        check('finished todo is closed', frappe.db.get_value('ToDo', cycle.todo, 'status') == 'Closed')
        c.reload()
        check('original signed dates unchanged', str(c.end_date) == '2026-12-31')
        fail('finished period cannot be processed twice', lambda: renewal.handle_cycle(cycle.name, '已确认按条款续期', 'test', '2027-01-01'))
        def overwrite():
            x = frappe.get_doc('Contract', c.name); x.renewal_notice_days = 10; x.save()
        fail('completed rules protected', overwrite)
        next_c = contract(start_date='2028-01-01', end_date='2028-12-31', renewal_enabled=0)
        fail('new contract required for signed outcome', lambda: renewal.handle_cycle(following.name, '已签新合同', 'test', '2027-01-01'))
        renewal.handle_cycle(following.name, '已签新合同', 'ISOLATED TEST: successor signed', '2027-01-01', new_contract=next_c.name)
        check('successor linked and period closed', frappe.db.get_value(renewal.CYCLE, following.name, 'new_contract') == next_c.name)
        check('completed contract creates no extra cycles', len(renewal.get_cycles(c.name)['cycles']) == 2)

    frappe.set_user('Guest')
    try:
        try:
            renewal.get_cycles(c.name)
        except frappe.PermissionError:
            checks.append('guest cannot view contract cycles')
        else:
            raise AssertionError('guest read leak')
        try:
            renewal.handle_cycle(cycle.name, '洽谈中', 'test', '2026-11-10')
        except frappe.PermissionError:
            checks.append('guest cannot process cycles')
        else:
            raise AssertionError('guest write leak')
    finally:
        frappe.set_user('Administrator')
    check('cycle list permission query works', isinstance(frappe.get_list(renewal.CYCLE, limit_page_length=5), list))
    check('notice list permission query works', isinstance(frappe.get_list(renewal.NOTICE, limit_page_length=5), list))
    check('all financial row counts unchanged', before == {dt: frappe.db.count(dt) for dt in before})
    # Keep a current, open specimen for the browser flow.
    with patch('contract_ops.renewal.nowdate', return_value='2026-11-10'):
        specimen = contract(renewal_mode='到期协商续签', end_date='2026-11-30', renewal_notice_days=0)
        renewal.daily()
    frappe.db.commit()
    return dict(passed=len(checks), checks=checks, browser_contract=specimen.name,
        browser_cycle=renewal.get_cycles(specimen.name)['cycles'][0].name)
