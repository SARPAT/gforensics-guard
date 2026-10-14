"""Read-only access to the reference captures in data/captures/scrubbed/.

The renderers use these files as format templates at runtime. Nothing here
writes to data/captures/ (CLAUDE.md: read-only, never edit).
"""
from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

CAPTURES_DIR = Path(__file__).resolve().parent.parent / "data" / "captures" / "scrubbed"


def read(name: str) -> str:
    return (CAPTURES_DIR / name).read_text()


@dataclass(frozen=True)
class MetricDef:
    name: str
    help: str
    type: str


@lru_cache(maxsize=None)
def dcgm_metric_defs() -> tuple[MetricDef, ...]:
    """Metric names, HELP text and TYPE, in the order dcgm-exporter emitted them."""
    defs, help_text = [], {}
    for line in read("dcgm_exporter_metrics.txt").splitlines():
        m = re.match(r"# HELP (\S+) (.*)", line)
        if m:
            help_text[m.group(1)] = m.group(2)
            continue
        m = re.match(r"# TYPE (\S+) (\S+)", line)
        if m:
            defs.append(MetricDef(m.group(1), help_text[m.group(1)], m.group(2)))
    return tuple(defs)


@lru_cache(maxsize=None)
def dcgm_label_keys() -> tuple[str, ...]:
    """Label keys of an ordinary series in the capture (XID adds err_code/err_msg)."""
    for line in read("dcgm_exporter_metrics.txt").splitlines():
        if line.startswith("DCGM_FI_DEV_SM_CLOCK{"):
            return tuple(re.findall(r'(\w+)="', line))
    raise RuntimeError("capture changed: no DCGM_FI_DEV_SM_CLOCK series")


@lru_cache(maxsize=None)
def diag_template() -> dict:
    return json.loads(read("dcgmi_diag_r1.json"))


@lru_cache(maxsize=None)
def smi_q_template() -> str:
    return read("nvidia_smi_q.txt")


@dataclass(frozen=True)
class TraceSample:
    temp_c: int
    power_w: float
    sm_mhz: int
    mask: int


@lru_cache(maxsize=None)
def throttle_trace() -> tuple[TraceSample, ...]:
    """The real 4-minute load test (smi_throttle_trace.csv), idle -> SwPowerCap -> idle."""
    rows = []
    with open(CAPTURES_DIR / "smi_throttle_trace.csv", newline="") as f:
        reader = csv.reader(f, skipinitialspace=True)
        next(reader)
        for r in reader:
            if len(r) < 6:
                continue
            rows.append(TraceSample(int(r[2]), float(r[3].split()[0]),
                                    int(r[4].split()[0]), int(r[5], 16)))
    return tuple(rows)


@lru_cache(maxsize=None)
def dmesg_boot_lines() -> tuple[str, ...]:
    """Driver-load lines from dmesg_nvrm.txt, with their '[timestamp] ' prefix stripped."""
    out = []
    for line in read("dmesg_nvrm.txt").splitlines():
        out.append(re.sub(r"^\[[^\]]+\] ", "", line))
    return tuple(out)
