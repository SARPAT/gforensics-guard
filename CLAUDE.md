# CLAUDE.md — gforensics-guard (ArmorIQ Flow State Hackathon)

## 0. Mission
Build and deploy **Gforensics Guard**: a GPU-fleet incident agent that **investigates freely but must earn the authority to remediate** through ArmorIQ intent enforcement.
- Owner: Saransh Patel (GitHub: SARPAT). Solo build.
- 🔴 **Submission deadline: Oct 17, 2026** (window Oct 10–17). Keep a 6h buffer.
- Full plan, research, architecture, policies, demo script: **read `HANDOFF.md` in the repo root before any work.** Track status in `PROGRESS.md`.

## 1. Working rules (NON-NEGOTIABLE)
Saransh must understand everything built. No one-shot black boxes.

**Autonomy mode (Saransh, 2026-10-13):** discuss with Saransh ONLY at the start of each checkpoint (the PLAN step). After approval, test, iterate, adjust the plan and build fast without waiting; use subagents / sibling threads heavily for parallel work. Interrupt him only for actions that need him on platform.armoriq.ai (onboarding, policies, approvals, dashboard toggles) or for real blockers. Keys, verdict honesty and the end-of-checkpoint EXPLAIN still apply.

**Gate rule — every checkpoint:**
1. **PLAN** — what will be built + why (≤10 lines). WAIT for approval (the only mandatory wait).
2. **BUILD** — implement the approved scope; iterate freely within it.
3. **RUN** — run it yourself (via Remote Control on his laptop when keys are needed), then give ONE command for Saransh to verify himself.
4. **EXPLAIN** — 5 bullets: what exists now · key files · ArmorIQ call used · one tradeoff · what is simulated/faked.
5. **COMMIT** — `git commit -m "CP-n: ..."`, update `PROGRESS.md`.

**Checkpoints:**
```
CP0 🔴 ArmorIQ spike: SDK hello-world, 1 dummy MCP on Render, onboarded; answer T1–T5 → decides scope
CP1 fleet-sim (real formats from data/captures) + fault injector
CP2 telemetry-mcp + fleet-ops-mcp deployed (public URLs) + onboarded
CP3 diagnoser rule table + orchestrator (capture_plan → token → delegate → invoke)
CP4 policies (Policy Studio / Rego) + demo scenes
CP5 web UI timeline + lineage report; naive-agent baseline (split screen)
CP6 deploy, README (+disclosure), architecture diagram, enforcement doc, 3-min video → SUBMIT
```
Do NOT start a checkpoint before the previous one passes its gate. Do NOT start CP1 before CP0 answers T1–T5.

**Scope cuts if behind (in order):** kill switch → lineage page polish → fleet size (→4 nodes).
**Untouchable:** split-screen injection scene, RMA trap scene, authority expansion + approval hold.

**Remote Control (running things on Saransh's laptop):** his laptop serves the folder `/home/patel/Documents/gforensics-guard` (it has `.venv` and the gitignored `.env` with the keys) via `env -u ANTHROPIC_API_KEY claude remote-control` (spawn mode: same-dir). Any thread can start a session there with `start_rc_session` (folder from `list_devices`). Workflow: cloud thread commits to its `claude/...` branch and pushes → laptop session does `git fetch` + `git checkout <branch>` + `git pull --ff-only` → runs the script → reports stdout. The laptop never commits, pushes or edits `.env`, and never prints key values. If the laptop isn't connected, ask Saransh to restart the command above.

## 2. Communication style
- Bold section headers, point-wise breakdowns, concrete examples.
- Diagram-heavy: code-fenced ASCII blocks for structure, flows, comparisons, tradeoffs.
- Markers: 🔴 critical · ⚠ caution · ✅ confirmed · ✗ rejected. CAPS for emphasis inside blocks.
- Expand short forms on first use (e.g. MCP = Model Context Protocol).
- Honest, direct pushback over agreement. If an earlier claim was wrong, say "Self-correction:" explicitly.
- Number findings and carry them forward.
- **Terminal work: ONE command block at a time, wait for output, adapt.** No long multi-step sequences.
- Keep responses short unless asked for depth.

## 3. Hackathon rules — re-check at EVERY gate
1. ✅ Must integrate ArmorIQ — functional, not cosmetic. No functional integration = not eligible.
2. (ElevenLabs projects must also integrate ArmorIQ — not relevant to us.)
3. Any model / framework / language allowed.
4. ✅ At least one MCP server (we build two).
5. Core development during the window (Oct 10–17). Libraries/frameworks/templates OK.
6. AI coding assistants + OSS deps OK. **Significant pre-existing code must be disclosed.**
7. ✅ Sensitive operations in sandbox/test only. No real customer data, no production systems.
8. 🔴 Security demos must use **ACTUAL ArmorIQ enforcement** — never simulated or hardcoded verdicts.
9. Don't attack ArmorIQ infra, other participants, or third parties.
10. Misrepresenting integrations = disqualification.

**Platform requirements:** demonstrate intent + policy enforcement via https://platform.armoriq.ai. API keys generated on the platform. **All agents and MCP servers onboarded** on the platform.
**Submission:** working project, **DEPLOYED** (organizers confirmed), short demo video: what the agent does, how ArmorIQ is used, how the boundary is maintained.
**Inferred judging (past ArmorIQ track):** enforcement strength (deterministic runtime blocking) · architectural clarity (reasoning separated from execution, explicit enforcement layer) · realistic non-trivial scenario · **declarative policy, NOT hardcoded if/else** · repo + architecture diagram + intent/policy/enforcement doc + ~3-min video. Judges reward teams who can explain their tradeoffs.

## 4. Engineering guardrails
- 🔴 **Rule table decides what plan is PROPOSED; ArmorIQ decides what is ALLOWED.** Never block/allow actions with Python if/else — enforcement lives in ArmorIQ plans, delegations, policies, Rego.
- 🔴 Never fake an ArmorIQ verdict in UI or logs. Every allowed/held/blocked shown must come from a real ArmorIQ response.
- ArmorIQ proxy forwards to MCP servers → **MCP servers must be publicly reachable** (Render). Deploy early, not on day 5.
- Everything writes to the **simulated fleet only** (rule 7).
- Secrets (ArmorIQ API key, LLM key) via env vars only. Never commit `.env`. Add `.env.example`.
- `data/captures/` is copied reference data from Gforensics @ `4eb6bcd` — read-only, never edit.
- Pin dependency versions in `requirements.txt`.
- Keep the README "Disclosure" section accurate: pre-existing = scrubbed telemetry captures + design notes from Gforensics; no pre-existing code; all code written Oct 10–17, 2026.
- Don't modify the `SARPAT/Gforensics` repo.

## 5. ArmorIQ knowledge — VERIFY, DON'T GUESS
🔴 **Never invent SDK method names, signatures, policy fields, or API behaviour.** If unsure, **search the internet yourself** before writing code: official docs first, then the PyPI page, then ArmorIQ's GitHub repos (example code), then their blog/X posts. Docs paths may change — if a link 404s, web-search "ArmorIQ <topic>". State clearly when something is unverified and add it to the CP0 test list.

**Known so far (from docs, verify exact signatures in CP0):**
- Core SDK methods: `capture_plan()`, `get_intent_token()`, `invoke(mcp, action, token, params)`, `delegate(token, pubkey, allowed_actions=[...])`; `for_user(email)` scopes to an end user.
- Actions outside the committed plan → `IntentMismatchException`.
- `delegate()` restricts by action NAMES (not params); delegated tokens expire sooner.
- Delegation approvals: plans held when policy limits exceeded until a higher-role human approves; requester can't self-approve.
- Policies: tool permissions, IP restrictions, time constraints, rate limiting. Optional OPA engine with custom Rego (enforce / shadow mode).
- Platform features: Policy Studio, per-agent kill switch, observability traces (session → trace → span), AIQ Graph.

**CP0 open tests:**
```
T1 Step params enforced at invoke()?      T2 Does Rego see input.params?
T3 Does a "hold" policy action exist?     T4 Kill switch via API/dashboard?
T5 Two public MCPs onboard cleanly? (if no → ask organizers on Discord immediately)
```

## 6. Resource links
**ArmorIQ (official):**
- Website: https://armoriq.ai
- Platform (API keys, onboarding, Policy Studio): https://platform.armoriq.ai
- Docs home: https://docs.armoriq.ai
- Getting started: https://docs.armoriq.ai/platform/getting-started
- Plans: https://docs.armoriq.ai/platform/plans
- Policies: https://docs.armoriq.ai/platform/policies
- Policy enforcement: https://docs.armoriq.ai/platform/policies/policy-enforcement
- OPA engine: https://docs.armoriq.ai/platform/opa-engine
- SDK client init: https://docs.armoriq.ai/sdk/client-initialization
- SDK delegate(): https://docs.armoriq.ai/sdk/core-methods/delegate
- Python SDK (PyPI): https://pypi.org/project/armoriq-sdk/
- GitHub org (example code): https://github.com/armoriq
  - https://github.com/armoriq/armorclaw (OSS cryptographic intent assurance)
  - https://github.com/armoriq/armoriq-openshell-middleware (NVIDIA OpenShell enforcement — mention as deployment path, do NOT build)
- Blog — thesis we implement: https://armoriq.ai/blogs/the-defenders-need-ai-their-ai-needs-boundaries (Sep 17, 2026: investigation → remediation authority must be explicit and attributable)
- X: https://x.com/armoriqAI (Sep 4 tweet: "intent lineage is the next big thing in security"). X blocks scraping; use a Nitter mirror via search if needed.

**Our repos:**
- Build repo: https://github.com/SARPAT/gforensics-guard
- Source of captures (do not modify): https://github.com/SARPAT/Gforensics — findings in `data/captures/README.md`

**Building blocks:**
- MCP spec: https://modelcontextprotocol.io
- MCP Python SDK: https://github.com/modelcontextprotocol/python-sdk
- NVIDIA XID errors (for synthesizing dmesg Xid lines): https://docs.nvidia.com/deploy/xid-errors/
- DCGM: https://docs.nvidia.com/datacenter/dcgm/latest/
- dcgm-exporter: https://github.com/NVIDIA/dcgm-exporter
- FastAPI: https://fastapi.tiangolo.com · Render: https://render.com/docs
- OPA / Rego: https://www.openpolicyagent.org/docs/latest/policy-language/

**Hackathon communication:** official updates are on the ArmorIQ Discord (Saransh pastes relevant messages). If a rule is ambiguous, flag it and suggest asking organizers rather than guessing.

## 7. Tech stack
Python 3.11+, FastAPI, `armoriq-sdk`, MCP Python SDK, any LLM via API (plan drafting + narrative only), plain HTML/JS UI served by FastAPI, Render for deployment.
