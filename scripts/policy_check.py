"""CP4: the policy matrix, and drift between policies/*.yaml and a live export.

    python scripts/policy_check.py                # tool -> action matrix from policies/*.yaml
    python scripts/policy_check.py --live DIR     # also diff against YAML copied from the
                                                  # platform's YAML lens into DIR (same file names)

Read-only. It never talks to ArmorIQ and never decides anything: it only shows what the
declared policies say, so the demo can be checked against them.
"""
from __future__ import annotations

import argparse
import pathlib
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1] / "policies"
KEYS = ("name", "default_action", "priority")


def load(path: pathlib.Path) -> dict:
    doc = yaml.safe_load(path.read_text())
    return {**{k: doc.get(k) for k in KEYS},
            "scope": doc.get("scope", {}),
            "rules": {r["tool"]: r["action"] for r in doc.get("rules", [])}}


def diff(a: dict, b: dict) -> list[str]:
    out = [f"{k}: repo={a[k]!r} live={b[k]!r}" for k in KEYS if a[k] != b[k]]
    if a["scope"] != b["scope"]:
        out.append(f"scope: repo={a['scope']} live={b['scope']}")
    for tool in sorted(set(a["rules"]) | set(b["rules"])):
        if a["rules"].get(tool) != b["rules"].get(tool):
            out.append(f"rule {tool}: repo={a['rules'].get(tool)} live={b['rules'].get(tool)}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", type=pathlib.Path)
    args = ap.parse_args()
    drift = 0
    for path in sorted(ROOT.glob("*.yaml")):
        p = load(path)
        print(f"\n{p['name']}  (priority {p['priority']}, unlisted tools -> {p['default_action'].upper()})")
        for tool, action in p["rules"].items():
            print(f"  {action.upper():6} {tool}")
        if args.live:
            live = args.live / path.name
            if not live.exists():
                print(f"  ⚠ no live export at {live}")
                drift += 1
                continue
            d = diff(p, load(live))
            drift += len(d)
            print("  ✅ matches live export" if not d else "  ✗ DRIFT\n    " + "\n    ".join(d))
    return 1 if drift else 0


if __name__ == "__main__":
    sys.exit(main())
