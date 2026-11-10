"""Run only on the disposable v17 site restored from the v16 backup."""
import json
import frappe
from frappe.utils import now_datetime

from contract_ops.tests.helpers import make_contract
from contract_ops.migration import migrate_legacy_contracts


def run():
    assert frappe.local.site == 'erp.merged-test.localhost'
    frappe.set_user('Administrator')
    checks = []
    before = {dt: frappe.db.count(dt) for dt in ('Sales Invoice', 'VAT Invoice Record', 'Payment Entry', 'GL Entry')}
    company = 'Contract Ops Test Company'
    customer = 'Contract Ops Test Customer'
    token = frappe.generate_hash(length=8)

    def check(label, condition):
        assert condition, label
        checks.append(label)

    def fail(label, function):
        try:
            function()
        except (frappe.ValidationError, frappe.PermissionError):
            checks.append(label)
        else:
            raise AssertionError(label)

    def sales(**kw):
        values = dict(contract_title='TEST Native '+token, company=company, customer=customer,
                      currency='CNY', contract_amount=100, start_date='2025-01-01', end_date='2027-12-31')
        values.update(kw)
        return make_contract(values)

    # Ordinary native contracts must remain usable without a sales amount or this app's setup.
    generic = frappe.get_doc(dict(doctype='Contract', party_type='Customer', party_name=customer,
        contract_terms='ISOLATED TEST: native generic customer agreement')).insert()
    check('native unsigned status retained', generic.status == 'Unsigned')
    check('ordinary native customer contract has no sales obligations', not frappe.db.exists('Contract Renewal Cycle', {'sales_contract': generic.name}))
    fail('generic contract cannot fund sales plans', lambda: __import__('contract_ops.planning', fromlist=['lock_contract']).lock_contract(generic.name))

    template = frappe.get_doc(dict(doctype='Contract Template', title='TEST Native Template '+token,
        contract_terms='ISOLATED TEST: customer {{ party_name }}', requires_fulfilment=1,
        fulfilment_terms=[dict(requirement='ISOLATED TEST: deliver evidence')])).insert()
    from erpnext.crm.doctype.contract_template.contract_template import get_contract_template
    rendered = get_contract_template(template.name, dict(doctype='Contract', party_name=customer))
    check('native template renders original fields', customer in rendered['contract_terms'])
    check('native fulfilment template retained', rendered['contract_template'].requires_fulfilment == 1)
    c = sales(contract_template=template.name, contract_terms=rendered['contract_terms'], requires_fulfilment=1,
        fulfilment_deadline='2027-12-31', fulfilment_terms=[dict(requirement='ISOLATED TEST: deliver evidence')]).insert()
    c.submit()
    check('native signed active status', c.is_signed == 1 and c.status == 'Active')
    check('native submission records company signer', c.signed_by_company == 'Administrator')
    check('native fulfilment initially unfulfilled', c.fulfilment_status == 'Unfulfilled')
    c.fulfilment_terms[0].fulfilled = 1
    c.save()
    check('native fulfilment updates after submission', c.fulfilment_status == 'Fulfilled')
    def resign():
        doc = frappe.get_doc('Contract', c.name); doc.signee = 'different signer'; doc.save()
    fail('native signature protection survives extension', resign)
    def append_term():
        doc = frappe.get_doc('Contract', c.name); doc.append('fulfilment_terms', dict(requirement='extra')); doc.save()
    fail('native submitted checklist protection survives extension', append_term)
    fail('native date validation survives extension', lambda: sales(start_date='2028-01-01', end_date='2027-01-01').insert())
    fail('sales amount still required', lambda: sales(contract_amount=0).insert())
    supplier = frappe.get_doc(dict(doctype='Supplier', supplier_name='TEST Native Supplier '+token,
                                  supplier_type='Company')).insert()
    fail('supplier cannot enable sales extension', lambda: frappe.get_doc(dict(doctype='Contract',
        party_type='Supplier', party_name=supplier.name,
        contract_ops_enabled=1, contract_terms='ISOLATED TEST')).insert())
    supplier_contract = frappe.get_doc(dict(doctype='Contract', party_type='Supplier', party_name=supplier.name,
        contract_terms='ISOLATED TEST: generic supplier agreement')).insert()
    def change_party():
        doc = frappe.get_doc('Contract', c.name); doc.contract_ops_enabled = 0; doc.save()
    fail('sales scope cannot be silently disabled', change_party)

    created_before = frappe.db.count('Contract')
    repeated = migrate_legacy_contracts()
    check('legacy migration is idempotent', repeated['created'] == 0 and frappe.db.count('Contract') == created_before)
    legacy = frappe.db.get_value('Contract', {'legacy_sales_contract': ['is', 'set']}, 'name')
    if legacy:
        old = frappe.get_doc('Contract', legacy)
        snapshot = json.loads(old.legacy_source_snapshot)
        check('legacy identifier remains stable', old.name == snapshot['source']['name'] == old.legacy_sales_contract)
        check('legacy customer maps to native party', old.party_name == snapshot['source_row']['customer'])
        check('legacy signature is not invented', not old.is_signed and not old.signee and not old.signed_on)
        def change_source():
            doc = frappe.get_doc('Contract', legacy); doc.legacy_source_sha256 = 'changed'; doc.save()
        fail('legacy snapshot is immutable', change_source)
        original_hash = old.legacy_source_sha256
        try:
            frappe.db.set_value('Contract', legacy, 'legacy_source_sha256', '0' * 64, update_modified=False)
            fail('source conflict blocks repeat migration', migrate_legacy_contracts)
        finally:
            frappe.db.set_value('Contract', legacy, 'legacy_source_sha256', original_hash, update_modified=False)
        check('legacy editing path is removed', not frappe.db.exists('DocType', 'Sales Contract'))
    check('invoice link points at native master', frappe.get_meta('Sales Invoice').get_field('custom_sales_contract').options == 'Contract')

    account_user = frappe.get_doc(dict(doctype='User', email=f'native-accounts-{token}@example.invalid',
        first_name='Native Accounts Test', enabled=1, send_welcome_email=0,
        user_type='System User', roles=[dict(role='Accounts Manager')])).insert()
    check('accounts role can read sales contract', frappe.has_permission('Contract', 'read', doc=c, user=account_user.name))
    check('accounts role cannot read generic contract', not frappe.has_permission('Contract', 'read', doc=generic, user=account_user.name))
    mixed_user = frappe.get_doc(dict(doctype='User', email=f'native-mixed-{token}@example.invalid',
        first_name='Native Mixed Roles Test', enabled=1, send_welcome_email=0, user_type='System User',
        roles=[dict(role='Sales Manager'), dict(role='Accounts Manager')])).insert()
    check('adding accounting role preserves native customer access', frappe.has_permission('Contract', 'read', doc=generic, user=mixed_user.name))
    check('adding accounting role does not grant supplier access', not frappe.has_permission('Contract', 'read', doc=supplier_contract, user=mixed_user.name))
    frappe.set_user(account_user.name)
    try:
        names = frappe.get_list('Contract', pluck='name', limit_page_length=10000)
        check('accounts list matches document restriction', c.name in names and generic.name not in names)
    finally:
        frappe.set_user('Administrator')
    check('native tests do not post financial documents', before == {dt: frappe.db.count(dt) for dt in before})
    frappe.db.commit()
    return dict(passed=len(checks), checks=checks, browser_contract=c.name, template=template.name)
