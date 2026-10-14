"""Turn raw telemetry (as returned by gfg-telemetry tools) into facts the rule table reads.

Pure parsing, no network, no decisions. Every fact keeps a pointer to the tool that
produced it, so the lineage report can show "this finding came from these reads".
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

# DCGM_FI_DEV_CLOCK_THROTTLE_REASONS bit for SW power cap (dcgm_field_reference.txt)
MASK_SW_POWER_CAP = 0x4
VIOLATION_METRICS = ("DCGM_FI_DEV_POWER_VIOLATION", "DCGM_FI_DEV_THERMAL_VIOLATION",
                     "DCGM_FI_DEV_SYNC_BOOST_VIOLATION", "DCGM_FI_DEV_BOARD_LIMIT_VIOLATION",
                     "DCGM_FI_DEV_LOW_UTIL_VIOLATION", "DCGM_FI_DEV_RELIABILITY_VIOLATION")

_PROM = re.compile(r'^(\w+)\{([^}]*)\}\s+(\S+)$')
_GPU_LABEL = re.compile(r'gpu="(\d+)"')


@dataclass
class NodeEvidence:
    node: str
    xid_events: list[dict] = field(default_factory=list)           # get_xid_events
    diag: list[dict] = field(default_factory=list)                 # get_diag_result
    dcgm: dict[int, dict[str, float]] = field(default_factory=dict)  # get_dcgm_metrics
    smi: dict[str, dict] = field(default_factory=dict)             # get_smi_query, by PCI
    smi_missing: list[str] = field(default_factory=list)           # "Unable to determine the device handle"
    nvlink: list[dict] = field(default_factory=list)               # get_nvlink_errors["links"]
    jobs: list[dict] = field(default_factory=list)                 # get_node_jobs
    kmsg: str = ""                                                  # get_kmsg (raw, may be tenant text)
    reads: list[str] = field(default_factory=list)                 # tools that returned data
    unread: dict[str, str] = field(default_factory=dict)           # tool -> verdict/err when no data


def parse_dcgm(text: str) -> dict[int, dict[str, float]]:
    out: dict[int, dict[str, float]] = {}
    for line in text.splitlines():
        m = _PROM.match(line.strip())
        if not m:
            continue
        g = _GPU_LABEL.search(m.group(2))
        if not g:
            continue
        try:
            out.setdefault(int(g.group(1)), {})[m.group(1)] = float(m.group(3))
        except ValueError:
            pass
    return out


def parse_diag(text: str) -> list[dict]:
    """dcgmi diag -j output -> flat list of per-GPU results with their warnings."""
    try:
        doc = json.loads(text)
    except (TypeError, ValueError):
        return []
    rows = []
    for cat in doc.get("DCGM Diagnostic", {}).get("test_categories", []):
        for test in cat.get("tests", []):
            for r in test.get("results", []):
                warns = r.get("warnings") or []
                rows.append({"test": test.get("name"), "gpu": r.get("entity_id"),
                             "status": r.get("status"),
                             "error_category": [w.get("error_category") for w in warns],
                             "error_id": [w.get("error_id") for w in warns],
                             "warning": " | ".join(w.get("warning", "") for w in warns)})
    return rows


def parse_smi(text: str) -> tuple[dict[str, dict], list[str]]:
    """nvidia-smi -q -> {pci: {power_draw_w, power_limit_w, sw_power_cap}}, [unreachable pci]."""
    gpus: dict[str, dict] = {}
    missing: list[str] = []
    cur: dict | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("Unable to determine the device handle for GPU"):
            # "...handle for GPU0000:47:00.0: Unknown Error" -> "0000:47:00.0"
            missing.append(line[len("Unable to determine the device handle for GPU"):].split(": ")[0])
            cur = None
            continue
        if re.match(r"^GPU [0-9A-Fa-f]{8}:", line):
            cur = gpus.setdefault(line[4:].lower(), {})
            continue
        if cur is None or ":" not in line:
            continue
        key, val = (s.strip() for s in line.split(":", 1))
        if key == "Instantaneous Power Draw" and "power_draw_w" not in cur:
            cur["power_draw_w"] = _watts(val)
        elif key == "Current Power Limit" and "power_limit_w" not in cur:
            cur["power_limit_w"] = _watts(val)
        elif key == "SW Power Cap" and "sw_power_cap" not in cur:
            cur["sw_power_cap"] = val == "Active"
    return gpus, missing


def _watts(val: str) -> float | None:
    try:
        return float(val.split()[0])
    except (ValueError, IndexError):
        return None
