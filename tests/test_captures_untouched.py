"""data/captures/ is read-only reference data (CLAUDE.md §4).

tests/captures_manifest.json records the sha256 of every file as copied from
Gforensics @ 4eb6bcd. Any edit, deletion or new file under data/captures/ fails here.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from conftest import REPO_ROOT

CAPTURES_ROOT = REPO_ROOT / "data" / "captures"
MANIFEST = Path(__file__).with_name("captures_manifest.json")


def test_captures_match_manifest():
    expected = json.loads(MANIFEST.read_text())
    actual = {p.relative_to(CAPTURES_ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in sorted(CAPTURES_ROOT.rglob("*")) if p.is_file()}
    assert sorted(actual) == sorted(expected), "files added to or removed from data/captures/"
    changed = [name for name in expected if actual[name] != expected[name]]
    assert changed == [], f"capture files modified: {changed}"
