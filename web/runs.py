"""CP5: run manager behind the web UI.

A run = one scene, two sides:
    governed  Gforensics Guard through ArmorIQ (agents.orchestrator.run), LIVE, or a REPLAY
              of a recorded real run (web/recorded/*.json, captured on the laptop by
              scripts/demo.py; every verdict in it is what ArmorIQ returned at the time)
    naive     the same LLM + the same fleet operations, NO ArmorIQ, on its own private
              simulated fleet (agents.naive.run_naive)

🔴 This module never creates a verdict. Governed events are passed through exactly as the
orchestrator recorded them (decision/source/reason come from armoriq_io). The only edits
are privacy scrubs (email addresses) before anything leaves the server.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

import sim
from sim import faults, ops, telemetry
from sim.fleet import Fleet

RECORDED_DIR = pathlib.Path(__file__).parent / "recorded"
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
WRITE_TOOLS = ("cordon_node", "uncordon_node", "drain_node", "drain_nodes", "reset_gpu",
               "enable_persistence_mode", "open_rma_ticket")
REPLAY_MAX_GAP_S = 1.2        # replays keep the real order, but compress long LLM waits
KEEP_RUNS = 30


def scrub(obj: Any) -> Any:
    """Hide email addresses (requester / approver) in anything we send to a browser."""
    if isinstance(obj, str):
        return EMAIL.sub("[email hidden]", obj)
    if isinstance(obj, dict):
        return {k: ("on-call SRE" if k == "requester" and v else scrub(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [scrub(v) for v in obj]
    return obj


def live_available() -> bool:
    return bool(os.getenv("ARMORIQ_API_KEY"))


@dataclass
class Run:
    id: str
    scene: str
    mode: str                       # live | replay
    compromised: bool
    with_naive: bool
    recorded: dict | None = None    # replay: {"file": ..., "recorded_at": ...}
    started: float = field(default_factory=time.time)
    status: str = "running"         # running | done | error
    error: str | None = None
    gov_events: list[dict] = field(default_factory=list)
    gov_fleet: dict | None = None
    gov_done: bool = False
    outcome: str = ""
    naive_events: list[dict] = field(default_factory=list)
    naive_fleet: dict | None = None
    naive_done: bool = False
    naive_outcome: str = ""

    def public(self) -> dict:
        d = asdict(self)
        return scrub(d)

    @property
    def finished(self) -> bool:
        return self.gov_done and (self.naive_done or not self.with_naive)


def _fresh_fleet(sim_scene: str) -> Fleet:
    f = Fleet()
    faults.inject_scene(f, sim_scene)
    return f


def recorded_runs() -> list[dict]:
    out = []
    for p in sorted(RECORDED_DIR.glob("*.json")):
        d = json.loads(p.read_text())
        out.append({"id": p.stem, "scene": d.get("scene", "").replace("-compromised", ""),
                    "compromised": d.get("scene", "").endswith("-compromised"),
                    "recorded_at": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(d["started"])),
                    "outcome": d.get("outcome", "")})
    return out


class RunManager:
    def __init__(self):
        self.runs: dict[str, Run] = {}
        self.live_lock = threading.Lock()     # one live run at a time: the governed fleet is shared

    # ── public API ─────────────────────────────────────────────────────────
    def start(self, scene: str, mode: str, compromised: bool = False, with_naive: bool = True,
              recorded_id: str | None = None) -> Run:
        from agents.scenes import SCENES
        if scene not in SCENES:
            raise ValueError(f"unknown scene {scene!r}")
        if mode == "live":
            if not live_available():
                raise PermissionError("live runs need ARMORIQ_API_KEY on the server; use a replay")
            if not self.live_lock.acquire(blocking=False):
                raise RuntimeError("another live run is in progress; try again when it finishes")
        rec = None
        if mode == "replay":
            rec = self._pick_recording(scene, compromised, recorded_id)
        run = Run(uuid.uuid4().hex[:10], scene, mode, compromised, with_naive,
                  recorded={"file": rec[0].name, "recorded_at": rec[2]} if rec else None)
        self.runs[run.id] = run
        self._trim()
        target = self._live if mode == "live" else self._replay
        threading.Thread(target=self._guard, args=(run, target, rec), daemon=True).start()
        if with_naive:
            threading.Thread(target=self._naive, args=(run,), daemon=True).start()
        else:
            run.naive_done = True
        return run

    def get(self, run_id: str) -> Run | None:
        return self.runs.get(run_id)

    # ── governed side ──────────────────────────────────────────────────────
    def _guard(self, run: Run, target, rec) -> None:
        try:
            target(run, rec)
        except Exception as e:  # noqa: BLE001  (shown as an error, never as a verdict)
            run.status, run.error = "error", f"{type(e).__name__}: {str(e)[:300]}"
        finally:
            run.gov_done = True
            if run.mode == "live":
                self.live_lock.release()
            self._maybe_done(run)

    def _live(self, run: Run, _rec) -> None:
        from agents.armoriq_io import ArmorIQGateway
        from agents.orchestrator import Incident, run as orchestrate
        from agents.scenes import SCENES
        sc = SCENES[run.scene]
        fleet_ctl = FleetControl()
        run.gov_fleet = fleet_ctl.set_scene(sc.sim_scene)
        email = os.getenv("GFG_REQUESTER_EMAIL") or None
        inc = Incident(sc.objective, sc.nodes, requester=email, approval_wait_s=0,
                       use_llm=bool(os.getenv("LLM_API_KEY")),
                       injected_steps=sc.injected if run.compromised else [],
                       telemetry_mcp=os.getenv("GFG_TELEMETRY_MCP", "gfg-telemetry"),
                       ops_mcp=os.getenv("GFG_OPS_MCP", "gfg-ops"))

        def on_event(e: dict) -> None:
            run.gov_events.append(scrub(e))
            if e.get("kind") == "call" and e.get("tool") in WRITE_TOOLS:
                run.gov_fleet = fleet_ctl.summary()

        lin = orchestrate(ArmorIQGateway(user_email=email), inc, on_event=on_event)
        run.outcome = lin.outcome
        run.gov_fleet = fleet_ctl.summary()

    def _pick_recording(self, scene: str, compromised: bool, recorded_id: str | None):
        if recorded_id:
            p = RECORDED_DIR / f"{pathlib.Path(recorded_id).name}.json"
            if not p.exists():
                raise ValueError(f"no recording {recorded_id!r}")
        else:
            label = scene + ("-compromised" if compromised else "")
            cands = [p for p in sorted(RECORDED_DIR.glob("*.json"))
                     if json.loads(p.read_text()).get("scene") == label]
            if not cands:
                raise ValueError(f"no recorded run for {label}")
            p = cands[-1]
        d = json.loads(p.read_text())
        return p, d, time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(d["started"]))

    def _replay(self, run: Run, rec) -> None:
        """Re-play a recorded real run in its original order. The fleet map is rebuilt by
        applying the recorded fleet audit log (what actually reached the fleet) to a fresh
        simulated fleet, at the moment the matching ALLOWED call appears."""
        from agents.scenes import SCENES
        _, d, _ = rec
        fleet = _fresh_fleet(SCENES[run.scene].sim_scene)
        run.gov_fleet = telemetry.fleet_summary(fleet)
        audit = list(d.get("fleet_audit", []))
        prev_t = 0.0
        for e in d["events"]:
            time.sleep(min(max(e.get("t", 0) - prev_t, 0), REPLAY_MAX_GAP_S))
            prev_t = e.get("t", prev_t)
            run.gov_events.append(scrub(e))
            if (e["kind"] == "call" and e.get("decision") == "ALLOWED" and e.get("tool") in WRITE_TOOLS
                    and audit and audit[0]["tool"] == e["tool"]):
                a = audit.pop(0)
                _apply(fleet, a["tool"], a.get("params", {}), a.get("caller", "remediator"))
            run.gov_fleet = telemetry.fleet_summary(fleet)
        run.outcome = d.get("outcome", "")

    # ── naive side ─────────────────────────────────────────────────────────
    def _naive(self, run: Run) -> None:
        try:
            from agents.naive import run_naive

            def on_event(e: dict) -> None:
                run.naive_events.append(scrub(e))

            res = run_naive(run.scene, use_llm=bool(os.getenv("LLM_API_KEY")), on_event=on_event,
                            compromised=run.compromised)
            run.naive_fleet, run.naive_outcome = res["fleet"], res["outcome"]
        except Exception as e:  # noqa: BLE001
            run.naive_events.append({"kind": "note", "t": 0, "text": f"naive agent crashed: {type(e).__name__}: {e}"})
        finally:
            run.naive_done = True
            self._maybe_done(run)

    def _maybe_done(self, run: Run) -> None:
        if run.finished and run.status == "running":
            run.status = "done"

    def _trim(self) -> None:
        for rid in sorted(self.runs, key=lambda r: self.runs[r].started)[:-KEEP_RUNS]:
            if self.runs[rid].finished:
                del self.runs[rid]


class FleetControl:
    """The fleet the MCP servers serve. On Render the UI and the MCP servers share one process,
    so it is sim.get_fleet(). When the UI runs elsewhere (laptop test), GFG_FLEET_URL points at
    the deployed service and the scene is set through its /sim endpoints."""

    def __init__(self):
        self.url = (os.getenv("GFG_FLEET_URL") or "").rstrip("/")
        tok = os.getenv("GFG_ADMIN_TOKEN")
        self.headers = {"X-Admin-Token": tok} if tok else {}

    def set_scene(self, sim_scene: str) -> dict:
        if not self.url:
            fleet = sim.reset_fleet()
            faults.inject_scene(fleet, sim_scene)
            return telemetry.fleet_summary(fleet)
        import httpx
        r = httpx.post(f"{self.url}/sim/scene/{sim_scene}", headers=self.headers, timeout=90)
        r.raise_for_status()
        return r.json()["summary"]

    def summary(self) -> dict:
        if not self.url:
            return telemetry.fleet_summary(sim.get_fleet())
        import httpx
        return httpx.get(f"{self.url}/sim/state", timeout=60).json()["summary"]


def _apply(fleet: Fleet, tool: str, params: dict, caller: str) -> None:
    fn = getattr(ops, tool)
    kw = {k: v for k, v in params.items() if k in fn.__code__.co_varnames}
    try:
        fn(fleet, caller=caller, **kw)
    except Exception:  # noqa: BLE001  (a replay must never crash on an old audit entry)
        pass


MANAGER = RunManager()
