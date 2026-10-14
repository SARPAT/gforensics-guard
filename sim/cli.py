"""Eyeball tool for the simulator (humans only; agents use the MCP servers).

  python -m sim.cli faults
  python -m sim.cli show node-07 --inject XID79_bus_drop
  python -m sim.cli show node-04 --inject PERSISTENCE_OFF --view diag
  python -m sim.cli show node-07 --scene split_screen --view jobs
  python -m sim.cli summary --scene all
"""
from __future__ import annotations

import argparse
import json

from sim import build_fleet, telemetry
from sim.faults import FAULTS, inject, inject_scene

VIEWS = {
    "dcgm": telemetry.get_dcgm_metrics,
    "smi": telemetry.get_smi_query,
    "diag": telemetry.get_diag_result,
    "dmesg": telemetry.get_dmesg,
    "kmsg": telemetry.get_kmsg,
    "xid": telemetry.get_xid_events,
    "nvlink": telemetry.get_nvlink_status,
    "nvlink-errors": telemetry.get_nvlink_errors,
    "jobs": telemetry.get_node_jobs,
}


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="python -m sim.cli")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("faults", help="list injectable faults")
    for name in ("show", "summary"):
        sp = sub.add_parser(name)
        if name == "show":
            sp.add_argument("node")
            sp.add_argument("--view", choices=sorted(VIEWS), default=None,
                            help="one view (default: xid, dmesg tail, jobs, dcgm XID/power lines)")
        sp.add_argument("--inject", action="append", default=[], metavar="FAULT")
        sp.add_argument("--scene", default=None)
        sp.add_argument("--seed", type=int, default=7)
    a = p.parse_args(argv)

    if a.cmd == "faults":
        for f in FAULTS.values():
            target = f.default_node + ("" if f.default_gpu is None else f" gpu{f.default_gpu}")
            print(f"{f.name:<18} {target:<14} {f.summary}\n{'':<33}naive trap: {f.trap}")
        return

    fleet = build_fleet(seed=a.seed)
    if a.scene:
        inject_scene(fleet, a.scene)
    for fault in a.inject:
        inject(fleet, fault)

    if a.cmd == "summary":
        print(json.dumps(telemetry.fleet_summary(fleet), indent=2))
        return

    def emit(view: str) -> None:
        out = VIEWS[view](fleet, a.node)
        print(out if isinstance(out, str) else json.dumps(out, indent=2))

    if a.view:
        emit(a.view)
        return
    print(f"== {a.node}: Xid events (parsed from dmesg)")
    emit("xid")
    print(f"== {a.node}: dmesg (last 8 lines)")
    print("".join(telemetry.get_dmesg(fleet, a.node).splitlines(True)[-8:]), end="")
    print(f"== {a.node}: DCGM XID / power / throttle series")
    for line in telemetry.get_dcgm_metrics(fleet, a.node).splitlines():
        if line.startswith(("DCGM_FI_DEV_XID_ERRORS", "DCGM_FI_DEV_POWER_USAGE",
                            "DCGM_FI_DEV_CLOCK_THROTTLE_REASONS")):
            print(line)
    print(f"== {a.node}: jobs")
    emit("jobs")


if __name__ == "__main__":
    main()
