"""CP5: web UI routes, mounted into the same FastAPI app as the MCP servers (one Render service).

    GET  /                      incident console (one run, timeline + fleet map)
    GET  /split                 split screen: naive agent (no ArmorIQ) vs Gforensics Guard
    GET  /report/{run_id}       intent-lineage report for a run
    GET  /api/scenes            scenes + recorded runs + whether live runs are available
    POST /api/runs              start a run {scene, mode: live|replay, compromised, naive, recorded_id}
    GET  /api/runs/{id}         full run state (polling)
    GET  /api/runs/{id}/stream  Server-Sent Events: each new event as it happens
    GET  /api/approval/{id}     live status of an ArmorIQ approval request (as ArmorIQ reports it)
"""
from __future__ import annotations

import asyncio
import json
import os
import pathlib
import re

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from web.runs import MANAGER, BudgetError, live_available, recorded_runs, scrub

STATIC = pathlib.Path(__file__).parent / "static"
router = APIRouter()
static_app = StaticFiles(directory=STATIC)


class RunRequest(BaseModel):
    scene: str
    mode: str = "replay"
    compromised: bool = False
    naive: bool = True
    recorded_id: str | None = None


@router.get("/", include_in_schema=False)
def console():
    return FileResponse(STATIC / "index.html")


@router.get("/split", include_in_schema=False)
def split():
    return FileResponse(STATIC / "split.html")


@router.get("/report/{run_id}", include_in_schema=False)
def report(run_id: str):
    return FileResponse(STATIC / "report.html")


@router.get("/api/scenes")
def scenes() -> dict:
    from agents.scenes import DEMO_ORDER, SCENES
    order = DEMO_ORDER + [s for s in SCENES if s not in DEMO_ORDER]
    return {"live_available": live_available(), "live_budget": MANAGER.live_budget(),
            "llm_available": bool(os.getenv("LLM_API_KEY")),
            "scenes": [{"name": s, "objective": SCENES[s].objective, "nodes": SCENES[s].nodes,
                        "story": SCENES[s].story, "has_compromised": bool(SCENES[s].injected)}
                       for s in order],
            "recorded": recorded_runs()}


@router.post("/api/runs")
def start_run(req: RunRequest) -> dict:
    if req.mode not in ("live", "replay"):
        raise HTTPException(400, "mode must be live or replay")
    try:
        run = MANAGER.start(req.scene, req.mode, req.compromised, req.naive, req.recorded_id)
    except ValueError as e:
        raise HTTPException(404, str(e)) from None
    except PermissionError as e:
        raise HTTPException(403, str(e)) from None
    except BudgetError as e:
        raise HTTPException(429, str(e)) from None
    except RuntimeError as e:
        raise HTTPException(409, str(e)) from None
    return {"id": run.id}


@router.get("/api/runs/{run_id}")
def get_run(run_id: str) -> dict:
    run = MANAGER.get(run_id)
    if not run:
        raise HTTPException(404, "unknown run (the server may have restarted)")
    return run.public()


@router.get("/api/runs/{run_id}/stream")
async def stream(run_id: str):
    run = MANAGER.get(run_id)
    if not run:
        raise HTTPException(404, "unknown run")

    async def gen():
        gi = ni = 0
        last_fleet = None
        yield _sse({"type": "meta", "run": {k: v for k, v in run.public().items()
                                             if k not in ("gov_events", "naive_events")}})
        while True:
            done = run.finished
            while gi < len(run.gov_events):
                yield _sse({"type": "event", "side": "governed", "event": run.gov_events[gi]})
                gi += 1
            while ni < len(run.naive_events):
                yield _sse({"type": "event", "side": "naive", "event": run.naive_events[ni]})
                ni += 1
            fleets = {"governed": run.gov_fleet, "naive": run.naive_fleet}
            if fleets != last_fleet:
                last_fleet = json.loads(json.dumps(fleets))
                yield _sse({"type": "fleet", **fleets})
            if done:
                yield _sse({"type": "done", "status": run.status, "error": scrub(run.error),
                            "outcome": scrub(run.outcome), "naive_outcome": run.naive_outcome})
                return
            await asyncio.sleep(0.25)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/api/approval/{delegation_id}")
def approval(delegation_id: str) -> dict:
    """What ArmorIQ says about a held step's approval request right now (read-only)."""
    if not re.fullmatch(r"[0-9a-fA-F-]{8,64}", delegation_id):
        raise HTTPException(400, "bad id")
    if not live_available():
        raise HTTPException(403, "needs ARMORIQ_API_KEY on the server")
    from agents.armoriq_io import ArmorIQGateway
    try:
        status = ArmorIQGateway().approval_status(delegation_id)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"ArmorIQ status lookup failed: {type(e).__name__}") from None
    return {"delegation_id": delegation_id, "status": status, "source": "ArmorIQ get_delegation_status"}


def _sse(obj: dict) -> str:
    return f"data: {json.dumps(obj, default=str)}\n\n"
