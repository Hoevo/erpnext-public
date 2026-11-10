import sys
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'apps' / 'contract_ops'))
from contract_ops.planning_rules import allocate_fifo, business_due_date, money


class PlanningRulesTests(unittest.TestCase):
    def test_partial_and_excess_receipts_do_not_duplicate_cash(self):
        self.assertEqual(allocate_fifo(150, [100, 100]), [Decimal('100'), Decimal('50')])
        self.assertEqual(allocate_fifo(300, [100, 100]), [Decimal('100'), Decimal('100')])
        self.assertEqual(allocate_fifo(-10, [100]), [Decimal('0')])

    def test_cent_rounding_and_non_finite(self):
        self.assertEqual(sum(allocate_fifo('0.03', ['0.01']*4)), Decimal('0.03'))
        with self.assertRaises(ValueError):
            money('NaN')

    def test_evidenced_holidays_and_calendar_bounds(self):
        self.assertEqual(business_due_date(date(2026,11,6), 1, {date(2026,11,7),date(2026,11,8)},date(2026,1,1),date(2026,12,31)),date(2026,11,9))
        with self.assertRaises(ValueError):
            business_due_date(date(2026,12,31),1,set(),date(2026,1,1),date(2026,12,31))
