"""Diagnoser: reads telemetry through its delegated (read-only) token, then runs the rule table.

`call(tool, params) -> Verdict` is injected by the orchestrator; it is always an
ArmorIQ invoke() with the Diagnoser's delegate_subtree() token. A read that is not
ALLOWED simply yields no evidence; the rule table then says INSUFFICIENT_EVIDENCE.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Callable

from agents.armoriq_io import Verdict
from agents.evidence import NodeEvidence, parse_dcgm, parse_diag, parse_smi
from agents.rules import INVESTIGATION_READS, diagnose


def collect(node: str, call: Callable[[str, dict], Verdict]) -> tuple[NodeEvidence, list[Verdict]]:
    ev, verdicts = NodeEvidence(node=node), []
    for tool in INVESTIGATION_READS:
        v = call(tool, {"node": node})
        verdicts.append(v)
        if v.decision != "ALLOWED" or v.data is None or v.reason:
            ev.unread[tool] = f"{v.decision} [{v.source}] {v.reason[:120]}"
            continue
        ev.reads.append(tool)
        d = v.data
        if tool == "get_xid_events":
            ev.xid_events = d if isinstance(d, list) else []
        elif tool == "get_diag_result":
            ev.diag = parse_diag(d)
        elif tool == "get_dcgm_metrics":
            ev.dcgm = parse_dcgm(d) if isinstance(d, str) else {}
        elif tool == "get_smi_query":
            ev.smi, ev.smi_missing = parse_smi(d) if isinstance(d, str) else ({}, [])
        elif tool == "get_nvlink_errors":
            ev.nvlink = (d or {}).get("links", []) if isinstance(d, dict) else []
        elif tool == "get_node_jobs":
            ev.jobs = d if isinstance(d, list) else []
        elif tool == "get_kmsg":
            ev.kmsg = d if isinstance(d, str) else ""
    return ev, verdicts


def investigate(node: str, call: Callable[[str, dict], Verdict]) -> dict:
    ev, verdicts = collect(node, call)
    return {"evidence": asdict(ev), "diagnosis": diagnose(ev), "reads": verdicts}
