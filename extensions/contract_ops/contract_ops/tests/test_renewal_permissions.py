"""Document permissions, reassignment and disabled recipients on the isolated site."""
import frappe
from contract_ops import renewal


def run():
    assert frappe.local.site == 'erp.merged-test.localhost'
    frappe.set_user('Administrator')
    token = frappe.generate_hash(length=7)
    user = frappe.get_doc(dict(doctype='User', email=f'renewal-{token}@example.invalid', first_name='Renewal Tester',
        enabled=1, send_welcome_email=0, user_type='System User', roles=[dict(role='Sales Manager')])).insert()
    allowed = 'Contract Ops Test Company'
    denied = frappe.get_all('Company', filters={'name':['!=',allowed]}, pluck='name')[0]
    frappe.get_doc(dict(doctype='User Permission', user=user.name, allow='Company', for_value=allowed, apply_to_all_doctypes=1)).insert()
    def make(company, owner='Administrator'):
        c = frappe.get_doc(dict(doctype='Sales Contract', contract_title='TEST Renewal Permissions '+token,
            company=company, customer='Contract Ops Test Customer', currency='CNY', contract_amount=100,
            start_date='2025-01-01', end_date='2026-11-30', renewal_enabled=1, renewal_mode='到期协商续签',
            renewal_owner=owner, renewal_leads='30,7,1,0', renewal_notice_days=0, renewal_clause='ISOLATED TEST: expiry negotiation')).insert()
        c.submit()
        return c, renewal.get_cycles(c.name)['cycles'][0].name
    a, ca = make(allowed, user.name)
    b, cb = make(denied)
    renewal.daily()
    old = frappe.db.get_value(renewal.CYCLE,ca,'todo')
    assert old and frappe.db.get_value('ToDo',old,'allocated_to') == user.name
    frappe.set_user(user.name)
    try:
        assert frappe.has_permission('Sales Contract','read',doc=frappe.get_doc('Sales Contract',a.name))
        assert not frappe.has_permission('Sales Contract','read',doc=frappe.get_doc('Sales Contract',b.name))
        assert frappe.has_permission(renewal.CYCLE,'read',doc=frappe.get_doc(renewal.CYCLE,ca))
        assert not frappe.has_permission(renewal.CYCLE,'read',doc=frappe.get_doc(renewal.CYCLE,cb))
        rows = frappe.get_list(renewal.CYCLE, fields=['name','company'], limit_page_length=10000)
        assert ca in [r.name for r in rows] and cb not in [r.name for r in rows]
        assert all(r.company == allowed for r in rows)
        notices = frappe.get_list(renewal.NOTICE, fields=['cycle'], limit_page_length=10000)
        assert ca in [r.cycle for r in notices] and cb not in [r.cycle for r in notices]
        renewal.handle_cycle(ca, '洽谈中', 'ISOLATED TEST: authorized staff action', frappe.utils.nowdate())
        try:
            renewal.handle_cycle(cb, '洽谈中', 'ISOLATED TEST: forbidden', frappe.utils.nowdate())
        except frappe.PermissionError:
            pass
        else:
            raise AssertionError('restricted company write leak')
    finally:
        frappe.set_user('Administrator')
    frappe.db.set_value('User',user.name,'enabled',0)
    frappe.clear_cache(user=user.name)
    renewal.daily()
    assert frappe.db.get_value(renewal.CYCLE,ca,'health') == '负责人已停用或非系统用户'
    assert frappe.db.get_value('ToDo',old,'status') == 'Closed'
    a.reload(); a.renewal_owner='Administrator'; a.save()
    renewal.daily()
    new = frappe.db.get_value(renewal.CYCLE,ca,'todo')
    assert new != old and frappe.db.get_value('ToDo',new,'allocated_to') == 'Administrator'
    assert frappe.db.count(renewal.NOTICE,{'cycle':ca,'recipient':'Administrator'}) == 1
    a.reload(); a.renewal_enabled=0; a.save()
    b.reload(); b.renewal_enabled=0; b.save()
    frappe.db.commit()
    return dict(passed=12, company_permissions=True, runtime_disabled_owner=True, reassignment=True)
