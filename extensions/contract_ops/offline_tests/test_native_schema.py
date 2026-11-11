import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class NativeSchemaTests(unittest.TestCase):
    def test_native_fields_are_not_redefined(self):
        fields = json.loads((ROOT / 'contract_ops/contract_fields.json').read_text(encoding='utf-8'))
        names = {field['fieldname'] for field in fields}
        self.assertEqual(len(names), len(fields))
        self.assertFalse(names & {'party_type', 'party_name', 'customer', 'start_date', 'end_date',
                                  'contract_terms', 'contract_template', 'is_signed', 'signee',
                                  'signed_on', 'status', 'fulfilment_terms', 'amended_from'})
        self.assertIn('contract_ops_enabled', names)
        self.assertFalse((ROOT / 'contract_ops/contract_operations/doctype/sales_contract/sales_contract.json').exists())

    def test_all_business_contract_links_use_native_master(self):
        found = 0
        for path in (ROOT / 'contract_ops/contract_operations/doctype').rglob('*.json'):
            for field in json.loads(path.read_text(encoding='utf-8')).get('fields', []):
                if field['fieldname'] in ('sales_contract', 'previous_contract', 'new_contract'):
                    self.assertEqual(field['options'], 'Contract', str(path))
                    found += 1
        self.assertGreaterEqual(found, 9)


if __name__ == '__main__':
    unittest.main()
