"""Deterministic before/after comparison on the disposable restored database."""
import hashlib
import json
from pathlib import Path

import frappe

TABLES = ('Sales Contract', 'Contract', 'Sales Invoice', 'VAT Invoice Record', 'Payment Entry', 'GL Entry',
          'Contract Group', 'Contract Billing Plan', 'Contract Receipt Plan', 'Contract Collection Plan',
          'Contract Collection Allocation', 'VAT Invoice Allocation', 'VAT Payment Allocation',
          'Public Income Project', 'Public Income Contract Link', 'Public Income Import')


def snapshot(phase):
    assert frappe.local.site == 'erp.merged-test.localhost'
    assert phase in ('before', 'after')
    root = Path(frappe.get_site_path('private'))
    data = {dt: frappe.db.sql(f'SELECT * FROM `tab{dt}` ORDER BY name', as_dict=True)
            for dt in TABLES if frappe.db.table_exists(dt)}
    data = json.loads(json.dumps(data, default=str, ensure_ascii=False))
    (root / f'native-{phase}.json').write_text(json.dumps(data, ensure_ascii=False, sort_keys=True), encoding='utf-8')
    if phase == 'before':
        return {dt: len(rows) for dt, rows in data.items()}
    original = json.loads((root / 'native-before.json').read_text(encoding='utf-8'))
    checks, differences = {}, {}
    for dt, rows in original.items():
        current = {r['name']: r for r in data.get(dt, [])}
        changed = [{'name': row['name'], 'fields': [k for k, v in row.items() if current.get(row['name'], {}).get(k) != v]}
                   for row in rows if any(current.get(row['name'], {}).get(k) != v for k, v in row.items())]
        extra = set(current) - {row['name'] for row in rows}
        checks[dt] = not changed and (not extra or dt == 'Contract')
        if not checks[dt]:
            differences[dt] = dict(changed=changed, extra=sorted(extra))
    native = {r['name']: r for r in data['Contract']}
    mappings = []
    for row in original.get('Sales Contract', []):
        target = native.get(row['name'], {})
        source = json.loads(target.get('legacy_source_snapshot') or '{}')
        expected_hash = hashlib.sha256(json.dumps(row, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode('utf-8')).hexdigest()
        preserved = all(target.get('party_name' if key == 'customer' else key) == value
                        for key, value in row.items() if key not in ('naming_series', '_user_tags', '_comments', '_assign', '_liked_by'))
        # These internal fields are retained verbatim in the immutable source snapshot instead.
        evidence = source.get('source_row') == row and target.get('legacy_source_sha256') == expected_hash
        mappings.append(dict(source=row['name'], target=target.get('name'), business_fields=preserved,
                             source_evidence=evidence, signature_unconfirmed=not target.get('is_signed') and not target.get('signed_on')))
    checks['native_mapping'] = all(r['business_fields'] and r['source_evidence'] and r['signature_unconfirmed'] for r in mappings)
    report = dict(checks=checks, differences=differences, mappings=mappings,
                  before_counts={dt: len(rows) for dt, rows in original.items()},
                  after_counts={dt: len(rows) for dt, rows in data.items()})
    (root / 'native-preservation.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    assert all(checks.values()), {key: value for key, value in checks.items() if not value}
    return report
