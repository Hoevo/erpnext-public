"""Pure date rules. All dates are site-local calendar dates, never UTC timestamps."""
from calendar import monthrange
from datetime import date, timedelta

MODES = ('待核实', '到期协商续签', '自动续期', '到期终止', '无固定期限')
OPEN = ('待处理', '洽谈中')
OUTCOMES = ('洽谈中', '已签新合同', '已确认按条款续期', '已发出不续签通知', '已终止')


def as_date(value):
    return value if isinstance(value, date) else date.fromisoformat(str(value))


def offsets(value):
    try:
        parts = str(value or '').replace('，', ',').split(',')
        result = sorted({int(x.strip()) for x in parts}, reverse=True)
        if not result or any(x < 0 or x > 3660 for x in result):
            raise ValueError
        return result
    except (ValueError, TypeError):
        raise ValueError('提醒提前量须为逗号分隔的非负整数，最大3660天') from None


def deadline(mode, end, notice_days=0, review=None):
    if mode == '无固定期限':
        if not review:
            raise ValueError('无固定期限合同必须填写复核日期')
        return as_date(review)
    if mode not in MODES or mode == '待核实' or not end:
        raise ValueError('请核实续签方式并填写履行结束日期')
    return as_date(end) - timedelta(days=int(notice_days or 0))


def next_period(end, months):
    months = int(months)
    if months <= 0 or months > 1200:
        raise ValueError('自动续期月数必须在1至1200之间')
    start = as_date(end) + timedelta(days=1)
    index = start.year * 12 + start.month - 1 + months
    year, month = divmod(index, 12)
    month += 1
    anniversary = date(year, month, min(start.day, monthrange(year, month)[1]))
    return start, anniversary - timedelta(days=1)


def due_stage(today, due, leads):
    """Catch up once at the most urgent stage; weekly overdue buckets are stable."""
    today, due = as_date(today), as_date(due)
    remaining = (due - today).days
    if remaining < 0:
        return f'overdue:{(-remaining - 1) // 7}'
    eligible = [n for n in offsets(leads) if remaining <= n]
    return f'before:{min(eligible)}' if eligible else None
