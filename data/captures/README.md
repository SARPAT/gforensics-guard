# Week 0 Capture Session — Real Telemetry Artifacts

Captured to ground the event schema, ingestion adapters, and failure
taxonomy in **real formats** rather than assumed ones.

Everything in `scrubbed/` is committed. `raw/` is gitignored.
Scrubber: `scripts/scrub_captures.sh` (idempotent, format-preserving).

## Capture environment

| Field | Value |
|---|---|
| Date | 2026-09-16 |
| Platform | Lightning AI Studio (containerized, host kernel shared) |
| GPU | 1× NVIDIA A100-SXM4-40GB |
| Driver | 570.148.08 (Open Kernel Module) |
| CUDA | 12.8 |
| OS | Ubuntu 24.04.4 LTS, kernel 6.8.0-60-generic |
| DCGM | 4.1.1 (`dcgm-exporter:4.1.1-4.0.4`, `cloud-native/dcgm:4.1.1-1`) |
| Interconnect | PCIe only — NVLink present but all links inactive |
| NIC | mlx5_0 (Mellanox), PHB to GPU |

`dmesg` required `sudo sysctl -w kernel.dmesg_restrict=0` (does not
persist across Studio restarts).

## Artifacts

| File | What it grounds |
|---|---|
| `dcgm_exporter_metrics.txt` | Prometheus exposition format, label set |
| `dcgm_field_reference.txt` | Full DCGM field-ID catalogue (authoritative) |
| `dcgmi_diag_r1.json` | Diagnostic result schema, typed error objects |
| `dcgmi_discovery.txt` | Entity enumeration format |
| `dcgm_throttle_trace.txt` | 4-min load test, DCGM side, 2s interval |
| `smi_throttle_trace.csv` | Same window, nvidia-smi side |
| `nvidia_smi_q.txt` | Full `-q` field tree |
| `nvidia_smi_topo.txt` | Topology matrix (1×1 — see limitations) |
| `nvlink_status.txt` | NVLink state (inactive) |
| `ecc_baseline.txt` | ECC field structure, healthy baseline |
| `dmesg_nvrm.txt` | Kernel NVRM line formats (no Xid — healthy GPU) |
| `kmsg_sample.txt` | `/dev/kmsg` structured format |
| `proc_nvidia_gpus.txt` | `/proc/driver/nvidia/gpus/*/information` |
| `environment.txt` | Host/driver metadata |

## Findings that changed the design

### 1. `DCGM_FI_DEV_XID_ERRORS` is a gauge of the *last* Xid, not a counter
Help text: "Value of the last XID error encountered." It persists after
recovery and cannot count events. Bursts within one scrape interval
collapse to a single observation.

**Consequence:** `dmesg` / `/dev/kmsg` is the authoritative Xid event
source. DCGM corroborates but cannot detect. The `err_code` / `err_msg`
labels do give free code→meaning decoding.

### 2. dcgm-exporter drops unsupported fields silently
Requested 43 fields in a custom counters file, got 32. No warning, no
error, no log line.

**Consequence:** a startup capability probe is required — scrape
`/metrics`, diff against the required-field list, and mark affected
failure classes as *unassessable* rather than *healthy*.

### 3. Violation counters can read 0 while the condition is active
During the load test `CLOCK_THROTTLE_REASONS` reported `0x4` (SwPowerCap)
for 119 consecutive samples while `POWER_VIOLATION`,
`THERMAL_VIOLATION` and `BOARD_LIMIT_VIOLATION` all stayed at `0`.
The counters require violation accounting to be enabled; unpopulated
reads as zero.

**Consequence:** the event schema needs three states, not two —
`MEASURED_PRESENT`, `MEASURED_ABSENT`, `NOT_MEASURED`. Cross-checking
the bitmask against the counters detects the third case and is itself
a contradiction rule for the causal KB.

### 4. `Hostname` label is the container ID, and the label set varies
Observed `Hostname="7e6f00cf349d"` — the dcgm-exporter container, not
the host. `DCGM_FI_DRIVER_VERSION` appeared with the default counters
file and was absent with the custom one.

**Consequence:** key entities on `UUID` + `pci_bus_id`. Never require
`Hostname` or any optional label.

### 5. ECC is per-location; remapped rows are a degradation ladder
Fields 310–345 split volatile/aggregate across L1, L2, device, register,
texture, shared, CBU and SRAM. SRAM ECC (Xid 94, contained) and DRAM
double-bit (Xid 48) need different remediation.

Fields 385–389 expose `remap_rows_avail_{max,high,partial,low,none}` —
a monotonic descent, not a binary. This is a precursor signal and feeds
lemon-node detection directly.

### 6. NVLink error taxonomy is far more granular than the exporter suggests
Fields 1200–1215 distinguish `rcv_err` from `rcv_remote_err` (local vs
peer fault), `link_recovery_successful` from `link_recovery_failed`
(transient vs persistent), and expose `symbol_ber` as a continuous
degradation signal. `buffer_overrun_err` indicates congestion, not
hardware failure.

Per-link field indices are **non-contiguous** (flit CRC exists for
l0–l5 then l12–l14). Parsers must not loop `0..N`.

**Consequence:** "NVLink fault" is too coarse for a single taxonomy
class. Recovery-succeeded is a *contradicting* signal for persistent
hardware-fault hypotheses.

### 7. dcgm-exporter cannot run diagnostics
It runs DCGM embedded, with no `nv-hostengine` listening and no plugin
directory. `dcgmi` against it fails with a connection error.
The `cloud-native/dcgm` image is required for `dcgmi diag`.

Separately, diagnostic results are readable as **fields 350–362**
(`pcie_test_result`, `diag_status`, …), so the agent can read results
without shelling out and parsing JSON.

### 8. `diag status: Fail` does not mean hardware failure
The r1 run failed on `error_category: 8, error_id: 29` — persistence
mode disabled. A deployment misconfiguration, not a fault.

**Consequence:** read `error_category` and `error_severity`, never
`status` alone.

### 9. Kernel logs identify GPUs by packed PCI address
`[drm] [nvidia-drm] [GPU ID 0x00000600] Loading driver` → `0000:06:00.0`.
The dmesg parser must decode this to join kernel events to DCGM entities.

### 10. Instantaneous power draw exceeds the enforced cap
Observed 437.47 W against a 400 W limit. `power.draw > power.limit` is
not by itself evidence of a fault.

## Load test result

4-minute fp16 GEMM loop (16384², 6900 iterations): throttle reasons 0x1 GpuIdle → 0x0 none → 0x4 SwPowerCap → 0x1
temperature 32 °C → 76 °C
power 48 W → 437 W (cap 400 W)
SM clock 210 → 1410 MHz 
A complete idle→active→throttle→cooldown transition. This is the
grounding trace for the thermal/power failure class.

## Limitations of this capture

Not obtainable on this platform:

- **Real Xid line format** — healthy GPU, no Xid fired. Parser must be
  written from NVIDIA documentation and public issue reports, then
  validated against real lines later.
- **Multi-GPU topology** — `topo -m` is 1×1. No peer relationships, so
  Layer 1 topology design has no real example to check against.
- **NVLink error fields** — links inactive, so DCGM does not enumerate
  them ("Not collecting NvLink metrics; no switches to monitor").
- **NCCL debug traces** — single GPU.
- **NVSwitch / fabric manager** — no switches present.

These require a 2+ GPU host with active NVLink and are deferred to a
follow-up capture session.
# Session 2 — Multi-GPU capture (Modal, 2× A100-SXM4-80GB)

Run to close the gaps left by Session 1: multi-GPU topology, active
NVLink, and a real NCCL trace. Partially successful — the sandbox
blocks more than expected, but the failures are themselves findings.

## Environment

| Field | Value |
|---|---|
| Date | 2026-09-17 |
| Platform | Modal (`modal shell --gpu a100-80gb:2 --pty`), gVisor sandbox |
| GPU | 2× NVIDIA A100-SXM4-80GB |
| Driver | 580.95.05, CUDA 13.0 |
| NCCL | 2.30.7+cuda13.3 |
| Interconnect | 12 NVLinks per GPU @ 25 GB/s, direct (no NVSwitch) |

Blocked by the sandbox: `dmesg`, `sysctl`, `/sys/bus/pci/devices/`,
Docker (so no dcgm-exporter, no `dcgmi`).

## Artifacts

| File | What it grounds |
|---|---|
| `modal_probe.txt` | `nvidia-smi`, `nvlink -s`, `nvlink -c` |
| `modal_topo.txt` | `topo -m` failure mode |
| `modal_nvml_nvlink_state.txt` | Per-link state + peer PCI query |
| `modal_nvml_nvlink_errors.txt` | Per-link error counter query |
| `modal_nccl_debug_min.txt` | Filtered NCCL trace (220 lines) |
| `modal_nccl_debug_head.txt` | Raw NCCL init phase (173 lines) |

## Findings

### 11. Thermal thresholds are device-reported, not universal
NVML on the Session-1 A100 returned slowdown 89 °C, shutdown 92 °C;
PCIe max gen4 ×16, current gen4 ×16; remapped rows `(0,0,0,0)`.

**Consequence:** read thresholds from the device. Never hardcode a
temperature limit. PCIe degradation is `current < max`, a comparison
that needs both fields.

### 12. NVLink peer identity is masked under virtualization
All 24 links reported `state=1` (active), but
`nvmlDeviceGetNvLinkRemotePciInfo` returned the sentinel
`FFFFFFFF:FF:FF.0` for every link.

**Consequence:** the topology builder must handle "link active, peer
unknown". Peer discovery cannot be assumed to succeed; fall back to
declared configuration.

### 13. NVLink error counters return NotSupported, not zero
`nvmlDeviceGetNvLinkErrorCounter` raised `NVMLError_NotSupported` for
CRC_FLIT, CRC_DATA, REPLAY and RECOVERY on all 24 links — while
`nvidia-smi nvlink -s` simultaneously reported all links up at 25 GB/s.

**Consequence:** this is the third independent instance of the
measured-absent vs not-measured distinction (see Findings 2 and 3).
A diagnoser reading these as zero would conclude "NVLink healthy,
therefore the NCCL timeout is a software problem" — confidently wrong,
and exactly the misleading-symptom case this project targets.
The three-state event schema is validated empirically, from three
different sources.

### 14. "Using network Socket" is not evidence of NVLink fallback
The NCCL log line `Using network Socket` refers to the NET plugin
selected for *inter-node* transport. With `nNodes 1`, that path is
never exercised. The actual data path appears elsewhere: 
Pattern 1, ... nChannels 12, bw 40.0/40.0, type NVL/PIX
Channel 00/0 : 0[0] -> 1[1] via P2P/CUMEM/read (×24)
Connected all rings, use ring PXN 0 GDR 1

**Consequence:** transport truth lives in `type NVL/…` and
`via P2P/…`, not in the NET plugin line. A parser keying on
"Using network" produces false NVLink-fault diagnoses.

### 15. NVLS multicast availability is a capability, not a fault
`NVLS multicast support is not available on dev 0/1` — NVLink SHARP
requires NVSwitch, absent on a directly-linked pair.

**Consequence:** collectives are slower than on an NVSwitch node
without anything being broken. Judging "is this slow?" requires
knowing the expected topology.

### 16. NCCL logs are a topology source
`Channel NN/24 : 0 1`, the `Trees` graph, and `busId 80000 / 80030`
expose the actual communication topology — recoverable even when
NVML peer discovery is blocked.

### 17. NCCL silently guesses topology when introspection fails 
Could not find real path of /sys/class/pci_bus/fffffff/../../fffffff:ff:f
Could not get device class for NVLink target fffffff:ff:ff.0, assuming NVSwitch
Could not get speed from /sys/class/net/eth0/speed. Defaulting to 10 Gbps. 
There is no NVSwitch on this node. NCCL hit the same masked sentinel
NVML did, assumed a switch, built its graph on that assumption, and
reported success with no flag in the graph output.

**Consequence:** this qualifies Finding 16. NCCL-derived topology is
low-confidence whenever `Could not …` / `assuming` / `Defaulting`
lines are present. Parse them as a confidence qualifier on everything
downstream.

## NCCL log format (parse targets) 
modal:36:36 [0] NCCL INFO ...
^host ^pid:tid ^rank ^level

[2026-09-17 10:33:34] modal:36:36 [0] misc/ibvwrap.cc:185 NCCL WARN ...
^WARN lines carry a timestamp and source:line; INFO lines do not 

Useful fields: `busId` (entity join key), `nNodes` / `localRanks`
(scope), `type NVL/PIX` (transport), `via P2P/CUMEM/read` (data path),
`Init timings - ...` (per-phase latency, a slow-init diagnostic),
`Destroy COMPLETE` (clean shutdown vs abort).

## Still outstanding

Requires bare metal with root:

- Real Xid line format in `dmesg`
- Populated NVLink error counters
- A working `nvidia-smi topo -m` matrix
- NVSwitch / fabric-manager telemetry

## Provider constraint on longitudinal capture

Each `modal shell` invocation lands on a different physical host —
GPU UUIDs differed between two runs minutes apart with an identical
`--gpu` spec. Nothing captured from an ephemeral-sandbox provider can
be correlated across sessions.

This rules out more than convenience: entity history, precursor
windows and lemon-node signals all require the same node observed over
time. Those need bare metal with persistent identity, not just root
access.

### 18. NUMA affinity is unavailable, and NCCL proceeds anyway
NCCL INFO Topology detection: could not read
/sys/devices/system/node/node4294967295/cpumap, using empty affinity 

`4294967295` is `0xFFFFFFFF`, `(uint32) -1` — the kernel's "no NUMA
node" sentinel. NCCL could not resolve it, substituted empty affinity,
and logged the substitution at INFO level.

This is the sixth instance of the same pattern, and the fourth distinct
vocabulary for it:

| Finding | Source | How "unknown" is expressed |
|---|---|---|
| 2 | dcgm-exporter | field silently absent |
| 3 | DCGM counters | reads 0, never measured |
| 12 | NVML | peer PCI = `FFFFFFFF:FF:FF.0` |
| 13 | NVML | raises `NotSupported` |
| 17 | NCCL | "assuming NVSwitch" |
| 18 | NCCL | "using empty affinity" |

**Consequence:** Findings 17 and 18 differ in one useful respect. F17
guesses silently; F18 *states* its substitution. Phrases of the form
"using empty/default X" are a greppable admission that X was never
measured, and belong in the NCCL parser as `NOT_MEASURED` markers
rather than being discarded as INFO noise.

### A hazard specific to preparing captures for publication

Two scrubber rules, added in good faith, destroyed telemetry: 
0x[12+ hex] -> 0xADDR killed clocks_event_reasons
(Finding 3's evidence)

[0-9a-f]{12} catchall killed node4294967295
(Finding 18's evidence) 

This is not coincidence. Sentinel values *look like* identifiers —
all-Fs, max-uint, long hex runs — so the exact values this project
exists to detect are the ones a naive scrubber targets.

An audit of the second rule found it matched **zero** real container
IDs across all 21 artifacts (those are caught by the dedicated
`Hostname="<12hex>"` rule) while damaging four telemetry sites. It was
removed rather than narrowed: a rule that fires only on telemetry is
worse than no rule.

**Rule for future scrubbing:** sentinel values are telemetry, not
identifiers. Audit what a rule actually matches before adding it.
