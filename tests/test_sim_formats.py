"""Rendered telemetry matches the REAL capture formats (CP1 plan §6: test_dcgm_format,
test_diag_matches_capture). The agent should not be able to tell sim output from a
real A100 node by format alone.
"""
from __future__ import annotations

import json
import re

import pytest

from conftest import read_capture
from sim import captures, telemetry
from sim.render.diag import dumps_dcgmi
from sim.render.dmesg import decode_packed_gpu_id

DCGM_SERIES_RE = re.compile(r"^DCGM_FI_\w+\{[^}]*\} [0-9.]+$")
DMESG_RE = re.compile(r"^\[\w{3} \w{3} \d{2} \d{2}:\d{2}:\d{2} \d{4}\] ")
KMSG_RE = re.compile(r"^\d+,\d+,\d+,-;")
LABEL_KEY_RE = re.compile(r'(\w+)="')
NODES = [f"node-{i:02d}" for i in (1, 7, 9, 16)]


# ---- helpers ----------------------------------------------------------------
def dcgm_headers(text: str) -> tuple[list[str], list[str]]:
    """(# HELP lines, # TYPE lines) in order."""
    lines = text.splitlines()
    return ([l for l in lines if l.startswith("# HELP ")],
            [l for l in lines if l.startswith("# TYPE ")])


def dcgm_series(text: str) -> list[str]:
    return [l for l in text.splitlines() if l and not l.startswith("#")]


def smi_gpu_blocks(text: str) -> list[list[str]]:
    """Split `nvidia-smi -q` output into per-GPU blocks (header line included)."""
    blocks, cur = [], None
    for line in text.rstrip("\n").split("\n"):
        if line.startswith("GPU 00000000:"):
            cur = [line]
            blocks.append(cur)
        elif cur is not None and line:
            cur.append(line)
    return blocks


def smi_keys(block: list[str]) -> list[str]:
    """Indentation + key of every line, ignoring values ('    Bus Id   : x' -> '    Bus Id')."""
    return [re.sub(r"\s*:.*$", "", l) if " : " in l or l.rstrip().endswith(":") else l.rstrip()
            for l in block[1:]]


# ---- DCGM exporter ------------------------------------------------------------
@pytest.mark.parametrize("node", NODES)
def test_dcgm_series_lines_match_prometheus_format(fleet, node):
    text = telemetry.get_dcgm_metrics(fleet, node)
    series = dcgm_series(text)
    assert series, "no series rendered"
    bad = [l for l in series if not DCGM_SERIES_RE.match(l)]
    assert bad == []


def test_dcgm_help_and_type_equal_capture(fleet):
    """Same metric set, order, HELP text and TYPE as the real dcgm-exporter scrape."""
    cap_help, cap_type = dcgm_headers(read_capture("dcgm_exporter_metrics.txt"))
    for node in NODES:
        help_, type_ = dcgm_headers(telemetry.get_dcgm_metrics(fleet, node))
        assert help_ == cap_help
        assert type_ == cap_type


def test_dcgm_metric_names_equal_capture(fleet):
    cap = {l.split("{")[0] for l in dcgm_series(read_capture("dcgm_exporter_metrics.txt"))}
    sim_ = {l.split("{")[0] for l in dcgm_series(telemetry.get_dcgm_metrics(fleet, "node-01"))}
    assert sim_ == cap


def test_dcgm_every_gpu_has_every_metric(fleet):
    """Healthy node: 8 series per metric (one per GPU)."""
    text = telemetry.get_dcgm_metrics(fleet, "node-01")
    n_metrics = len(dcgm_headers(text)[1])
    assert len(dcgm_series(text)) == 8 * n_metrics


def test_dcgm_label_keys_equal_capture(fleet):
    """Label keys = capture's: gpu, UUID, pci_bus_id, device, modelName, Hostname;
    the XID gauge adds err_code, err_msg (Finding #1 free code->meaning labels)."""
    base = ["gpu", "UUID", "pci_bus_id", "device", "modelName", "Hostname"]
    cap = dcgm_series(read_capture("dcgm_exporter_metrics.txt"))
    for line in cap:
        expected = base + (["err_code", "err_msg"] if line.startswith("DCGM_FI_DEV_XID_ERRORS") else [])
        assert LABEL_KEY_RE.findall(line) == expected       # sanity: capture is what we think
    for line in dcgm_series(telemetry.get_dcgm_metrics(fleet, "node-07")):
        expected = base + (["err_code", "err_msg"] if line.startswith("DCGM_FI_DEV_XID_ERRORS") else [])
        assert LABEL_KEY_RE.findall(line) == expected, line
    assert list(captures.dcgm_label_keys()) == base


def test_dcgm_hostname_label_is_not_node_name(fleet):
    """Finding #4: Hostname is the exporter container id, never the host's name."""
    for name in fleet.nodes:
        hosts = set(re.findall(r'Hostname="([^"]*)"', telemetry.get_dcgm_metrics(fleet, name)))
        assert len(hosts) == 1
        (host,) = hosts
        assert host != name and "node" not in host
        assert re.fullmatch(r"[0-9a-f]{12}", host)        # docker short id, like 7e6f00cf349d


# ---- nvidia-smi -q -------------------------------------------------------------
def test_smi_gpu_blocks_have_capture_shape(fleet):
    """Every GPU block has the captured block's line count and key sequence."""
    (cap_block,) = smi_gpu_blocks(read_capture("nvidia_smi_q.txt"))
    for node in NODES:
        blocks = smi_gpu_blocks(telemetry.get_smi_query(fleet, node))
        assert len(blocks) == 8
        for b in blocks:
            assert len(b) == len(cap_block)
            assert smi_keys(b) == smi_keys(cap_block)


def test_smi_header_reports_eight_gpus(fleet):
    text = telemetry.get_smi_query(fleet, "node-01")
    assert re.search(r"^Attached GPUs\s+: 8$", text, re.M)
    assert re.search(r"^Driver Version\s+: 570\.148\.08$", text, re.M)


def test_smi_blocks_carry_each_gpus_identity(fleet):
    node = fleet.node("node-03")
    text = telemetry.get_smi_query(fleet, "node-03")
    for gpu, block in zip(node.gpus, smi_gpu_blocks(text)):
        assert block[0] == f"GPU {gpu.pci_bus_id}"
        assert any(re.search(rf"GPU UUID\s+: {gpu.uuid}$", l) for l in block)


# ---- dcgmi diag -----------------------------------------------------------------
def test_diag_serializer_reproduces_capture_bytes():
    """The jsoncpp-style writer reproduces dcgmi's exact layout (tabs, ' : ')."""
    assert dumps_dcgmi(captures.diag_template()) + "\n" == read_capture("dcgmi_diag_r1.json")


@pytest.mark.parametrize("node", NODES)
def test_diag_render_is_json_with_capture_keys(fleet, node):
    cap = json.loads(read_capture("dcgmi_diag_r1.json"))
    doc = json.loads(telemetry.get_diag_result(fleet, node))
    assert doc.keys() == cap.keys()
    assert doc["metadata"] == cap["metadata"]
    test = doc["DCGM Diagnostic"]["test_categories"][0]["tests"][0]
    assert test["name"] == "software"
    assert [r["entity_id"] for r in test["results"]] == list(range(8))
    assert {r["status"] for r in test["results"]} == {"Pass"}      # baseline: persistence on
    assert test["test_summary"]["status"] == "Pass"


# ---- dmesg / kmsg ---------------------------------------------------------------
@pytest.mark.parametrize("node", NODES)
def test_dmesg_lines_have_ctime_prefix(fleet, node):
    lines = telemetry.get_dmesg(fleet, node).splitlines()
    assert lines and all(DMESG_RE.match(l) for l in lines)


def test_capture_dmesg_matches_same_regex():
    """Sanity: our dmesg regex accepts the real capture."""
    assert all(DMESG_RE.match(l) for l in read_capture("dmesg_nvrm.txt").splitlines())


@pytest.mark.parametrize("node", NODES)
def test_kmsg_lines_have_record_prefix(fleet, node):
    lines = telemetry.get_kmsg(fleet, node).splitlines()
    assert lines and all(KMSG_RE.match(l) for l in lines)
    seqs = [int(l.split(",")[1]) for l in lines]
    assert seqs == list(range(len(lines)))


def test_capture_kmsg_matches_same_regex():
    assert all(KMSG_RE.match(l) for l in read_capture("kmsg_sample.txt").splitlines() if l)


def test_decode_packed_gpu_id_capture_example():
    """Finding #9: '[GPU ID 0x00000600]' in the capture is 0000:06:00.0."""
    assert decode_packed_gpu_id("0x00000600") == "0000:06:00.0"
    assert decode_packed_gpu_id("0x00004700") == "0000:47:00.0"


def test_boot_drm_lines_decode_to_real_gpu_addresses(fleet):
    """Finding #9: every packed GPU ID in a node's boot log joins to one of its GPUs."""
    node = fleet.node("node-05")
    packed = re.findall(r"\[GPU ID (0x[0-9a-fA-F]{8})\]", telemetry.get_dmesg(fleet, "node-05"))
    assert len(packed) == 8
    assert [decode_packed_gpu_id(p) for p in packed] == [f"{g.pci_short}.0" for g in node.gpus]
