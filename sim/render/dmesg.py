"""Kernel log renderers: `dmesg -T` text and `/dev/kmsg` records.

Templates: dmesg_nvrm.txt ("[Wed Sep 16 10:59:24 2026] msg") and
kmsg_sample.txt ("pri,seq,usec,-;msg"). dmesg shows the kernel facility only;
/dev/kmsg also carries userspace lines (facility 1), which is where tenant
processes can write, and where the POISONED_LOG fault lands.
"""
from __future__ import annotations

import re

from sim.clock import SimClock
from sim.model import Node

XID_RE = re.compile(r"NVRM: Xid \(PCI:(?P<pci>[0-9a-fA-F:]+)\): (?P<code>\d+), (?P<rest>.*)$")
DMESG_RE = re.compile(r"^\[(?P<ts>[^\]]+)\] (?P<msg>.*)$")


def render_dmesg(node: Node, clock: SimClock) -> str:
    return "".join(f"[{clock.ctime(r.at)}] {r.message}\n" for r in node.kmsg if r.priority < 8)


def render_kmsg(node: Node, clock: SimClock) -> str:
    return "".join(f"{r.priority},{seq},{int(clock.uptime(r.at) * 1_000_000)},-;{r.message}\n"
                   for seq, r in enumerate(node.kmsg))


def parse_xid_events(dmesg_text: str) -> list[dict]:
    """Parse Xid lines out of dmesg text (dmesg is the authoritative Xid source, Finding #1)."""
    events = []
    for line in dmesg_text.splitlines():
        m = DMESG_RE.match(line)
        x = XID_RE.search(m.group("msg")) if m else None
        if x:
            events.append({"time": m.group("ts"), "pci": x.group("pci").lower(),
                           "xid": int(x.group("code")), "detail": x.group("rest"), "raw": line})
    return events


def decode_packed_gpu_id(packed: str) -> str:
    """'0x00004700' -> '0000:47:00.0' (Finding #9)."""
    v = int(packed, 16)
    return f"0000:{(v >> 8) & 0xff:02x}:{(v >> 3) & 0x1f:02x}.{v & 0x7}"
