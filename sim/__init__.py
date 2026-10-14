"""Gforensics Guard fleet simulator (CP1).

A seeded, in-memory GPU fleet (default 16 nodes x 8 A100s) that renders
telemetry in the exact text formats captured in data/captures/scrubbed/,
plus a fault injector and the fleet-ops state that CP2's MCP servers wrap.

Everything here is SIMULATED. Nothing touches real hardware (hackathon rule 7).
The simulator never decides whether an action is allowed: that is ArmorIQ's job.
"""
from sim.fleet import Fleet, build_fleet, get_fleet, reset_fleet

__all__ = ["Fleet", "build_fleet", "get_fleet", "reset_fleet"]
