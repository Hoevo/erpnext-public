import sys
import unittest
from datetime import date
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from contract_ops.renewal_rules import deadline, due_stage, next_period, offsets


class RenewalRulesTests(unittest.TestCase):
    def test_notice_deadline_and_review(self):
        self.assertEqual(deadline('自动续期','2027-12-31',30),date(2027,12,1))
        self.assertEqual(deadline('无固定期限',None,review='2027-06-01'),date(2027,6,1))
        with self.assertRaises(ValueError): deadline('待核实','2027-12-31')
        with self.assertRaises(ValueError): deadline('无固定期限',None)

    def test_leap_and_month_end_periods(self):
        self.assertEqual(next_period('2023-12-31',12),(date(2024,1,1),date(2024,12,31)))
        self.assertEqual(next_period('2024-01-31',1),(date(2024,2,1),date(2024,2,29)))
        self.assertEqual(next_period('2024-02-29',12),(date(2024,3,1),date(2025,2,28)))
        with self.assertRaises(ValueError): next_period('2024-01-31',0)

    def test_catchup_most_urgent_and_overdue_week(self):
        self.assertIsNone(due_stage('2027-10-31','2027-12-01','30,7,1,0'))
        self.assertEqual(due_stage('2027-11-25','2027-12-01','30,7,1,0'),'before:7')
        self.assertEqual(due_stage('2027-12-01','2027-12-01','30,7,1,0'),'before:0')
        self.assertEqual(due_stage('2027-12-02','2027-12-01','30,7'),'overdue:0')
        self.assertEqual(due_stage('2027-12-08','2027-12-01','30,7'),'overdue:0')
        self.assertEqual(due_stage('2027-12-09','2027-12-01','30,7'),'overdue:1')

    def test_offset_validation(self):
        self.assertEqual(offsets('30，7,7,0'),[30,7,0])
        for value in ('', '-1', '1.5', 'abc', '4000'):
            with self.assertRaises(ValueError): offsets(value)
