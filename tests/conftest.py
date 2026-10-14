"""Shared fixtures for the fleet-simulator tests.

Every test gets its OWN freshly built fleet (seed 7, 16 nodes x 8 GPUs), so
faults and ops in one test can never leak into another.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import sim

REPO_ROOT = Path(__file__).resolve().parent.parent
CAPTURES = REPO_ROOT / "data" / "captures" / "scrubbed"


@pytest.fixture
def fleet():
    """A healthy, seeded baseline fleet, new for every test."""
    return sim.build_fleet()


def read_capture(name: str) -> str:
    """Read a reference capture (read-only; never written by tests)."""
    return (CAPTURES / name).read_text()
