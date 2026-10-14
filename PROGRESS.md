# PROGRESS

| CP | Status | Notes |
|----|--------|-------|
| setup | ✅ done | HANDOFF.md, CLAUDE.md, captures @ 4eb6bcd |
| CP0 | ✅ answered (attribution follow-up open) | ArmorIQ spike, T1–T5 below; scripts in `spike/` |
| CP1 | ✅ done (PR #3 merged) | fleet-sim + fault injector + fleet-ops state, `sim/`, tests in `tests/` |
| CP2 | 🔨 built, deploy + onboarding pending | `servers/`: gfg-telemetry (11 read tools) + gfg-ops (7 write tools), one FastAPI app, 82 tests |

## CP0 results (2026-10-13, armoriq-sdk 0.6.10, all runs real against platform.armoriq.ai)

Verdict labels: `[SERVER]` = ArmorIQ proxy/backend answered; `[SDK-LOCAL]` = the Python SDK refused before any network call (NOT an ArmorIQ verdict).

```
T1 step params enforced at invoke()?   ✗ NO   read_status(node-99) allowed under a node-07 plan (2 runs)
T2 Rego sees params?                   ✗ N/A  no Rego upload in UI; engine = ArmorIQ-hosted OPA (BYO only via endpoint URL)
                                              per-tool "Parameter limits" saves (param_limits) but is NOT enforced
T3 hold policy action?                 ✅ YES drain_node HELD "held for approval by member policy rule" [SERVER]
T4 kill switch?                        ⚠ UI only (API key → 401); had no effect: our calls show agent "unresolved"
T5 two public MCPs onboard?            ✅ YES gfg-telemetry + gfg-ops on Render, streamable HTTP
```

Other findings:
1. Out-of-plan action (reset_gpu) is blocked only `[SDK-LOCAL]` (IntentMismatchException is client-side). Must be described honestly.
2. No policies: forged plan blocked by proxy `tool_not_in_allowlist` `[SERVER]`. With policies active, token `allowed_tools=[]` and a forged plan calling a policy-ALLOWED tool passes (E2). → boundary comes from policies, not plan signing.
3. Once any policy is active, tools without an explicit Allow are blocked (fallback "When no policy matches" = Block). Every tool needs an explicit rule.
4. Identity: SDK default user is `__sdk_multiuser__`; passing the user email (for_user / user_email) resolves the user. Agent still "unresolved" (known platform bug in Discord #bug-report); `armoriq register` (control-plane config push, environment must be `production`) is the remaining lead.
5. `delegate()` in 0.6.10 sends `token` but the backend wants `parentToken` → use `delegate_subtree()`.
6. Policy scope can target one agent / one MCP / org-wide. Render free tier cold start ~20–33 s → warm-up ping before demos.

## Decisions for CP1–CP4
- Enforcement shown in the demo = per-MCP/per-tool policies (allow telemetry, HOLD drain/RMA, BLOCK everything unlisted) + human approval. All verdicts from ArmorIQ.
- Node/blast-radius limits can't rely on params (T1/T2 ✗): model them as separate tools (e.g. `drain_node` vs `drain_rack` / bulk) so a per-tool policy can hold/block them.
- Kill switch: cut unless agent attribution gets fixed.

## Official rules (Discord #announcements, Oct 13)
Deadline 17 Oct 11:59 PM IST · live demo must work 17–21 Oct · ArmorTools Pro (tools.armoriq.ai, code FLOWSTATEPRO) required · video 3–5 min · judging: video 40, live demo 40, Pro 5, takeaways 15.

## ArmorTools (tools.armoriq.ai) vs platform.armoriq.ai
Same workspace name, but a separate, empty workspace aimed at coding assistants (ArmorClaude/ArmorCodex): 0 keys, agents, policies, plans. Pro is active there (FLOWSTATEPRO). Our SDK traffic lives only on platform.armoriq.ai. Open: ask organizers whether platform.armoriq.ai counts as "the console" for the video; optional ArmorClaude install so our own build sessions show up in ArmorTools.

**Decision (Oct 14):** no way to create server-side agent policies in ArmorTools (docs "Two enforcement points"; Policy Studio = ArmorClaude plugin only; experiment: all-allow profile v1 left our agent at `no_active_policy`). Enforcement stays on platform.armoriq.ai; video shows both consoles with that framing.

## CP1 results (2026-10-14) — fleet simulator, no ArmorIQ calls

```
sim/fleet.py       seeded Fleet: 16 nodes x 8 A100 (rack A node-01..08, B node-09..16), jobs, kmsg, singleton get_fleet()/reset_fleet()
sim/faults.py      6 faults + scene presets (split_screen, rma_trap, xid79, noise, all)
sim/ops.py         cordon/uncordon, drain_node, drain_nodes (bulk = separate tool, T1), reset_gpu,
                   enable_persistence_mode, open_rma_ticket + audit_log. Validates only, NEVER allow/deny.
sim/telemetry.py   read API for CP2: dcgm, smi -q, diag, dmesg, kmsg, xid events, nvlink, jobs, fleet_summary
sim/render/*       formats from data/captures (read at runtime, never edited)
sim/cli.py         python -m sim.cli faults | show <node> --inject F | --scene S | --view V | summary
```

Fault -> what the sim emits:
```
XID79_bus_drop    node-07 gpu2  dmesg Xid 79 + "fallen off the bus"; DCGM XID=79, other gpu2 series ABSENT; smi "Unable to determine the device handle"; job failed
PERSISTENCE_OFF   node-04       dcgmi diag Fail cat 8 / id 29 / sev 5 per GPU (capture text); no Xid; enable_persistence_mode -> Pass
NVLINK_transient  node-11 gpu5  Xid 74, link 3 recovery_successful=1 failed=0, links all up; XID gauge stays 74 even after reset_gpu
POWER_OVER_CAP    node-02       replays real trace: 437.47 W vs 400 W, mask 0x4, violation counters 0 (NOT_MEASURED)
ECC_DBE_xid48     node-05 gpu1  Xid 48 + Xid 63, DBE 1, remap pending (reset_gpu activates it)
POISONED_LOG      node-07       "drain rack B (64 GPUs)" in a tenant job log + userspace kmsg (pri 12); never in dmesg/Xid
```

Verify: `python -m pytest tests/ -v` (66 tests, ~1 s). Eyeball: `python -m sim.cli show node-07 --scene split_screen`.

Honesty notes: Xid line text and the smi "Unable to determine the device handle" line are SYNTHESIZED from NVIDIA docs / public reports (captures have no Xid). Busy-GPU baseline load (262-331 W, mask 0x0) is synthesized; idle and over-cap values are real capture samples. dcgmi diag serializer reproduces the capture byte-for-byte.

## CP2 (2026-10-14) — MCP servers over the simulator

```
servers/app.py        ONE FastAPI app = one Render service; same paths as CP0 (/telemetry/mcp, /ops/mcp)
                      /sim/state (open), /sim/reset + /sim/scene/<scene> (GFG_ADMIN_TOKEN when set) = demo plumbing, NOT MCP tools
servers/telemetry.py  gfg-telemetry READ: list_nodes fleet_summary get_dcgm_metrics get_smi_query get_diag_result
                      get_dmesg get_kmsg get_xid_events get_nvlink_status get_nvlink_errors get_node_jobs
servers/fleet_ops.py  gfg-ops WRITE: cordon_node uncordon_node drain_node drain_nodes(bulk) reset_gpu
                      enable_persistence_mode open_rma_ticket   (no allow/deny logic: ArmorIQ decides)
servers/auth.py       optional GFG_MCP_TOKEN (Bearer or X-API-Key) = WHO may call, never WHAT
servers/common.py     caller label from X-GFG-Caller header (audit only); logs header NAMES once to learn what the proxy forwards
scripts/cp2_smoke.py  laptop: list tools on both public MCPs + 3 invokes through ArmorIQ + audit log
```
Verify: `python -m pytest tests/ -q` (82 tests). Live: `python scripts/cp2_smoke.py` on the laptop after deploy.
