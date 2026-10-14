"""Diagnoser rule table: evidence -> finding -> PROPOSED remediation steps.

🔴 This table decides what is PROPOSED, never what is ALLOWED. Every proposed step
still goes into a committed ArmorIQ plan and through invoke(); ArmorIQ's verdict
is the only thing that lets it touch the fleet.

The rules read only structured device telemetry (Xid events, dcgmi diag, DCGM,
nvidia-smi, NVLink counters). They never read job logs or kmsg userspace lines:
those are tenant-controlled text (the poisoned-log scene) and go to the narrator only.

Findings numbers (#1, #3, #6, #8, #10) refer to data/captures/README.md.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from agents.evidence import MASK_SW_POWER_CAP, VIOLATION_METRICS, NodeEvidence

TELEMETRY_MCP = "gfg-telemetry"
OPS_MCP = "gfg-ops"

# What Phase 1 reads for every node under investigation (all gfg-telemetry tools).
INVESTIGATION_READS = ("get_xid_events", "get_diag_result", "get_dcgm_metrics",
                       "get_smi_query", "get_nvlink_errors", "get_node_jobs", "get_kmsg")

SEVERITY = {"HARDWARE": 5, "MISCONFIG": 4, "INSUFFICIENT_EVIDENCE": 3, "NOT_MEASURED": 2,
            "TRANSIENT": 1, "NORMAL": 0, "HEALTHY": 0}


def step(tool: str, why: str, **params) -> dict:
    return {"tool": tool, "mcp": OPS_MCP, "params": params, "why": why}


@dataclass(frozen=True)
class Rule:
    id: str
    category: str
    finding: str                                   # e.g. "#8" from the captures README
    summary: str
    match: Callable[[NodeEvidence], list[dict]]   # -> hits (each with "evidence")
    propose: Callable[[NodeEvidence, dict], list[dict]]


# ── matchers ────────────────────────────────────────────────────────────────

def _xids(ev: NodeEvidence, code: int) -> list[dict]:
    return [e for e in ev.xid_events if e.get("xid") == code]


def _gpu_pci(ev: NodeEvidence, event: dict) -> str:
    return (event.get("pci") or "").lower()


def m_xid79_off_bus(ev):
    hits = []
    for e in _xids(ev, 79):
        gpu, pci = e.get("gpu"), _gpu_pci(ev, e)
        proof = [e.get("raw", "")]
        if ev.dcgm and "DCGM_FI_DEV_POWER_USAGE" not in ev.dcgm.get(gpu, {}):
            proof.append(f"DCGM: gpu {gpu} exports only the XID gauge, every other series is gone "
                         "(device lost, not just an old gauge value, #1)")
        if any(pci and pci in m.lower() for m in ev.smi_missing):
            proof.append(f"nvidia-smi: Unable to determine the device handle for {pci}")
        if len(proof) > 1:   # Xid line alone is not enough: the DCGM gauge keeps old Xids (#1)
            hits.append({"gpu": gpu, "evidence": proof})
    return hits


def m_xid79_unconfirmed(ev):
    if m_xid79_off_bus(ev):
        return []
    return [{"gpu": e.get("gpu"), "evidence": [e.get("raw", ""),
             "no DCGM / nvidia-smi read confirms the device is gone"]} for e in _xids(ev, 79)]


def m_ecc_dbe(ev):
    return [{"gpu": e.get("gpu"), "evidence": [e.get("raw", "")]} for e in _xids(ev, 48)]


def m_persistence_off(ev):
    if ev.xid_events:
        return []
    fails = [r for r in ev.diag if r["status"] == "Fail" and 8 in r["error_category"]]
    if not fails:
        return []
    return [{"gpus": sorted({r["gpu"] for r in fails}),
             "evidence": [f"dcgmi diag {r['test']} gpu {r['gpu']}: Fail, error_category 8 "
                          f"id {r['error_id']}: {r['warning'][:90]}" for r in fails[:2]]
             + [f"... {len(fails)} GPU(s) Fail, all category 8 (config), no Xid in dmesg"]}]


def m_nvlink_transient(ev):
    if not _xids(ev, 74):
        return []
    links = [l for l in ev.nvlink if l["link_recovery_successful"] > 0
             and l["link_recovery_failed"] == 0 and l["up"]]
    if not links or any(not l["up"] or l["link_recovery_failed"] for l in ev.nvlink):
        return []
    return [{"gpu": l["gpu"], "evidence": [
        _xids(ev, 74)[0].get("raw", ""),
        f"gpu {l['gpu']} link {l['link']}: link_recovery_successful={l['link_recovery_successful']} "
        f"failed=0, link up (#6). DCGM XID gauge stays 74 after recovery (#1)."]} for l in links]


def m_power_over_cap(ev):
    if ev.xid_events:
        return []
    over = [(pci, g) for pci, g in ev.smi.items() if g.get("power_draw_w") and g.get("power_limit_w")
            and g["power_draw_w"] > g["power_limit_w"] and g.get("sw_power_cap")]
    if not over:
        return []
    pci, g = over[0]
    return [{"gpus": len(over), "evidence": [
        f"nvidia-smi {pci}: draw {g['power_draw_w']} W > limit {g['power_limit_w']} W, "
        f"SW Power Cap Active on {len(over)} GPU(s): the cap is doing its job (#10)"]}]


def m_not_measured(ev):
    hits = []
    for gpu, m in sorted(ev.dcgm.items()):
        mask = int(m.get("DCGM_FI_DEV_CLOCK_THROTTLE_REASONS", 0))
        viol = [m.get(k) for k in VIOLATION_METRICS if k in m]
        if mask and viol and all(v == 0 for v in viol):
            hits.append({"gpu": gpu, "evidence": [
                f"gpu {gpu}: throttle mask 0x{mask:x}"
                f"{' (SW power cap)' if mask & MASK_SW_POWER_CAP else ''} but all violation "
                "counters 0: counters NOT_MEASURED here, not proof of health (#3)"]})
    if not hits:
        return []
    return [{"gpus": [h["gpu"] for h in hits], "evidence": hits[0]["evidence"]}]


# ── the table ───────────────────────────────────────────────────────────────

RULES: tuple[Rule, ...] = (
    Rule("XID79_OFF_BUS", "HARDWARE", "Xid 79",
         "GPU fell off the bus and is still gone: take the node out of service and RMA the GPU",
         m_xid79_off_bus,
         lambda ev, h: [step("cordon_node", "stop new jobs landing on the node", node=ev.node),
                        step("drain_node", "move the surviving jobs off the node", node=ev.node),
                        step("open_rma_ticket", "device lost from the bus, not recoverable in place",
                             node=ev.node, gpu=h["gpu"],
                             reason=f"Xid 79 on {ev.node} gpu {h['gpu']}: fell off the bus, not recovered")]),
    Rule("XID79_UNCONFIRMED", "INSUFFICIENT_EVIDENCE", "#1",
         "Xid 79 in dmesg but no device-level read confirms it: keep investigating",
         m_xid79_unconfirmed, lambda ev, h: []),
    Rule("ECC_DBE", "HARDWARE", "Xid 48",
         "Uncorrectable ECC error: isolate the node, reset the GPU so the pending row remap applies",
         m_ecc_dbe,
         lambda ev, h: [step("cordon_node", "stop new jobs landing on the node", node=ev.node),
                        step("drain_node", "jobs on that GPU will hit the bad row again", node=ev.node),
                        step("reset_gpu", "a GPU reset activates the pending row remap",
                             node=ev.node, gpu=h["gpu"])]),
    Rule("PERSISTENCE_OFF", "MISCONFIG", "#8",
         "dcgmi diag Fail is category 8 (config): persistence mode is off. Fix the config, do not RMA",
         m_persistence_off,
         lambda ev, h: [step("enable_persistence_mode", "the diag failure is persistence mode off",
                             node=ev.node)]),
    Rule("NVLINK_TRANSIENT", "TRANSIENT", "#6",
         "NVLink error recovered on its own: monitor, no write action",
         m_nvlink_transient, lambda ev, h: []),
    Rule("POWER_OVER_CAP", "NORMAL", "#10",
         "Power draw above the limit with SW power cap active is normal capping: no action",
         m_power_over_cap, lambda ev, h: []),
    Rule("THROTTLE_NOT_MEASURED", "NOT_MEASURED", "#3",
         "Throttling with zero violation counters: counters are not measured, stay in investigation",
         m_not_measured, lambda ev, h: []),
)


def diagnose(ev: NodeEvidence) -> dict:
    """Run every rule; return findings + the deduplicated proposed steps."""
    if not ev.reads:
        return {"node": ev.node, "category": "INSUFFICIENT_EVIDENCE",
                "findings": [{"rule": "NO_READS", "category": "INSUFFICIENT_EVIDENCE",
                              "summary": "no telemetry read returned data", "hit": {
                                  "evidence": [f"{t}: {v}" for t, v in ev.unread.items()]}}],
                "proposed_steps": []}
    findings, steps = [], []
    for rule in RULES:
        for hit in rule.match(ev):
            findings.append({"rule": rule.id, "category": rule.category, "finding": rule.finding,
                             "summary": rule.summary, "hit": hit})
            for s in rule.propose(ev, hit):
                key = (s["tool"], tuple(sorted((k, str(v)) for k, v in s["params"].items())))
                if key not in {(x["tool"], tuple(sorted((k, str(v)) for k, v in x["params"].items())))
                               for x in steps}:
                    steps.append({**s, "rule": rule.id})
    category = max((f["category"] for f in findings), key=SEVERITY.get, default="HEALTHY")
    return {"node": ev.node, "category": category, "findings": findings, "proposed_steps": steps}
