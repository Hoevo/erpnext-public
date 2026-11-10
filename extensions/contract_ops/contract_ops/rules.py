"""Pure validation rules; no database or framework dependency."""
from decimal import Decimal, InvalidOperation


def money(value):
    try:
        amount = Decimal(str(value or 0))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError("金额格式无效") from exc
    if not amount.is_finite():
        raise ValueError("金额必须是有限数值")
    return amount


def validate_contract(amount, start_date=None, end_date=None):
    if money(amount) <= 0:
        raise ValueError("合同含税金额必须大于零")
    if start_date and end_date and str(end_date) < str(start_date):
        raise ValueError("合同结束日期不能早于开始日期")


def validate_tax_amount(amount, tax):
    if money(amount) <= 0:
        raise ValueError("蓝字发票价税合计必须大于零")
    if money(tax) < 0 or money(tax) > money(amount):
        raise ValueError("税额应在零和价税合计之间")


def validate_limit(limit, existing, current):
    if money(existing) + money(current) > money(limit):
        raise ValueError("累计金额不能超过合同含税金额")
