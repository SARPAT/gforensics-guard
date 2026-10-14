"""The ONLY place the agents talk to ArmorIQ (armoriq-sdk 0.6.10).

Every verdict recorded here is what the SDK/proxy returned, labelled with its source:
    SERVER     the ArmorIQ backend or proxy answered (a real enforcement verdict)
    SDK-LOCAL  the Python SDK refused before any network call (IntentMismatch: the
               action is not in the committed plan). Real, but client-side.
    ERROR      transport/tool error: neither allowed nor denied by policy
There is no code path that turns an ERROR or an exception into ALLOWED.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from armoriq_sdk import (ArmorIQClient, ArmorIQException, IntentMismatchException,
                         PolicyBlockedException, PolicyHoldException)
from armoriq_sdk.models import DelegationRequestParams

TOKEN_VALIDITY_S = 600


@dataclass
class Verdict:
    actor: str
    mcp: str
    tool: str
    params: dict
    decision: str            # ALLOWED | HELD | BLOCKED | ERROR
    source: str              # SERVER | SDK-LOCAL | ERROR
    reason: str = ""
    data: Any = None         # tool output when ALLOWED
    plan_hash: str = ""
    delegated: bool = False  # called with a delegate_subtree() child token
    delegation_context: dict | None = None
    approval_id: str | None = None
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        d = asdict(self)
        if isinstance(d["data"], str) and len(d["data"]) > 400:
            d["data"] = d["data"][:400] + f"... ({len(self.data)} chars)"
        return d


@dataclass
class CommittedPlan:
    phase: str
    goal: str
    steps: list[dict]
    token: Any                       # armoriq_sdk IntentToken
    plan_hash: str
    plan_id: str | None
    intent_reference: str
    parent_plan_hash: str | None
    allowed_tools: list | None
    expires_at: float

    def summary(self) -> dict:
        return {"phase": self.phase, "goal": self.goal, "plan_hash": self.plan_hash,
                "plan_id": self.plan_id, "intent_reference": self.intent_reference,
                "parent_plan_hash": self.parent_plan_hash, "allowed_tools": self.allowed_tools,
                "steps": [{"tool": s["tool"], "mcp": s["mcp"], "params": s["params"]} for s in self.steps]}


class PlanRefused(Exception):
    """get_intent_token() was refused by ArmorIQ (policy at issuance)."""

    def __init__(self, verdict: Verdict):
        super().__init__(verdict.reason)
        self.verdict = verdict


def new_agent_key() -> str:
    """Ed25519 public key (hex) identifying a sub-agent in a delegation."""
    return Ed25519PrivateKey.generate().public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()


def tool_output(result: Any) -> Any:
    """MCP CallToolResult (as relayed by the proxy) -> the tool's return value."""
    if isinstance(result, dict):
        sc = result.get("structuredContent")
        if isinstance(sc, dict):
            return sc.get("result", sc)
        content = result.get("content")
        if isinstance(content, list):
            text = "".join(c.get("text", "") for c in content if isinstance(c, dict))
            try:
                return json.loads(text)
            except ValueError:
                return text
    return result


class ArmorIQGateway:
    def __init__(self, client: ArmorIQClient | None = None, user_email: str | None = None,
                 llm_label: str = "gfg-orchestrator"):
        self.client = client or ArmorIQClient()
        self.user_email = user_email.strip().lower() if user_email else None
        if self.user_email:
            self.client.user_email_override = self.user_email   # what for_user() sets
        self.llm_label = llm_label

    # ── plans ───────────────────────────────────────────────────────────────
    def commit(self, phase: str, goal: str, steps: list[dict],
               parent: CommittedPlan | None = None, metadata: dict | None = None) -> CommittedPlan:
        """capture_plan + get_intent_token. One plan step per tool: the SDK matches a call
        to the FIRST step with that action name (params are not enforced, CP0 T1)."""
        seen, plan_steps = set(), []
        for s in steps:
            if s["tool"] in seen:
                continue
            seen.add(s["tool"])
            plan_steps.append({"action": s["tool"], "tool": s["tool"], "mcp": s["mcp"],
                               "params": s["params"]})
        plan = {"goal": goal, "steps": plan_steps}
        meta = {"phase": phase, **(metadata or {})}
        if parent:
            meta["parent_plan_hash"] = parent.plan_hash
            plan["parent_plan_hash"] = parent.plan_hash   # inside the signed plan, not just metadata
        cap = self.client.capture_plan(llm=self.llm_label, prompt=goal, plan=plan, metadata=meta)
        try:
            tok = self.client.get_intent_token(cap, validity_seconds=TOKEN_VALIDITY_S)
        except PolicyBlockedException as e:
            raise PlanRefused(Verdict("orchestrator", "-", f"get_intent_token[{phase}]", {},
                                      "BLOCKED", "SERVER", f"{e} reason={e.reason}"))
        except ArmorIQException as e:
            raise PlanRefused(Verdict("orchestrator", "-", f"get_intent_token[{phase}]", {},
                                      "ERROR", "ERROR", f"{type(e).__name__}: {e}"))
        return CommittedPlan(phase, goal, plan_steps, tok, tok.plan_hash, tok.plan_id,
                             tok.token_id, parent.plan_hash if parent else None,
                             (tok.policy_validation or {}).get("allowed_tools"), tok.expires_at)

    def delegate(self, plan: CommittedPlan, agent: str, subtree_path: str = "/steps") -> dict:
        """delegate_subtree(): child token bounded to `subtree_path` of the plan.
        (delegate() is broken in SDK 0.6.10: it sends `token`, backend wants `parentToken`.)"""
        pub = new_agent_key()
        d = self.client.delegate_subtree(plan.token, delegate_public_key=pub,
                                         subtree_path=subtree_path,
                                         validity_seconds=TOKEN_VALIDITY_S,
                                         parent_plan={"goal": plan.goal, "steps": plan.steps},
                                         target_agent=agent)
        return {"agent": agent, "agent_pubkey": pub, "subtree_path": subtree_path,
                "trust_id": d.get("trust_id"), "subtree_root": d.get("subtree_root"),
                "parent_plan_hash": plan.plan_hash, "token": d["delegated_token"]}

    # ── calls ───────────────────────────────────────────────────────────────
    def call(self, token, actor: str, mcp: str, tool: str, params: dict,
             extra: dict | None = None) -> Verdict:
        v = Verdict(actor, mcp, tool, dict(params), "ERROR", "ERROR",
                    plan_hash=getattr(token, "plan_hash", ""),
                    delegated=bool(getattr(token, "subtree_delegation", None)))
        return self._verdict(v, lambda: self.client.invoke(
            mcp, tool, token, {**params, **(extra or {})}, user_email=self.user_email))

    @staticmethod
    def _verdict(v: Verdict, invoke: Callable[[], Any]) -> Verdict:
        try:
            res = invoke()
            v.decision, v.source, v.data = "ALLOWED", "SERVER", tool_output(res.result)
            if res.status == "error":
                v.reason = "tool returned an error payload"
        except IntentMismatchException as e:
            v.decision, v.source, v.reason = "BLOCKED", "SDK-LOCAL", f"IntentMismatch: {e}"
        except PolicyHoldException as e:
            v.decision, v.source, v.reason = "HELD", "SERVER", str(e)
            v.delegation_context = {"delegation_context": e.delegation_context, "metadata": e.metadata}
        except PolicyBlockedException as e:
            v.decision, v.source, v.reason = "BLOCKED", "SERVER", f"{e} (reason={e.reason})"
        except ArmorIQException as e:
            v.reason = f"{type(e).__name__}: {e}"
        return v

    # ── approval (delegation request) for a HELD call ──────────────────────
    def request_approval(self, plan: CommittedPlan, held: Verdict, requester_email: str,
                         amount: float) -> str:
        ctx = (held.delegation_context or {}).get("delegation_context") or {}
        req = self.client.create_delegation_request(DelegationRequestParams(
            tool=held.tool, action="execute", arguments=held.params, amount=max(amount, 0.01),
            requester_email=requester_email, requester_role="agent_user", requester_limit=0,
            domain=ctx.get("domain", held.mcp), target_url=ctx.get("targetUrl"),
            plan_id=ctx.get("planId") or plan.plan_id,
            intent_reference=ctx.get("intentReference") or plan.intent_reference,
            merkle_root=ctx.get("merkleRoot") or plan.plan_hash, reason=held.reason))
        return req.delegation_id

    def approval_status(self, delegation_id: str) -> str:
        return self.client.get_delegation_status(delegation_id)
