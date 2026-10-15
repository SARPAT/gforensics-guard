# Submission checklist (Flow State Hackathon)

🔴 Deadline: **Oct 17, 2026, 11:59 PM IST = 18:29 UTC**. Aim to submit by Oct 17 12:00 UTC (6 h buffer).
Live demo must keep working **Oct 17–21**.

## Before submitting

```
[ ] PR with CP6 merged to master, Render auto-deployed, /health shows the new commit
[ ] keep-alive workflow enabled (Actions tab → keep-alive → runs every 10 min, HTTP 200)
[ ] live run of each scene from https://gforensics-guard.onrender.com works (split_screen, rma_trap, xid79)
[ ] stale held requests cleared in platform.armoriq.ai → Intent → Held Actions
[ ] policies on the platform match policies/*.yaml (scripts/policy_check.py)
[ ] README Disclosure accurate; no secrets in the repo (.env never committed)
[ ] ArmorTools Pro active for every team member (tools.armoriq.ai → Settings → Billing)   ✅ done Oct 13
[ ] demo video (CP7), 3–5 min, unlisted YouTube / Loom / Vimeo
[ ] post on X (tag @ArmorIQForDevs) or LinkedIn (tag ArmorIQ) with #FlowStateHackathon
```

## Form fields (forms.gle/fxkXnY8xgeyZ3QQX6)

```
Team / name        Saransh Patel (solo)
GitHub repo        https://github.com/SARPAT/gforensics-guard
Live demo URL      https://gforensics-guard.onrender.com
Video link         (CP7)
How ArmorIQ used   see below
Post link          (after posting)
```

**How ArmorIQ is used (paste):**

> Gforensics Guard is a GPU-fleet incident agent. It investigates freely but has to earn remediation authority through
> ArmorIQ. Each phase is a signed ArmorIQ plan (capture_plan + get_intent_token), sub-agents get delegate_subtree tokens, and
> every tool call goes through invoke() to the ArmorIQ proxy in front of our two MCP servers (gfg-telemetry, gfg-ops). Declarative
> policies on platform.armoriq.ai allow reads and single-node remediation, HOLD RMA tickets and GPU resets for a second human in
> the Held Actions console (the requester can't approve their own request), and BLOCK bulk rack drains. The demo shows a prompt
> injection in a tenant job log and a compromised planner that writes "drain rack B" into the signed plan: ArmorIQ blocks it and
> the rack stays up. All verdicts in the UI are real ArmorIQ responses; the fleet is simulated. We also used ArmorClaude with a
> Policy Studio profile on tools.armoriq.ai to govern our own coding agent during the build.

## Post draft (Saransh posts it himself)

> Built Gforensics Guard for #FlowStateHackathon by @ArmorIQForDevs: a GPU-fleet incident agent that can read all the telemetry
> it wants, but has to earn the right to touch the fleet. A poisoned job log tells the AI to drain a whole rack. ArmorIQ blocks it;
> the rack stays up. RMA tickets wait for a second human.
> Live: https://gforensics-guard.onrender.com
> Code: https://github.com/SARPAT/gforensics-guard
