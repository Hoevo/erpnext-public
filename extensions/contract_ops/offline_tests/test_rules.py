import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from contract_ops.rules import money, validate_contract, validate_limit, validate_tax_amount


class ContractRulesTests(unittest.TestCase):
    def test_partial_invoices_at_exact_limit(self):
        validate_limit("100000", "40000", "60000")

    def test_fractional_money_uses_decimal(self):
        validate_limit("0.30", "0.10", "0.20")

    def test_overbilling_rejected(self):
        with self.assertRaises(ValueError):
            validate_limit("100000", "40000", "60000.01")

    def test_non_finite_values_rejected(self):
        for value in ("NaN", "Infinity", "-Infinity", "abc"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                money(value)

    def test_contract_requires_positive_amount(self):
        for value in (0, -1):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_contract(value)

    def test_invalid_period_rejected(self):
        with self.assertRaises(ValueError):
            validate_contract(100, "2026-11-06", "2026-11-05")

    def test_blue_tax_invoice_constraints(self):
        validate_tax_amount("106", "6")
        for amount, tax in ((0, 0), (-106, -6), (106, -1), (106, 107)):
            with self.subTest(amount=amount, tax=tax), self.assertRaises(ValueError):
                validate_tax_amount(amount, tax)


if __name__ == "__main__":
    unittest.main()
