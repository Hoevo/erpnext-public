"""Version 1 plan calculations: currency cents, FIFO allocation and evidenced calendars."""
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP


def money(value):
    result = Decimal(str(value or 0))
    if not result.is_finite():
        raise ValueError('金额必须是有限数值')
    return result.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def allocate_fifo(total, amounts):
    remaining = max(money(total), Decimal('0'))
    result = []
    for amount in amounts:
        allocation = min(remaining, max(money(amount), Decimal('0')))
        result.append(allocation)
        remaining -= allocation
    return result


def business_due_date(start, days, holidays, calendar_start, calendar_end):
    if days < 0 or not calendar_start <= start <= calendar_end:
        raise ValueError('工作日历不覆盖收票日期，或间隔天数无效')
    current = start
    for _ in range(days):
        current += timedelta(days=1)
        while current in holidays:
            current += timedelta(days=1)
        if current > calendar_end:
            raise ValueError('工作日历未覆盖预计到期日，请先补充日历')
    return current
