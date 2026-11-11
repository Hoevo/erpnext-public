"""Sales-only hooks; the ERPNext Contract controller remains authoritative."""
import frappe

from contract_ops.rules import validate_contract


def is_sales_contract(doc):
    return doc.get('party_type') == 'Customer' and bool(doc.get('contract_ops_enabled'))


def require_sales_contract(doc):
    if not is_sales_contract(doc):
        frappe.throw('请选择已启用销售票款管理的客户合同')
    return doc


def validate(doc, method=None):
    if frappe.flags.in_contract_ops_migration:
        return
    if doc.is_new() and any(doc.get(field) for field in
                            ('legacy_sales_contract', 'legacy_source_sha256', 'legacy_source_snapshot')):
        frappe.throw('旧合同来源与迁移证据只能由受控迁移生成')
    before = doc.get_doc_before_save()
    if before and before.get('contract_ops_enabled') and before.docstatus != 0:
        if doc.party_type != 'Customer' or doc.party_name != before.party_name or not doc.contract_ops_enabled:
            frappe.throw('已启用销售票款管理的合同不能更换客户或关闭业务归属，请修订合同')
    if before and any(str(doc.get(f) or '') != str(before.get(f) or '') for f in
                      ('legacy_sales_contract', 'legacy_source_sha256', 'legacy_source_snapshot', 'signing_date', 'custom_legacy_archived')):
        frappe.throw('旧合同来源与迁移证据不可改写')
    if not doc.get('contract_ops_enabled'):
        return
    require_sales_contract(doc)
    if not all(doc.get(f) for f in ('company', 'party_name', 'currency', 'contract_title')):
        frappe.throw('销售票款管理需要公司、客户、币种和合同名称')
    try:
        # Dates, signatures and fulfilment are validated by the native controller.
        validate_contract(doc.contract_amount)
    except ValueError as exc:
        frappe.throw(str(exc))
    from contract_ops.prepayment import validate_flow_change
    from contract_ops.renewal import validate_contract as validate_renewal
    validate_flow_change(doc)
    validate_renewal(doc)


def synchronize(doc, method=None):
    if frappe.flags.in_contract_ops_migration or not is_sales_contract(doc):
        return
    from contract_ops.renewal import synchronize as sync_renewal
    sync_renewal(doc)


def before_cancel(doc, method=None):
    if not is_sales_contract(doc):
        return
    from contract_ops.prepayment import active_funding
    for name in frappe.get_all('Contract Collection Plan', filters={'sales_contract': doc.name}, pluck='name'):
        if active_funding(name):
            frappe.throw('合同仍有先款后票的实际到账，请先处理关联收款')
        frappe.db.set_value('Contract Collection Plan', name, dict(disabled=1, status='已停用'))
    for doctype, field in (('Sales Invoice', 'custom_sales_contract'), ('VAT Invoice Record', 'sales_contract')):
        if frappe.db.exists(doctype, {field: doc.name, 'docstatus': 1}):
            frappe.throw('请先取消关联合同的已提交发票，再取消合同')
    for name in frappe.get_all('Contract Billing Plan', filters={'sales_contract': doc.name}, pluck='name'):
        frappe.db.set_value('Contract Billing Plan', name, dict(disabled=1, status='已停用'))
    doc.ignore_linked_doctypes = ('Contract Billing Plan', 'Contract Receipt Plan', 'Contract Collection Plan')


def _account_party_scope(user=None):
    from frappe.permissions import get_valid_perms
    from erpnext.crm.doctype.contract.contract import ROLE_PARTY_TYPES
    roles = {permission.role for permission in get_valid_perms('Contract', user=user)}
    if 'Accounts Manager' not in roles:
        return None
    ordinary = roles - {'Accounts Manager'}
    if (user or frappe.session.user) == 'Administrator' or ordinary - ROLE_PARTY_TYPES.keys():
        return None
    return {ROLE_PARTY_TYPES[role] for role in ordinary}


def has_permission(doc, ptype=None, user=None):
    # Never grant access here: native role, party and company checks still apply.
    scope = _account_party_scope(user)
    if scope is not None:
        return doc.get('party_type') in scope or is_sales_contract(doc)
    return True


def permission_query(user=None, doctype=None):
    scope = _account_party_scope(user)
    if scope is None:
        return ''
    sales = "(`tabContract`.party_type='Customer' AND `tabContract`.contract_ops_enabled=1)"
    if scope:
        native = ','.join(frappe.db.escape(value) for value in sorted(scope))
        return f'({sales} OR `tabContract`.party_type IN ({native}))'
    return sales
