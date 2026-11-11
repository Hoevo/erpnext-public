"""Identity-preserving conversion of legacy masters; never creates financial documents."""
import hashlib
import json

import frappe

OPERATION = 'sales-contract-to-native-contract/1'
FIELD_MAP = {'customer': 'party_name'}


def source_json(row):
    return json.dumps(dict(row), ensure_ascii=False, sort_keys=True, default=str, separators=(',', ':'))


def migrate_legacy_contracts():
    if not frappe.db.table_exists('Sales Contract'):
        return {'created': 0, 'existing': 0}
    rows = frappe.db.sql('SELECT * FROM `tabSales Contract` ORDER BY name FOR UPDATE', as_dict=True)
    # Preflight every identity before any row or dynamic reference changes.
    fingerprints = {row.name: hashlib.sha256(source_json(row).encode('utf-8')).hexdigest() for row in rows}
    for row in rows:
        if frappe.db.exists('Contract', row.name):
            existing = frappe.db.get_value('Contract', row.name, ['legacy_sales_contract', 'legacy_source_sha256'], as_dict=True)
            if existing.legacy_sales_contract != row.name or existing.legacy_source_sha256 != fingerprints[row.name]:
                frappe.throw(f'合同编号冲突或旧来源变化，迁移已停止：{row.name}')
    created = 0
    savepoint = 'native_contract_conversion'
    frappe.db.savepoint(savepoint)
    prior_flag = frappe.flags.in_contract_ops_migration
    frappe.flags.in_contract_ops_migration = True
    try:
        for row in rows:
            if frappe.db.exists('Contract', row.name):
                continue
            fields = set(frappe.get_meta('Contract').get_valid_columns())
            excluded = {'name', 'doctype', 'docstatus', 'owner', 'creation', 'modified', 'modified_by',
                        'naming_series', 'customer', '_user_tags', '_comments', '_assign', '_liked_by'}
            values = {key: value for key, value in row.items() if key in fields and key not in excluded}
            values.update(doctype='Contract', party_type='Customer', party_name=row.customer, contract_ops_enabled=1,
                contract_terms='历史合同正文未转录；请查阅原件附件。', is_signed=0,
                legacy_sales_contract=row.name, legacy_source_sha256=fingerprints[row.name],
                legacy_source_snapshot=json.dumps(dict(operation=OPERATION,
                    source=dict(doctype='Sales Contract', name=row.name), target=dict(doctype='Contract', name=row.name),
                    valid_from=str(row.start_date) if row.start_date else None,
                    valid_to=str(row.end_date) if row.end_date else None,
                    field_mapping=FIELD_MAP, signature_status='unconfirmed: legacy schema had no signee or signature',
                    source_row=json.loads(source_json(row))), ensure_ascii=False, sort_keys=True))
            doc = frappe.get_doc(values).insert(ignore_permissions=True, set_name=row.name)
            # Insertion defaults must not replace historical blanks or recompute source health.
            for field, value in values.items():
                if field != 'doctype':
                    doc.set(field, value)
            # Preserve submission state and record times without pretending a new signing/submission happened.
            doc.docstatus = row.docstatus
            if row.docstatus == 2:
                doc.status = 'Cancelled'
            for field in ('owner', 'creation', 'modified', 'modified_by'):
                doc.set(field, row.get(field))
            doc.db_update()
            created += 1
        names = tuple(row.name for row in rows)
        if names:
            for doctype, type_field, name_field in (
                ('File', 'attached_to_doctype', 'attached_to_name'),
                ('Comment', 'reference_doctype', 'reference_name'),
                ('Version', 'ref_doctype', 'docname'),
                ('ToDo', 'reference_type', 'reference_name'),
                ('Notification Log', 'document_type', 'document_name'),
            ):
                frappe.db.sql(f'UPDATE `tab{doctype}` SET `{type_field}`=%s '
                              f'WHERE `{type_field}`=%s AND `{name_field}` IN %s',
                              ('Contract', 'Sales Contract', names))
            # Retain the original table as immutable evidence, with no second editing path.
            for table in ('DocPerm', 'Custom DocPerm'):
                frappe.db.sql(f'UPDATE `tab{table}` SET `write`=0, `create`=0, `delete`=0, '
                              '`submit`=0, `cancel`=0, `amend`=0 WHERE parent=%s', 'Sales Contract')
            frappe.clear_cache(doctype='Sales Contract')
        return {'created': created, 'existing': len(rows) - created, 'operation': OPERATION}
    except Exception:
        frappe.db.rollback(save_point=savepoint)
        raise
    finally:
        frappe.flags.in_contract_ops_migration = prior_flag
