"""Aritmética de meses de calendário, preservando horário e timezone."""

from calendar import monthrange
from datetime import datetime


def add_calendar_months(value: datetime, months: int) -> datetime:
    """Ajusta o dia para o último dia válido do mês de destino."""
    month_index: int = value.year * 12 + value.month - 1 + months
    year, month_zero = divmod(month_index, 12)
    month: int = month_zero + 1
    day: int = min(value.day, monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)
