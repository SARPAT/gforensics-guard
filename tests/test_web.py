"""CP5 web UI: offline checks (replays + naive agent, no ArmorIQ / LLM keys)."""
import json
import pathlib
import time

import pytest
from fastapi.testclient import TestClient

from servers.app import app
from web import runs

REC = pathlib.Path(runs.RECORDED_DIR)


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.delenv("ARMORIQ_API_KEY", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.setattr(runs, "REPLAY_MAX_GAP_S", 0)
    return TestClient(app)


def wait(client, rid, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        r = client.get(f"/api/runs/{rid}").json()
        if r["status"] != "running":
            return r
        time.sleep(0.1)
    raise AssertionError("run did not finish")


def test_pages_served(client):
    for path in ("/", "/split", "/report/x", "/static/app.js", "/static/app.css"):
        assert client.get(path).status_code == 200


def test_scenes_list_recordings(client):
    d = client.get("/api/scenes").json()
    assert d["live_available"] is False
    assert {s["name"] for s in d["scenes"]} >= {"split_screen", "rma_trap", "xid79", "noise"}
    assert d["recorded"]


def test_live_refused_without_key(client):
    r = client.post("/api/runs", json={"scene": "xid79", "mode": "live"})
    assert r.status_code == 403


def test_replay_passes_verdicts_through_unchanged(client):
    rid = client.post("/api/runs", json={"scene": "split_screen", "mode": "replay",
                                          "compromised": True, "naive": True}).json()["id"]
    r = wait(client, rid)
    assert r["status"] == "done"
    src = json.loads(next(REC.glob("*split_screen-compromised.json")).read_text())
    want = [(e["tool"], e["decision"], e["source"]) for e in src["events"] if e["kind"] == "call"]
    got = [(e["tool"], e["decision"], e["source"]) for e in r["gov_events"] if e["kind"] == "call"]
    assert got == want                                    # the UI shows exactly what ArmorIQ said
    assert r["gov_fleet"]["gpus_out_of_service"] == 8     # only node-07 reached the fleet
    assert r["naive_fleet"]["gpus_out_of_service"] == 64  # naive (scripted, no LLM) drains rack B


def test_requester_hidden(client):
    rid = client.post("/api/runs", json={"scene": "xid79", "mode": "replay", "naive": False}).json()["id"]
    r = wait(client, rid)
    obj = next(e for e in r["gov_events"] if e["kind"] == "objective")
    assert obj["requester"] in (None, "on-call SRE")
    assert "gmail" not in json.dumps(r)


def test_recordings_have_no_emails_or_tokens():
    for p in REC.glob("*.json"):
        s = p.read_text()
        assert "gmail" not in s and '"token"' not in s, p.name


def test_live_budget_cooldown_and_cap(client, monkeypatch):
    """CP6: the public site rations live runs (our app's budget, not an ArmorIQ verdict)."""
    m = runs.RunManager()
    monkeypatch.setattr(runs, "LIVE_COOLDOWN_S", 60)
    monkeypatch.setattr(runs, "LIVE_DAILY_CAP", 2)
    m._spend_live_budget()
    with pytest.raises(runs.BudgetError, match="try again"):
        m._spend_live_budget()
    m.live_starts = [time.time() - 3600, time.time() - 1800]
    with pytest.raises(runs.BudgetError, match="used up"):
        m._spend_live_budget()
    m.live_starts = [time.time() - 90000]           # older than 24 h: forgotten
    m._spend_live_budget()
    assert m.live_budget()["used_24h"] == 1


def test_budget_refusal_releases_live_lock(monkeypatch):
    monkeypatch.setenv("ARMORIQ_API_KEY", "x")
    m = runs.RunManager()
    m.live_starts = [time.time()]
    with pytest.raises(runs.BudgetError):
        m.start("xid79", "live")
    assert m.live_lock.acquire(blocking=False)     # not left locked


def test_health_reports_deploy_without_secrets(client):
    h = client.get("/health").json()
    assert h["ok"] and "commit" in h and h["live_runs"] is False
    assert "token" not in json.dumps({k: v for k, v in h.items() if k != "mcp_auth_last_seen"}).lower()
