"""CP4: the declared policies cover every tool the MCP servers serve.

The platform's fallback is BLOCK (CP0 finding 3), so a served tool with no explicit rule
would silently be unusable. These tests check the declaration only; enforcement is ArmorIQ's."""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
from policy_check import ROOT, diff, load  # noqa: E402
from tests.test_mcp_servers import OPS_TOOLS, TELEMETRY_TOOLS  # noqa: E402

TEL, OPS = load(ROOT / "gfg-telemetry-read.yaml"), load(ROOT / "gfg-ops-remediate.yaml")


def test_every_served_tool_has_a_rule():
    assert TELEMETRY_TOOLS <= set(TEL["rules"])
    assert OPS_TOOLS == set(OPS["rules"])


def test_reads_allowed_writes_tiered_default_block():
    assert all(TEL["rules"][t] == "allow" for t in TELEMETRY_TOOLS)
    assert TEL["default_action"] == OPS["default_action"] == "block"
    assert {t for t, a in OPS["rules"].items() if a == "hold"} == {"open_rma_ticket", "reset_gpu"}
    assert OPS["rules"]["drain_nodes"] == "block"


def test_same_priority_cp3_finding_4():
    assert TEL["priority"] == OPS["priority"]


def test_diff_reports_drift():
    live = {**OPS, "rules": {**OPS["rules"], "drain_nodes": "allow"}}
    assert diff(OPS, live) == ["rule drain_nodes: repo=block live=allow"]
