"""Общий HTTP-контракт интервала записи для staff и customer.

Неверное время — ошибка запроса (422), а не отказ в правах (403).
Серверные транзакционные проверки в auto_appointment_service остаются защитой.
"""
from datetime import datetime, timezone

from pydantic import BaseModel, model_validator


class BookingInterval(BaseModel):
    startsAt: datetime
    endsAt: datetime

    @model_validator(mode="after")
    def validate_interval(self):
        start, end = self.startsAt, self.endsAt
        if (start.tzinfo is None or end.tzinfo is None
                or start.utcoffset() is None or end.utcoffset() is None):
            raise ValueError("booking timestamps must include a timezone")
        seconds = (end.astimezone(timezone.utc) -
                   start.astimezone(timezone.utc)).total_seconds()
        if not 0 < seconds <= 86400:
            raise ValueError("booking interval must be positive and at most 24 hours")
        return self
