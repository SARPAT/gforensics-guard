# PROGRESS

| CP | Status | Notes |
|----|--------|-------|
| setup | ✅ done | HANDOFF.md, CLAUDE.md, captures @ 4eb6bcd |
| CP0 | ✅ answered (attribution follow-up open) | ArmorIQ spike, T1–T5 below; scripts in `spike/` |
| CP1 | ⏳ next | fleet-sim + fault injector (draft plan ready) |

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
