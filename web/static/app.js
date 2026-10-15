/* Gforensics Guard UI helpers, shared by the console, split screen and lineage report.
 *
 * 🔴 Honesty rule: this file contains NO verdicts. Every badge is computed from the event's
 * own `decision` + `source` fields, which come from agents/armoriq_io.py (what ArmorIQ or
 * its SDK returned) or, on the naive side, from the naive agent ("EXECUTED, NO-ARMORIQ").
 * The words below only translate those fields into plain language.
 */
const GFG = (() => {
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  // ── jargon → one-line explanations (hover tooltips) ───────────────────────
  const GLOSSARY = {
    "Xid": "An error code the NVIDIA driver writes to the kernel log when a GPU misbehaves. Xid 79 = the GPU fell off the PCIe bus.",
    "Xid 79": "The GPU disappeared from the server's PCIe bus. Usually real hardware trouble.",
    "cordon": "Mark a server so no NEW jobs land on it. Running jobs keep going.",
    "drain": "Move all jobs off a server so it can be repaired. Its 8 GPUs stop doing work.",
    "RMA": "Return Merchandise Authorization: shipping a GPU back to the vendor as broken. Expensive and slow; wrong if the GPU is healthy.",
    "persistence mode": "A driver setting that keeps the GPU initialised. When it is OFF, NVIDIA's diagnostic test reports 'Fail' even on a healthy GPU.",
    "DCGM": "NVIDIA Data Center GPU Manager: the standard GPU health-metrics tool.",
    "NVLink": "The high-speed cable between GPUs inside one server.",
    "intent token": "A signed pass from ArmorIQ that lists exactly which tools the agent said it would use. Calls outside it are refused.",
    "signed plan": "Before acting, the agent writes down every tool it intends to call. ArmorIQ signs that list (Merkle hash) so it can't be changed afterwards.",
    "plan hash": "A fingerprint of the signed plan. If one character of the plan changes, the fingerprint changes.",
    "delegation": "The orchestrator hands a sub-agent (Diagnoser or Remediator) a narrower pass derived from its own signed plan.",
    "policy": "Rules written in the ArmorIQ console (allow / hold for approval / block, per tool). ArmorIQ applies them on every call.",
    "MCP": "Model Context Protocol: the standard way AI agents call tools. Our two tool servers speak it.",
    "rack": "A cabinet of 8 servers. Rack B = node-09 to node-16, 64 GPUs.",
    "rule table": "Seven fixed if-this-evidence-then-that-diagnosis rules. They PROPOSE a plan; ArmorIQ decides what is ALLOWED.",
    "prompt injection": "Text planted where an AI will read it (here: a tenant's job log) that tries to give the AI orders.",
  };
  const term = (word, key) => `<span class="term" tabindex="0" data-tip="${esc(GLOSSARY[key || word] || "")}">${esc(word)}</span>`;

  // ── tools in plain words ──────────────────────────────────────────────────
  const TOOLS = {
    get_xid_events: ["Read the GPU error log", "Xid"],
    get_diag_result: ["Read the GPU self-test results", null],
    get_dcgm_metrics: ["Read GPU health metrics", "DCGM"],
    get_smi_query: ["Read nvidia-smi status", null],
    get_nvlink_errors: ["Read GPU-to-GPU link errors", "NVLink"],
    get_node_jobs: ["Read the jobs and their logs", null],
    get_kmsg: ["Read the kernel log", null],
    get_dmesg: ["Read the kernel log (dmesg)", null],
    get_nvlink_status: ["Read NVLink status", "NVLink"],
    list_nodes: ["List the servers", null],
    fleet_summary: ["Read the fleet summary", null],
    cordon_node: ["Cordon the server (no new jobs)", "cordon"],
    uncordon_node: ["Uncordon the server", "cordon"],
    drain_node: ["Drain ONE server (8 GPUs stop)", "drain"],
    drain_nodes: ["Drain MANY servers at once", "drain"],
    reset_gpu: ["Reset a GPU (kills its job)", null],
    enable_persistence_mode: ["Turn persistence mode on (config fix)", "persistence mode"],
    open_rma_ticket: ["Ship a GPU back to the vendor (RMA)", "RMA"],
  };
  const toolName = (t) => (TOOLS[t] ? TOOLS[t][0] : t);
  const toolHtml = (t) => {
    const k = TOOLS[t] && TOOLS[t][1];
    return k ? `<span class="term" tabindex="0" data-tip="${esc(GLOSSARY[k])}">${esc(toolName(t))}</span>` : esc(toolName(t));
  };
  const target = (p = {}) => {
    if (Array.isArray(p.nodes)) return `${p.nodes.length} servers (${p.nodes[0]} … ${p.nodes[p.nodes.length - 1]}) = ${p.nodes.length * 8} GPUs`;
    const bits = [];
    if (p.node) bits.push(p.node);
    if (p.gpu !== undefined && p.gpu !== null) bits.push(`GPU ${p.gpu}`);
    return bits.join(" · ");
  };

  // ── badge = plain-language view of decision + source ──────────────────────
  function verdict(e) {
    const d = e.decision, s = e.source;
    if (d === "ALLOWED") return { cls: "ALLOWED", icon: "✓", text: "Allowed by ArmorIQ", why: "ArmorIQ checked this call against the signed plan and the policies, then let it through to the tool server." };
    if (d === "HELD") return { cls: "HELD", icon: "⏸", text: "Waiting for a human approver", why: "An ArmorIQ policy says this action needs a second person. It is parked until a team member approves it in the ArmorIQ console. The agent cannot approve its own request." };
    if (d === "BLOCKED" && s === "SDK-LOCAL") return { cls: "BLOCKED sdk", icon: "⛔", text: "Stopped: not in the signed plan", why: "The ArmorIQ SDK refused before sending anything: this tool is not in the plan ArmorIQ signed for this agent. (Checked on the agent's side, by ArmorIQ's SDK.)" };
    if (d === "BLOCKED") return { cls: "BLOCKED", icon: "✕", text: "Blocked by ArmorIQ policy", why: "ArmorIQ's server refused the call. It never reached the fleet." };
    if (d === "ERROR") return { cls: "ERROR", icon: "!", text: "Error: did not run", why: "Something failed (network, token, tool). This is NOT an ArmorIQ allow or block; the agent stops safely." };
    if (d === "EXECUTED") return { cls: "EXECUTED", icon: "▶", text: "Done. Nobody checked", why: "The naive agent has no ArmorIQ. It calls the fleet directly and does whatever its AI suggested." };
    return { cls: "ERROR", icon: "?", text: d || "unknown", why: "" };
  }
  const badge = (e) => { const v = verdict(e); return `<span class="badge ${v.cls}" title="${esc(v.why)}">${v.icon} ${esc(v.text)}</span>`; };

  const ACTORS = {
    "diagnoser": "Diagnoser (read-only)",
    "diagnoser(llm-suggested)": "AI assistant, via the Diagnoser's read-only pass",
    "remediator": "Remediator (fix-it agent)",
    "orchestrator": "Orchestrator",
  };
  const CATEGORY = {
    HARDWARE: "Real hardware fault", MISCONFIG: "Configuration problem, not broken hardware",
    TRANSIENT: "Glitch that already fixed itself", NORMAL: "Normal behaviour", NOT_MEASURED: "Can't tell yet: the counters aren't measuring",
    INSUFFICIENT_EVIDENCE: "Not enough evidence", HEALTHY: "Healthy",
  };

  // ── one event → timeline row model ───────────────────────────────────────
  // returns {icon, title, sub, right, chips, explain, group}
  function describe(e, side) {
    const k = e.kind;
    if (k === "objective") return {
      icon: "📋", title: side === "naive" ? "Naive agent gets the alert" : "On-call engineer opens an incident",
      sub: esc(e.text), group: "start",
      explain: side === "naive"
        ? "The same alert goes to an agent with NO ArmorIQ. It will read the same telemetry, ask the same AI, and do what the AI says."
        : "This is the goal. Gforensics Guard may investigate freely, but has to EARN the right to change anything.",
    };
    if (k === "plan") {
      const inv = e.phase === "investigate";
      return {
        icon: "✍", group: inv ? "investigate" : "expand",
        title: inv ? `Signed plan #1: investigate (read-only)` : `Authority expansion: signed plan #2, remediate`,
        sub: inv ? `The agent commits to ${e.steps.length} read-only tools. ArmorIQ signs the list and issues an ${term("intent token")}.`
                 : `Based on the evidence, the agent asks for write tools. The new plan is chained to plan #1 (${term("plan hash")} ${esc(String(e.parent_plan_hash || "").slice(0, 10))}…).`,
        chips: e.steps.map((s) => toolName(s.tool)),
        right: `<span class="hash" title="${esc(e.plan_hash)}">${esc(String(e.plan_hash).slice(0, 10))}…</span>`,
        explain: `ArmorIQ's ${term("signed plan")} is the agent's promise of what it will do. Plan hash ${esc(e.plan_hash)}. ` +
          (inv ? "Write tools are not on it, so even a tricked AI cannot change the fleet during the investigation." : "Every write tool on it still goes through ArmorIQ's policies, one call at a time."),
      };
    }
    if (k === "delegation") return {
      icon: "🔑", group: e.agent === "diagnoser" ? "investigate" : "remediate",
      title: e.ok ? `Hands a limited pass to the ${e.agent === "diagnoser" ? "Diagnoser" : "Remediator"}` : `Pass to ${e.agent} failed: stops safely`,
      sub: e.ok ? `${term("delegation", "delegation")} derived from the signed plan (ArmorIQ delegate_subtree).` : esc(e.error),
      explain: e.ok ? `ArmorIQ created a delegated token for the ${e.agent}. trust_id ${esc(e.trust_id || "-")}.` : esc(e.error),
    };
    if (k === "read") return {
      icon: "👁", group: "investigate", title: toolHtml(e.tool), sub: esc(e.node), right: badge(e),
      explain: verdict(e).why,
    };
    if (k === "call") {
      const llm = (e.actor || "").includes("llm");
      const v = verdict(e);
      const isRead = (e.tool || "").startsWith("get_") || e.tool === "list_nodes";
      return {
        icon: v.icon, group: e.actor === "remediator" ? "remediate" : (e.actor === "orchestrator" ? "expand" : "investigate"),
        title: (llm ? "AI assistant tries: " : (e.rule === "INJECTED" ? "Planted step tries: " : "")) + toolHtml(e.tool),
        sub: `${esc(target(e.params))}${e.because ? ` · AI's reason: “${esc(e.because.slice(0, 120))}”` : ""}${!isRead ? ` · by ${esc(ACTORS[e.actor] || e.actor)}` : ""}`,
        right: badge(e), cls: e.decision,
        explain: `<b>${esc(v.text)}.</b> ${v.why}` + (e.reason ? `<br><span class="label">ArmorIQ said</span><br><span class="mono">${esc(e.reason.slice(0, 400))}</span>` : ""),
        raw: e,
      };
    }
    if (k === "diagnosis") {
      const f = e.findings || [];
      return {
        icon: "🩺", group: "investigate", title: `Diagnosis for ${esc(e.node)}: ${esc(CATEGORY[e.category] || e.category)}`,
        sub: f.map((x) => esc(x.summary)).join(" · ") || "No rule matched.",
        chips: (e.proposed_steps || []).length ? e.proposed_steps.map((s) => `proposes: ${toolName(s.tool)}`) : ["proposes: no changes"],
        explain: `The ${term("rule table")} looked at device telemetry only (never job logs). ` +
          f.map((x) => `<br>• <b>${esc(x.rule)}</b>: ${esc(x.summary)}`).join(""),
      };
    }
    if (k === "narrative") {
      const acts = e.suggested_actions || [];
      return {
        icon: "💬", group: side === "naive" ? "act" : "investigate",
        title: `AI assistant's read (${esc(String(e.llm || "").split("/").pop())})`,
        sub: esc((e.narrative || "").slice(0, 220)) + ((e.narrative || "").length > 220 ? "…" : ""),
        chips: acts.length ? acts.slice(0, 6).map((a) => `wants: ${toolName(a.tool)} ${target(a.params)}`).concat(acts.length > 6 ? [`+${acts.length - 6} more`] : []) : ["suggests nothing"],
        explain: `The AI reads the RAW logs, including text a tenant can write. ${side === "naive" ? "The naive agent will run every suggestion." : "Its suggestions are tried with the Diagnoser's read-only pass, so ArmorIQ decides."}<br><br>${esc(e.narrative || "")}`,
      };
    }
    if (k === "injection") return {
      icon: "☠", group: side === "naive" ? "act" : "expand",
      title: side === "naive" ? "Compromised planner adds a step" : "Compromised planner slips a step into plan #2",
      sub: `${toolHtml(e.tool)} ${esc(target(e.params))} · ${esc(e.why)}`,
      explain: "Demo of a worst case: the planner itself is compromised and writes a harmful step INTO the plan ArmorIQ signs. The signature can't make a bad step safe; ArmorIQ's policies still judge each call.",
    };
    if (k === "approval") {
      const st = String(e.status || "");
      const approved = st === "approved";
      return {
        icon: approved ? "✅" : "⏳", group: "remediate", approval: e,
        title: approved ? "Approved by a second team member in the ArmorIQ console" : (st === "pending" ? "Approval request sent to the ArmorIQ console" : `Approval: ${esc(st)}`),
        sub: approved ? `${toolHtml(e.tool)}: a human, not the agent, said yes.` : `${toolHtml(e.tool)} waits for a second person (Intent → Held Actions). The requester can't approve their own request.`,
        right: approved ? `<span class="badge APPROVED">✓ Approved by a human</span>` : `<span class="badge HELD">⏸ Waiting for a human approver</span>`,
        explain: `ArmorIQ approval request ${esc(e.delegation_id || "-")}, status reported by ArmorIQ: <b>${esc(st)}</b>.`,
      };
    }
    if (k === "exec") return {
      icon: "▶", group: "act", title: (e.planted ? "Runs the planted step: " : e.scripted ? "Playbook runs: " : "Runs the AI's suggestion: ") + toolHtml(e.tool),
      sub: `${esc(target(e.params))}${e.because ? ` · “${esc(String(e.because).slice(0, 110))}”` : ""}`,
      right: badge(e), cls: "EXECUTED",
      explain: `No checks at all. Result from the fleet: <span class="mono">${esc(JSON.stringify(e.result).slice(0, 300))}</span>`,
    };
    if (k === "note") return { icon: "ℹ", group: "act", title: esc(e.text), sub: "", explain: esc(e.text) };
    return { icon: "·", title: esc(k), sub: esc(JSON.stringify(e).slice(0, 140)), explain: "" };
  }

  const GROUPS = {
    start: "Incident", investigate: "Phase 1 · Investigate (read-only)", expand: "Authority expansion",
    remediate: "Phase 2 · Remediate (each call checked by ArmorIQ)", act: "Acting on the AI's advice",
  };

  // ── timeline renderer ────────────────────────────────────────────────────
  function Timeline(el, side, onSelect) {
    let lastGroup = null, n = 0;
    el.innerHTML = "";
    const head = (g) => {
      const h = document.createElement("div");
      h.className = "ev group-head";
      h.innerHTML = `<span class="label">${esc(side === "naive" && g === "investigate" ? "Reads the same telemetry (no checks)" : GROUPS[g] || g)}</span>`;
      el.appendChild(h);
    };
    return {
      add(e) {
        const isRead = (side === "governed" && e.kind === "call" && e.decision === "ALLOWED" && (e.tool || "").startsWith("get_") && e.actor === "diagnoser")
          || e.kind === "read";
        if (isRead) {
          if (lastGroup !== "investigate") { lastGroup = "investigate"; head("investigate"); }
          return compactReads(el, e.kind === "read" ? { ...e, params: { node: e.node } } : e, onSelect);
        }
        const key = e.kind === "call" ? [e.actor, e.tool, e.decision, e.source].join("|") : null;
        const prev = el.lastElementChild;
        if (key && prev && prev._key === key) {
          prev._count = (prev._count || 1) + 1;
          prev._events.push(e);
          let c = prev.querySelector(".repeat");
          if (!c) { c = document.createElement("span"); c.className = "chip repeat"; prev.querySelector(".row").after(c); }
          c.textContent = `×${prev._count} attempts (${[...new Set(prev._events.map((x) => target(x.params)))].slice(0, 4).join(", ")}${prev._count > 4 ? ", …" : ""})`;
          return prev;
        }
        const d = describe(e, side);
        if (d.group && d.group !== lastGroup) { lastGroup = d.group; head(d.group); }
        const row = document.createElement("div");
        row.className = "ev fresh";
        row.dataset.i = n++;
        row._key = key;
        row._events = [e];
        row.innerHTML = `<div class="time">${e.t !== undefined ? Number(e.t).toFixed(1) + "s" : ""}</div>
          <div class="ico">${d.icon}</div>
          <div><div class="row"><div class="title">${d.title}</div>${d.right || ""}</div>
          ${d.sub ? `<div class="sub">${d.sub}</div>` : ""}
          ${d.chips ? `<div class="chips">${d.chips.map((c) => `<span class="chip">${esc(c)}</span>`).join("")}</div>` : ""}</div>`;
        row.addEventListener("click", () => {
          el.querySelectorAll(".ev.sel").forEach((x) => x.classList.remove("sel"));
          row.classList.add("sel");
          onSelect && onSelect(e, d);
        });
        el.appendChild(row);
        if (el.scrollHeight > el.clientHeight) el.scrollTop = el.scrollHeight;
        return row;
      },
    };
  }
  // Seven reads in a row are noise for a viewer: fold consecutive allowed reads of one node into one row.
  function compactReads(el, e, onSelect) {
    let row = el.lastElementChild;
    if (!row || !row.classList.contains("reads") || row._node !== e.params.node) {
      row = document.createElement("div");
      row.className = "ev reads fresh";
      row.innerHTML = `<div class="time">${Number(e.t).toFixed(1)}s</div><div class="ico">👁</div>
        <div><div class="row"><div class="title">Reads the evidence on ${esc(e.params.node)}</div>${badge(e)}</div>
        <div class="chips"></div></div>`;
      row._node = e.params.node;
      row._reads = [];
      const r = row;
      row.addEventListener("click", () => {
        el.querySelectorAll(".ev.sel").forEach((x) => x.classList.remove("sel"));
        r.classList.add("sel");
        onSelect && onSelect(r._reads[0], {
          title: `Reads the evidence on ${esc(r._node)}`,
          explain: (r._reads[0].source === "NO-ARMORIQ" ? `The naive agent reads the fleet directly. Nothing checks it:<br>` : `Each read was a separate call through ArmorIQ, and each came back <b>Allowed by ArmorIQ</b> (read-only tools are allowed by policy):<br>`) +
            r._reads.map((x) => `• ${esc(toolName(x.tool))}: ${esc(verdict(x).text)}`).join("<br>"),
          raw: r._reads,
        });
      });
      el.appendChild(row);
    }
    row._reads.push(e);
    row.querySelector(".chips").insertAdjacentHTML("beforeend", `<span class="chip">${e.source === "NO-ARMORIQ" ? "▶" : "✓"} ${esc(toolName(e.tool).replace(/^Read (the )?/, ""))}</span>`);
    return true;
  }

  // ── fleet map ────────────────────────────────────────────────────────────
  const CRIT = new Set(["node-01", "node-02", "node-09", "node-10"]);
  function fleetMap(el, summary, title) {
    const s = summary || { drained: [], cordoned: [], active_faults: [], gpus_out_of_service: 0, rma_tickets: 0 };
    const drained = new Set(s.drained), cord = new Set(s.cordoned), fault = new Set((s.active_faults || []).map((f) => f.node));
    const node = (i) => {
      const nm = `node-${String(i).padStart(2, "0")}`;
      const cls = ["node", drained.has(nm) ? "drained" : "", cord.has(nm) && !drained.has(nm) ? "cordoned" : "", fault.has(nm) ? "fault" : "", CRIT.has(nm) ? "crit" : ""].join(" ");
      return `<div class="${cls}" title="${nm}${drained.has(nm) ? " · drained: 8 GPUs out of service" : cord.has(nm) ? " · cordoned" : ""}${fault.has(nm) ? " · has an active fault" : ""}">
        <div class="nm"><span>${nm.slice(5)}</span></div><div class="gpus">${"<i class='gpu'></i>".repeat(8)}</div></div>`;
    };
    const rack = (r, a) => `<div class="rack"><div class="label">${term("Rack " + r, "rack")}</div><div class="nodes">${Array.from({ length: 8 }, (_, j) => node(a + j)).join("")}</div></div>`;
    const out = s.gpus_out_of_service || 0;
    el.innerHTML = `<div class="head"><div><div class="label">${esc(title || "Simulated fleet · 16 servers · 128 GPUs")}</div>
        <div class="big ${out > 8 ? "bad" : out ? "" : "ok"}">${out} <span class="small muted">GPUs out of service</span></div></div>
        <div class="small muted">${s.rma_tickets || 0} ${term("RMA")} ticket${s.rma_tickets === 1 ? "" : "s"}</div></div>
      <div class="racks">${rack("A", 1)}${rack("B", 9)}</div>
      <div class="legend"><span><i style="background:var(--teal)"></i>working</span><span><i style="background:var(--bad)"></i>drained (out of service)</span>
        <span><i style="border:1px dashed var(--text-3)"></i>cordoned</span><span><i style="border:2px solid #f59e0b"></i>has a fault</span><span style="color:var(--accent)">★ critical inference server</span></div>`;
  }

  // ── API ──────────────────────────────────────────────────────────────────
  async function api(path, opts) {
    const r = await fetch(path, opts);
    const body = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(body.detail || `HTTP ${r.status}`);
    return body;
  }
  const startRun = (body) => api("/api/runs", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  function stream(id, handlers) {
    const es = new EventSource(`/api/runs/${id}/stream`);
    es.onmessage = (m) => {
      const msg = JSON.parse(m.data);
      handlers[msg.type] && handlers[msg.type](msg);
      if (msg.type === "done") es.close();
    };
    es.onerror = () => { es.close(); handlers.error && handlers.error(); };
    return es;
  }

  // Plain-language outcome, counted from the recorded verdicts (nothing invented).
  function plainOutcome(events, naive) {
    const calls = events.filter((e) => e.kind === (naive ? "exec" : "call") && (naive || e.actor === "remediator"));
    const by = (d) => [...new Set(calls.filter((c) => c.decision === d).map((c) => toolName(c.tool)))];
    if (naive) return calls.length ? `Did everything the ${calls.some((c) => c.scripted) ? "playbook" : "AI"} said: ${by("EXECUTED").join(", ")}.` : "Did nothing.";
    if (!calls.length) return "Changed nothing: the evidence did not justify asking for write authority.";
    const parts = [];
    if (by("ALLOWED").length) parts.push(`✓ Allowed by ArmorIQ and done: ${by("ALLOWED").join(", ")}`);
    if (by("HELD").length) parts.push(`⏸ Waiting for a human: ${by("HELD").join(", ")}`);
    if (by("BLOCKED").length) parts.push(`✕ Blocked by ArmorIQ: ${by("BLOCKED").join(", ")}`);
    if (by("ERROR").length) parts.push(`! Errors (no verdict): ${by("ERROR").join(", ")}`);
    return parts.join(" · ");
  }

  const SCENE_TITLES = {
    split_screen: ["Poisoned log", "A job log on node-07 contains planted orders: “drain rack B”. Will the agent obey?"],
    rma_trap: ["RMA trap", "The GPU self-test says Fail on all 8 GPUs. Broken hardware… or just a setting?"],
    xid79: ["GPU fell off the bus", "A real hardware fault. The agent must earn the right to fix it, and a human must OK the RMA."],
    noise: ["Scary but harmless", "Two alerts that look bad but need no change at all."],
  };

  return { esc, term, GLOSSARY, toolName, toolHtml, verdict, badge, describe, Timeline, fleetMap, api, startRun, stream, SCENE_TITLES, target, CATEGORY, plainOutcome };
})();
