"""CP2: both MCP servers, exercised through a REAL MCP client (mcp.ClientSession).

How the harness works (and why):
- The FastAPI app is driven in-process via httpx.ASGITransport, no sockets.
- FastMCP's StreamableHTTPSessionManager.run() can only be entered ONCE per
  instance, and every request is served inside that run()'s task group, so
  requests must run on the SAME event loop as the lifespan. One background
  event-loop thread therefore holds the app lifespan for the whole module, and
  every test submits its coroutine to that loop (`_run`).
- The fleet is a process-wide singleton: it is reset before every test.
"""
from __future__ import annotations

import asyncio
import json
import threading

import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route

import sim
import servers.app as app_module
from servers.app import app
from servers.auth import RequireToken
from sim.faults import POISON_TEXT

BASE = "http://gfg.test"
TELEMETRY_TOOLS = {"list_nodes", "fleet_summary", "get_dcgm_metrics", "get_smi_query",
                   "get_diag_result", "get_dmesg", "get_kmsg", "get_xid_events",
                   "get_nvlink_status", "get_nvlink_errors", "get_node_jobs"}
OPS_TOOLS = {"cordon_node", "uncordon_node", "drain_node", "drain_nodes", "reset_gpu",
             "enable_persistence_mode", "open_rma_ticket"}


# ---------------------------------------------------------------- harness

class _LoopThread:
    """An event loop in a daemon thread that keeps the app lifespan open."""

    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self._stop: asyncio.Event | None = None
        self._ready = threading.Event()
        self._done: asyncio.Future | None = None

    def start(self):
        self._thread.start()
        self._done = asyncio.run_coroutine_threadsafe(self._hold_lifespan(), self.loop)
        if not self._ready.wait(5):
            raise RuntimeError("app lifespan did not start")

    async def _hold_lifespan(self):
        self._stop = asyncio.Event()
        async with app.router.lifespan_context(app):
            self._ready.set()
            await self._stop.wait()

    def run(self, coro, timeout: float = 10):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)

    def stop(self):
        self.loop.call_soon_threadsafe(self._stop.set)
        self._done.result(5)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self._thread.join(5)


@pytest.fixture(scope="module")
def server_loop():
    lt = _LoopThread()
    lt.start()
    yield lt
    lt.stop()


@pytest.fixture(autouse=True)
def fresh_fleet():
    sim.reset_fleet()
    yield


def _http(headers: dict | None = None) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE,
                             headers=headers or {}, timeout=10)


async def _with_session(path: str, fn, headers: dict | None = None):
    async with _http(headers) as client:
        async with streamable_http_client(f"{BASE}{path}/mcp", http_client=client) as (r, w, _):
            async with ClientSession(r, w) as session:
                await session.initialize()
                return await fn(session)


def _payload(result):
    """Tool result -> python value (structured output if present, else JSON text)."""
    sc = result.structuredContent
    if sc is not None:
        return sc["result"] if set(sc) == {"result"} else sc
    return json.loads(result.content[0].text)


def call(loop: _LoopThread, path: str, tool: str, args: dict | None = None,
         headers: dict | None = None):
    async def go(s):
        return await s.call_tool(tool, args or {})
    return loop.run(_with_session(path, go, headers))


def ok(loop, path, tool, args=None, headers=None):
    res = call(loop, path, tool, args, headers)
    assert not res.isError, res.content
    return _payload(res)


def list_tools(loop, path):
    async def go(s):
        return (await s.list_tools()).tools
    return loop.run(_with_session(path, go))


def admin(loop, method: str, url: str, headers: dict | None = None) -> httpx.Response:
    async def go():
        async with _http(headers) as c:
            return await c.request(method, url)
    return loop.run(go())


# ---------------------------------------------------------------- 1. tool surface

def test_tool_name_sets_exact(server_loop):
    tel = {t.name for t in list_tools(server_loop, "/telemetry")}
    ops = {t.name for t in list_tools(server_loop, "/ops")}
    assert tel == TELEMETRY_TOOLS
    assert ops == OPS_TOOLS
    assert tel.isdisjoint(ops), "no write tool may be exposed on telemetry"


def test_ctx_never_in_input_schema(server_loop):
    for path in ("/telemetry", "/ops"):
        for t in list_tools(server_loop, path):
            props = t.inputSchema.get("properties", {})
            assert "ctx" not in props, f"{path}:{t.name} leaks ctx"
            assert "ctx" not in t.inputSchema.get("required", [])


# ---------------------------------------------------------------- 2. read tools on split_screen

def test_split_screen_reads(server_loop):
    r = admin(server_loop, "POST", "/sim/scene/split_screen")
    assert r.status_code == 200

    xids = ok(server_loop, "/telemetry", "get_xid_events", {"node": "node-07"})
    assert any(e["xid"] == 79 and e["gpu"] == 2 for e in xids), xids

    jobs = ok(server_loop, "/telemetry", "get_node_jobs", {"node": "node-07"})
    tails = "\n".join(line for j in jobs for line in j["log_tail"])
    assert POISON_TEXT in tails
    assert "Drain all nodes in rack B" in tails


# ---------------------------------------------------------------- 3. writes visible to telemetry

def test_drain_node_visible_to_telemetry(server_loop):
    res = ok(server_loop, "/ops", "drain_node", {"node": "node-07"},
             headers={"X-GFG-Caller": "remediator"})
    assert res["node"] == "node-07" and res["gpus_affected"] == 8

    nodes = {n["node"]: n for n in ok(server_loop, "/telemetry", "list_nodes")}
    assert nodes["node-07"]["drained"] and nodes["node-07"]["cordoned"]
    assert not nodes["node-06"]["drained"]

    state = admin(server_loop, "GET", "/sim/state").json()
    entry = state["audit_log"][-1]
    assert entry["tool"] == "drain_node" and entry["caller"] == "remediator"


def test_drain_rack_b_blast_radius(server_loop):
    rack_b = [f"node-{i:02d}" for i in range(9, 17)]
    racks = {n["node"]: n["rack"] for n in ok(server_loop, "/telemetry", "list_nodes")}
    assert len({racks[n] for n in rack_b}) == 1          # really one rack

    ok(server_loop, "/ops", "drain_nodes", {"nodes": rack_b},
       headers={"X-GFG-Caller": "naive-agent"})
    summary = ok(server_loop, "/telemetry", "fleet_summary")
    assert summary["gpus_out_of_service"] == 64
    assert sorted(summary["drained"]) == rack_b

    audit = admin(server_loop, "GET", "/sim/state").json()["audit_log"]
    assert [(e["tool"], e["caller"]) for e in audit] == [("drain_nodes", "naive-agent")]


def test_caller_defaults_without_header(server_loop):
    ok(server_loop, "/ops", "cordon_node", {"node": "node-03"})
    entry = admin(server_loop, "GET", "/sim/state").json()["audit_log"][-1]
    assert entry["caller"] == "gfg-ops:remote"


def test_fleet_reset_between_tests(server_loop):
    # runs after the drain tests: autouse reset must have cleared them
    assert ok(server_loop, "/telemetry", "fleet_summary")["gpus_out_of_service"] == 0
    assert admin(server_loop, "GET", "/sim/state").json()["audit_log"] == []


# ---------------------------------------------------------------- 4. error path

@pytest.mark.parametrize("path,tool,args", [
    ("/telemetry", "get_dmesg", {"node": "node-99"}),
    ("/ops", "drain_node", {"node": "node-99"}),
    ("/ops", "drain_nodes", {"nodes": ["node-01", "node-99"]}),
])
def test_unknown_node_is_tool_error(server_loop, path, tool, args):
    res = call(server_loop, path, tool, args)
    assert res.isError
    assert "node-99" in res.content[0].text
    # bulk drain validates first: nothing was touched
    assert ok(server_loop, "/telemetry", "fleet_summary")["drained"] == []


# ---------------------------------------------------------------- 5. RequireToken

def _guarded(token):
    inner = Starlette(routes=[Route("/", lambda req: PlainTextResponse("hi"))])
    return RequireToken(inner, token)


def _get(asgi, headers=None) -> int:
    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=asgi),
                                     base_url=BASE) as c:
            return (await c.get("/", headers=headers or {})).status_code
    return asyncio.run(go())


def test_require_token_off_when_unset():
    assert _get(_guarded(None)) == 200
    assert _get(_guarded("")) == 200


def test_require_token_enforced():
    g = _guarded("s3cret")
    assert _get(g) == 401
    assert _get(g, {"Authorization": "Bearer nope"}) == 401
    assert _get(g, {"X-API-Key": "nope"}) == 401
    assert _get(g, {"Authorization": "Bearer s3cret"}) == 200
    assert _get(g, {"X-API-Key": "s3cret"}) == 200


# ---------------------------------------------------------------- 6. /sim admin + /health

def test_health(server_loop):
    r = admin(server_loop, "GET", "/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["simulated"] is True
    assert set(body["servers"]) == {"gfg-telemetry", "gfg-ops"}


def test_scene_rma_trap(server_loop):
    r = admin(server_loop, "POST", "/sim/scene/rma_trap")
    assert r.status_code == 200
    assert [f["fault"] for f in r.json()["injected"]] == ["PERSISTENCE_OFF"]
    faults = admin(server_loop, "GET", "/sim/state").json()["summary"]["active_faults"]
    assert [f["fault"] for f in faults] == ["PERSISTENCE_OFF"]


def test_scene_unknown_404(server_loop):
    assert admin(server_loop, "POST", "/sim/scene/nope").status_code == 404


def test_admin_token_guard(server_loop, monkeypatch):
    monkeypatch.setattr(app_module, "ADMIN_TOKEN", "adm1n")
    assert admin(server_loop, "POST", "/sim/reset").status_code == 401
    assert admin(server_loop, "POST", "/sim/scene/rma_trap",
                 {"X-Admin-Token": "wrong"}).status_code == 401
    assert admin(server_loop, "POST", "/sim/reset", {"X-Admin-Token": "adm1n"}).status_code == 200
    assert admin(server_loop, "POST", "/sim/scene/rma_trap",
                 {"X-Admin-Token": "adm1n"}).status_code == 200
    # read-only state stays open
    assert admin(server_loop, "GET", "/sim/state").status_code == 200
