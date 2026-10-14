"""Deterministic simulated clock, so renders are byte-identical for a seed."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

# Boot time of the simulated fleet. Fixed so tests and demos are reproducible.
SIM_EPOCH = datetime(2026, 10, 14, 9, 58, 12, tzinfo=timezone.utc)


class SimClock:
    def __init__(self, start: datetime = SIM_EPOCH):
        self.boot = start
        self.now = start

    def advance(self, seconds: float) -> datetime:
        self.now = self.now + timedelta(seconds=seconds)
        return self.now

    def uptime(self, at: datetime | None = None) -> float:
        """Seconds since boot (the kmsg / dmesg monotonic timestamp)."""
        return ((at or self.now) - self.boot).total_seconds()

    @staticmethod
    def ctime(at: datetime) -> str:
        """dmesg -T / nvidia-smi style: 'Wed Oct 14 10:59:24 2026'."""
        return at.strftime("%a %b %d %H:%M:%S %Y")

    def iso(self, at: datetime | None = None) -> str:
        return (at or self.now).strftime("%Y-%m-%dT%H:%M:%SZ")
