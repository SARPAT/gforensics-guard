"""`dcgmi diag -r 1 -j` JSON, per node.

Template: data/captures/scrubbed/dcgmi_diag_r1.json (structure, keys, metadata
and the persistence-mode warning are taken from the capture; the serializer
reproduces dcgmi's jsoncpp layout: tabs, ' : ', nested values on a new line).
Finding #8: 'Fail' here is persistence mode (error_category 8, error_id 29,
severity 5) = deployment misconfiguration, NOT a hardware fault.
"""
from __future__ import annotations

import copy
import json
import re

from sim import captures
from sim.model import Node


def dumps_dcgmi(value, level: int = 0) -> str:
    """jsoncpp StyledWriter-style output, as dcgmi prints it."""
    tab = "\t"
    if isinstance(value, dict):
        if not value:
            return "{}"
        parts = []
        for i, (k, v) in enumerate(value.items()):
            sep = "," if i < len(value) - 1 else ""
            if isinstance(v, (dict, list)) and v:
                parts.append(f"{tab * (level + 1)}{json.dumps(k)} : \n"
                             f"{tab * (level + 1)}{dumps_dcgmi(v, level + 1)}{sep}")
            else:
                parts.append(f"{tab * (level + 1)}{json.dumps(k)} : {dumps_dcgmi(v, level + 1)}{sep}")
        return "{\n" + "\n".join(parts) + "\n" + tab * level + "}"
    if isinstance(value, list):
        if not value:
            return "[]"
        parts = [f"{tab * (level + 1)}{dumps_dcgmi(v, level + 1)}{',' if i < len(value) - 1 else ''}"
                 for i, v in enumerate(value)]
        return "[\n" + "\n".join(parts) + "\n" + tab * level + "]"
    return json.dumps(value)


def build(node: Node) -> dict:
    doc = copy.deepcopy(captures.diag_template())
    test = doc["DCGM Diagnostic"]["test_categories"][0]["tests"][0]
    proto = test["results"][0]
    warn_proto = proto["warnings"][0]
    results = []
    for gpu in node.gpus:
        if not gpu.on_bus:
            continue                          # DCGM can't enumerate a GPU that left the bus
        r = {k: v for k, v in proto.items() if k != "warnings"}
        r["entity_id"] = gpu.index
        if gpu.persistence_mode:
            r["status"] = "Pass"
        else:
            r["status"] = "Fail"
            w = dict(warn_proto)
            w["warning"] = re.sub(r"for GPU \d+", f"for GPU {gpu.index}", w["warning"])
            r["warnings"] = [w]
        results.append(r)
    test["results"] = results
    test["test_summary"]["status"] = "Fail" if any(r["status"] == "Fail" for r in results) else "Pass"
    ent_proto = doc["entity_groups"][0]["entities"][0]
    doc["entity_groups"][0]["entities"] = [
        {**ent_proto, "entity_id": g.index, "serial_num": g.serial} for g in node.gpus if g.on_bus]
    return doc


def render_node(node: Node) -> str:
    return dumps_dcgmi(build(node)) + "\n"
