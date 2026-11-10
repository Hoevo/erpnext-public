"""Explicitly signed native fixtures; never use to convert historical business data."""
import frappe
from frappe.utils import now_datetime


def make_contract(values):
    values = dict(values)
    values['doctype'] = 'Contract'
    values['party_type'] = 'Customer'
    values['party_name'] = values.pop('customer', values.get('party_name'))
    values.setdefault('contract_ops_enabled', 1)
    values.setdefault('contract_terms', 'ISOLATED TEST: sales contract terms')
    values.setdefault('is_signed', 1)
    values.setdefault('signee', 'ISOLATED TEST SIGNER')
    values.setdefault('signed_on', now_datetime())
    return frappe.get_doc(values)
