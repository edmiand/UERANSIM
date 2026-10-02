(() => {
  "use strict";

  const state = {
    gnb: { state: "stopped", fields: {} },
    ues: [],
    // per-UE data plane buffers, keyed by UE id
    console: {},
    udp: {},
    cmdBusy: {},
    // UE ids whose PDU session was released from this dashboard; cleared when a
    // TUN-up line arrives again (the UE stays "attached" after ps-release-all)
    pduReleased: {},
    logs: [],
  };

  // ── per-viewer preferences ───────────────────────────────────────────────
  const prefs = { details: false, tab: "term", logView: "hi" };
  try { Object.assign(prefs, JSON.parse(localStorage.getItem("ueransim-web-prefs") || "{}")); } catch (_) { /* ignore */ }
  function savePrefs() {
    try { localStorage.setItem("ueransim-web-prefs", JSON.stringify(prefs)); } catch (_) { /* ignore */ }
  }

  // ── REST helpers ─────────────────────────────────────────────────────────
  async function api(method, path, body) {
    const res = await fetch(path, {
      method,
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
    if (!res.ok) {
      let msg = res.statusText;
      try { const j = await res.json(); if (j.error) msg = j.error; } catch (_) { /* ignore */ }
      throw new Error(msg);
    }
    if (res.status === 204) return null;
    const text = await res.text();
    return text ? JSON.parse(text) : null;
  }

  // ── WebSocket ────────────────────────────────────────────────────────────
  function connectWs() {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    const ws = new WebSocket(`${proto}//${location.host}/ws`);

    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.type === "snapshot") {
        state.gnb = msg.gnb;
        state.ues = msg.ues;
        state.console = msg.console || {};
        state.udp = msg.udp || {};
        state.cmdBusy = {};
        for (const u of msg.ues) state.cmdBusy[u.id] = !!u.cmdBusy;
        state.logs = [];
        msg.logs.forEach((e) => addLog(e, false));
        renderAll();
        renderDataPlane(true);
        renderLogs();
      } else if (msg.type === "gnb_state") {
        state.gnb.state = msg.state;
        renderAll();
      } else if (msg.type === "ue_state") {
        const idx = state.ues.findIndex((u) => u.id === msg.id);
        const snap = { id: msg.id, name: msg.name, imsi: msg.imsi, state: msg.state,
                       regState: msg.regState, rrcState: msg.rrcState, connected: msg.connected, iface: msg.iface,
                       ip: msg.ip, udpPort: msg.udpPort, udpListening: msg.udpListening,
                       httpListening: msg.httpListening };
        if (idx === -1) state.ues.push(snap); else state.ues[idx] = snap;
        if (msg.state !== "attached") delete state.pduReleased[msg.id];
        renderAll();
      } else if (msg.type === "log") {
        addLog(msg, true);
      } else if (msg.type === "ue_console") {
        (state.console[msg.id] ||= []).push({ kind: msg.kind, text: msg.text });
        if (msg.kind === "cmd") state.cmdBusy[msg.id] = true;
        if (msg.kind === "exit") state.cmdBusy[msg.id] = false;
        trimBuffer(state.console[msg.id], DP_CONSOLE_MAX);
        if (dpTarget() && dpTarget().id === msg.id) {
          appendConsoleLine(msg);
          renderDataPlaneControls();
        }
      } else if (msg.type === "ue_udp") {
        (state.udp[msg.id] ||= []).push({ time: msg.time, proto: msg.proto, src: msg.src,
                                          text: msg.text, incident: msg.incident });
        trimBuffer(state.udp[msg.id], DP_UDP_MAX);
        if (dpTarget() && dpTarget().id === msg.id) appendUdpMessage(msg);
        renderInboxBadge();
      }
    };

    ws.onclose = () => setTimeout(connectWs, 1500);
  }

  function escapeHtml(s) {
    const d = document.createElement("div");
    d.textContent = s == null ? "" : String(s);
    return d.innerHTML;
  }

  function trimBuffer(buf, max) {
    while (buf.length > max) buf.shift();
  }

  // ── activity log ─────────────────────────────────────────────────────────
  const LOG_MAX = 300;

  // Plain-language summaries for the log lines a newcomer cares about. Lines that
  // match nothing only show in "Full log"; failures always become highlights.
  const HIGHLIGHTS = [
    { src: "gnb", re: /Trying to establish SCTP connection/, tone: "busy", text: () => "gNB is contacting the core network…" },
    { src: "gnb", re: /NG Setup procedure is successful/, tone: "ok", text: () => "gNB linked to the core network" },
    { src: "ue", re: /Selected cell/, tone: "busy", text: () => "Phone found the gNB cell" },
    { src: "ue", re: /RRC connection established/, tone: "ok", text: () => "Phone connected over radio" },
    { src: "ue", re: /Initial Registration is successful/, tone: "ok", text: () => "Phone registered with the network" },
    { src: "ue", re: /TUN interface\[([^,\]]+),\s*([^\]]+)\] is up/, tone: "ok", text: (m) => `Phone is online with address ${m[2].trim()}` },
    { src: "ue", re: /PDU session release|PDU Session Release/i, tone: "idle", text: () => "Internet session closed" },
    { src: "ue", re: /De-?registration.*(success|complete)/i, tone: "idle", text: () => "Phone signed off the network" },
  ];

  function srcKind(e) { return e.source === "gnb" ? "gnb" : e.source && e.source.startsWith("ue:") ? "ue" : "web"; }

  function addLog(e, live, preset) {
    const kind = srcKind(e);
    let hi = null;
    let tone = "idle";
    if (preset) {
      ({ hi, tone } = preset);
    } else if (e.level === "fail") {
      hi = e.text; tone = "bad";
    } else {
      for (const rule of HIGHLIGHTS) {
        if (rule.src !== kind) continue;
        const m = e.text.match(rule.re);
        if (m) { hi = rule.text(m); tone = rule.tone; break; }
      }
    }
    if (kind === "ue" && /TUN interface\[/.test(e.text)) {
      const ueId = Number(e.source.split(":")[1]);
      if (state.pduReleased[ueId]) { delete state.pduReleased[ueId]; if (live) renderAll(); }
    }
    const entry = { time: e.time, tag: e.tag, color: e.color, text: e.text, kind, hi, tone };
    state.logs.push(entry);
    trimBuffer(state.logs, LOG_MAX);
    if (live) appendLogEntry(entry);
  }

  // Dashboard-side events (button presses) shown alongside node logs.
  function note(text, tone) {
    const t = new Date();
    const p = (n) => String(n).padStart(2, "0");
    addLog({ source: "web", tag: "[web]", color: "#8C96A8",
             time: `${p(t.getHours())}:${p(t.getMinutes())}:${p(t.getSeconds())}`, text },
           true, { hi: text, tone: tone || "idle" });
  }

  const SRC_NAME = { gnb: "gNB", ue: "Phone", web: "Dashboard" };

  function hiItem(e) {
    const li = document.createElement("li");
    li.innerHTML =
      `<span class="hi-dot ${e.tone}"></span>` +
      `<div><div class="hi-text${e.tone === "bad" ? " bad" : ""}">${escapeHtml(e.hi)}</div>` +
      `<div class="hi-meta">${escapeHtml(e.time)} · ${escapeHtml(SRC_NAME[e.kind])}</div></div>`;
    return li;
  }

  function rawItem(e) {
    const div = document.createElement("div");
    div.innerHTML =
      `<span class="term-time">${escapeHtml(e.time)}</span> ` +
      `<span style="color:${escapeHtml(e.color)}">${escapeHtml(e.tag)}</span> ` +
      escapeHtml(e.text);
    return div;
  }

  function hiEmpty() {
    const li = document.createElement("li");
    li.className = "hi-empty";
    li.textContent = "Nothing yet — start the gNB, then power on the phone.";
    return li;
  }

  // Highlights are newest-first; the raw log reads top-to-bottom like a terminal.
  function renderLogs() {
    const hiEl = document.getElementById("log-hi");
    const rawEl = document.getElementById("log-raw");
    hiEl.innerHTML = "";
    rawEl.innerHTML = "";
    const his = state.logs.filter((e) => e.hi);
    if (his.length) his.slice().reverse().forEach((e) => hiEl.appendChild(hiItem(e)));
    else hiEl.appendChild(hiEmpty());
    state.logs.forEach((e) => rawEl.appendChild(rawItem(e)));
    rawEl.scrollTop = rawEl.scrollHeight;
  }

  function appendLogEntry(e) {
    const hiEl = document.getElementById("log-hi");
    const rawEl = document.getElementById("log-raw");
    if (e.hi) {
      hiEl.querySelector(".hi-empty")?.remove();
      hiEl.insertBefore(hiItem(e), hiEl.firstChild);
      while (hiEl.children.length > LOG_MAX) hiEl.removeChild(hiEl.lastChild);
    }
    const atBottom = rawEl.scrollHeight - rawEl.scrollTop - rawEl.clientHeight < 24;
    rawEl.appendChild(rawItem(e));
    while (rawEl.children.length > LOG_MAX) rawEl.removeChild(rawEl.firstChild);
    if (atBottom) rawEl.scrollTop = rawEl.scrollHeight;
  }

  // ── derived state ────────────────────────────────────────────────────────
  // The dashboard manages a single fixed UE (see server.py UE_NAME).
  function theUe() { return state.ues[0] || null; }

  function isOnline(ue) {
    return !!(ue && ue.connected && ue.ip && !state.pduReleased[ue.id]);
  }

  // ── rendering ────────────────────────────────────────────────────────────
  function renderAll() {
    renderOverall();
    renderMap();
    renderDetails();
    renderDataPlane(false);
  }

  function setTone(el, tone) {
    el.classList.remove("tone-busy", "tone-ok", "tone-bad");
    if (tone && tone !== "idle") el.classList.add("tone-" + tone);
  }

  function renderOverall() {
    const g = state.gnb.state;
    const ue = theUe();
    let label = "Offline", tone = "idle";
    if (g === "failed" || (ue && ue.state === "failed")) { label = "Problem detected"; tone = "bad"; }
    else if (g === "starting" || (ue && ue.state === "starting")) { label = "Connecting…"; tone = "busy"; }
    else if (isOnline(ue)) { label = "Phone online"; tone = "ok"; }
    else if (g === "attached") { label = "Network ready"; tone = "ok"; }
    document.getElementById("overall-label").textContent = label;
    setTone(document.getElementById("overall"), tone);
  }

  const ICONS = {
    ue: '<rect x="6" y="2.5" width="12" height="19" rx="2.5"/><path d="M11 18.5h2"/>',
    gnb: '<path d="M4.9 8a10 10 0 0 0 0 8M19.1 8a10 10 0 0 1 0 8M7.8 10a5 5 0 0 0 0 4M16.2 10a5 5 0 0 1 0 4"/><circle cx="12" cy="12" r="1.5"/><path d="m12 13.5-3 8M12 13.5l3 8M10 18h4"/>',
    core: '<rect x="3" y="4" width="18" height="7" rx="2"/><rect x="3" y="13" width="18" height="7" rx="2"/><path d="M7 7.5h.01M7 16.5h.01"/>',
    net: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/>',
  };

  // Short identity line for the gNB card; the full field list lives in Technical details.
  function gnbTech() {
    const f = state.gnb.fields || {};
    const parts = ["nr-gnb-01"];
    if (f.mcc != null && f.mnc != null) parts.push(`PLMN ${f.mcc}/${f.mnc}`);
    if (f.tac != null) parts.push(`TAC ${f.tac}`);
    if (f.nci != null) parts.push(`NCI ${f.nci}`);
    return parts.join(" · ");
  }

  function buildNodes() {
    const g = state.gnb.state;
    const gnbOn = g === "attached";
    const ue = theUe();
    const us = ue ? ue.state : "stopped";
    const ueOn = us === "attached";
    const online = isOnline(ue);
    const amf = (state.gnb.fields && state.gnb.fields.amfAddress) || "—";

    const ueStatus = {
      stopped: ["Off", "idle"], starting: ["Joining…", "busy"], failed: ["Failed", "bad"],
      attached: online ? ["Online", "ok"] : ["Registered, no data", "busy"],
    }[us] || ["Off", "idle"];
    const gnbStatus = {
      stopped: ["Off", "idle"], starting: ["Starting…", "busy"], attached: ["On air", "ok"], failed: ["Failed", "bad"],
    }[g] || ["Off", "idle"];

    const ueActions = [];
    if (ue) {
      if (us === "attached" || us === "starting") ueActions.push({ action: "ue-stop", label: "Power off", cls: "btn-stop" });
      else ueActions.push({ action: "ue-start", label: "Power on", cls: "btn-primary", disabled: !gnbOn });
      if (ueOn) {
        ueActions.push(online
          ? { action: "pdu-release", label: "Disconnect", cls: "btn-ghost" }
          : { action: "pdu-establish", label: "Connect", cls: "btn-ghost" });
      }
    }

    return [
      { key: "ue", title: "Phone", tech: `UE · imsi-${ue ? ue.imsi : "—"}`, active: ueOn,
        status: ueStatus, actions: ueActions },
      { key: "gnb", title: "gNB", tech: gnbTech(), active: gnbOn,
        conn: { on: ueOn, label: "Radio" }, status: gnbStatus,
        actions: [g === "attached" || g === "starting"
          ? { action: "gnb-stop", label: "Stop", cls: "btn-stop" }
          : { action: "gnb-start", label: "Start", cls: "btn-primary" }] },
      { key: "core", title: "Core network", tech: `5GC · AMF ${amf}`, active: gnbOn,
        conn: { on: gnbOn, label: "Linked" },
        status: gnbOn ? ["Reachable", "ok"] : g === "failed" ? ["Link failed", "bad"] : ["Waiting for gNB", "idle"],
        actions: [] },
      { key: "net", title: "Internet", tech: `DNN internet · ${ue && ue.iface !== "—" ? ue.iface : "uesimtun0"}`, active: online,
        conn: { on: online, label: "Data" },
        status: online ? ["Reachable", "ok"] : ["Not connected", "idle"], actions: [] },
    ];
  }

  function renderMap() {
    const map = document.getElementById("net-map");
    const focused = document.activeElement && map.contains(document.activeElement)
      ? document.activeElement.dataset.action : null;

    map.innerHTML = buildNodes().map((n) => {
      const conn = n.conn
        ? `<div class="conn${n.conn.on ? " on" : ""}" aria-hidden="true"><div class="conn-line"></div><div class="conn-label">${n.conn.label}</div></div>`
        : "";
      const [statusText, tone] = n.status;
      const actions = n.actions.length
        ? `<div class="node-actions">${n.actions.map((a) =>
            `<button type="button" class="btn ${a.cls}" data-action="${a.action}"${a.disabled ? " disabled" : ""}>${escapeHtml(a.label)}</button>`).join("")}</div>`
        : "";
      return `<div class="node">${conn}
        <div class="node-card${n.active ? " active" : ""}">
          <div class="node-top">
            <div class="node-icon"><svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[n.key]}</svg></div>
            <div style="min-width:0"><div class="node-title">${escapeHtml(n.title)}</div><div class="node-tech">${escapeHtml(n.tech)}</div></div>
          </div>
          <div class="pill${tone !== "idle" ? " tone-" + tone : ""}"><span class="dot"></span>${escapeHtml(statusText)}</div>
          <div class="node-fill"></div>
          ${actions}
        </div></div>`;
    }).join("");

    if (focused) map.querySelector(`[data-action="${focused}"]`)?.focus();
  }

  const GNB_FIELDS = [
    ["mcc", "Country code (MCC)"], ["mnc", "Network code (MNC)"], ["nci", "Cell ID (NCI)"], ["tac", "Area code (TAC)"],
    ["amfAddress", "Core address (AMF)"], ["gtpIp", "gNB user-plane IP (GTP)"], ["linkIp", "Radio link IP"],
  ];

  function renderDetails() {
    const el = document.getElementById("details");
    const f = state.gnb.fields || {};
    const ue = theUe();
    const rows = GNB_FIELDS.map(([k, label]) => [label, f[k]]);
    rows.push(
      ["Phone ID (IMSI)", ue ? ue.imsi : null],
      ["Phone IP", isOnline(ue) ? ue.ip : null],
      ["Phone interface", isOnline(ue) ? ue.iface : null],
      ["Registration (5GMM)", ue ? ue.regState : null],
      ["Connection (CM)", ue ? ue.rrcState : null],
      ["Inbox port (UDP + TCP)", ue ? ue.udpPort : null],
    );
    el.innerHTML = rows.map(([label, v]) =>
      `<div><div class="field-label">${escapeHtml(label)}</div><div class="field-value">${escapeHtml(v == null || v === "" ? "—" : v)}</div></div>`).join("");
  }

  // ── data plane (command console + UDP inbox) ─────────────────────────────
  const DP_CONSOLE_MAX = 300;
  const DP_UDP_MAX = 200;
  let dpShownId = null;

  // Only one UE can run at a time (see UeRegistry.start), so the data plane
  // follows the attached UE, falling back to one that is still starting.
  function dpTarget() {
    return state.ues.find((u) => u.connected) || state.ues.find((u) => u.state === "starting") || null;
  }

  function scrollToEnd(el) {
    el.scrollTop = el.scrollHeight;
  }

  function appendConsoleLine(entry) {
    const out = document.getElementById("dp-console");
    out.querySelector(".term-empty")?.remove();
    const line = document.createElement("div");
    if (entry.kind === "cmd") {
      line.className = "term-cmd";
      line.textContent = "$ " + entry.text;
    } else if (entry.kind === "exit") {
      line.className = "term-exit" + (entry.text === "exit code 0" ? "" : " bad");
      line.textContent = "[" + entry.text + "]";
    } else {
      line.textContent = entry.text;
    }
    out.appendChild(line);
    while (out.children.length > DP_CONSOLE_MAX) out.removeChild(out.firstChild);
    scrollToEnd(out);
  }

  function appendUdpMessage(msg) {
    const out = document.getElementById("dp-udp");
    out.querySelector(".inbox-empty")?.remove();
    const row = document.createElement("div");
    row.className = "msg";
    const via = msg.proto === "http" ? "HTTP" : "UDP";
    const incident = msg.incident ? ` · incident ${escapeHtml(msg.incident)}` : "";
    row.innerHTML =
      `<div class="msg-meta"><b>From ${escapeHtml(msg.src)}</b>` +
      `<span>${via}${incident} · ${escapeHtml(msg.time)}</span></div>` +
      `<div class="msg-text">${escapeHtml(msg.text)}</div>`;
    out.insertBefore(row, out.firstChild);
    while (out.children.length > DP_UDP_MAX) out.removeChild(out.lastChild);
  }

  function consoleEmpty() {
    const out = document.getElementById("dp-console");
    const hint = isOnline(dpTarget())
      ? "Pick a quick test above, or type a command below."
      : "The phone needs an internet session before you can send traffic. Start the gNB, then power on the phone.";
    out.innerHTML = `<div class="term-empty">${escapeHtml(hint)}</div>`;
  }

  function udpEmpty() {
    const ue = dpTarget();
    const port = ue && ue.udpPort ? ue.udpPort : 9000;
    const ip = isOnline(ue) ? ue.ip : "<phone IP>";
    document.getElementById("dp-udp").innerHTML =
      `<div class="inbox-empty"><b>No messages yet</b>Send one from the core host:` +
      `<code>echo "hello" | nc -u -w1 ${escapeHtml(ip)} ${port}</code>` +
      `<code>curl -X POST http://${escapeHtml(ip)}:${port}/notify -H 'Content-Type: application/json' ` +
      `-d '{"message":"hello"}'</code></div>`;
  }

  // Rebuilds both panes only when the target UE changes (or on a fresh snapshot),
  // so streaming lines and a half-typed command aren't disturbed.
  function renderDataPlane(force) {
    const ue = dpTarget();
    const id = ue ? ue.id : null;
    if (force || id !== dpShownId) {
      dpShownId = id;
      document.getElementById("dp-console").innerHTML = "";
      document.getElementById("dp-udp").innerHTML = "";
      const lines = id != null ? state.console[id] || [] : [];
      const msgs = id != null ? state.udp[id] || [] : [];
      if (lines.length) lines.forEach(appendConsoleLine); else consoleEmpty();
      if (msgs.length) msgs.forEach(appendUdpMessage); else udpEmpty();
    } else {
      // refresh the hint text, which depends on whether the phone is online
      const c = document.getElementById("dp-console");
      if (c.querySelector(".term-empty")) consoleEmpty();
      if (document.getElementById("dp-udp").querySelector(".inbox-empty")) udpEmpty();
    }
    renderDataPlaneControls();
    renderInboxBadge();
  }

  function renderDataPlaneControls() {
    const ue = dpTarget();
    const up = isOnline(ue);
    const busy = !!(ue && state.cmdBusy[ue.id]);

    document.getElementById("dp-prompt").textContent = up ? `${ue.iface} $` : "offline $";
    document.getElementById("dp-input").disabled = !up;
    document.getElementById("dp-run").disabled = !up || busy;
    document.getElementById("dp-stop").disabled = !up || !busy;
    document.querySelectorAll("#dp-presets .chip").forEach((c) => { c.disabled = !up || busy; });

    const port = ue && ue.udpPort ? ue.udpPort : 9000;
    const udp = !!(ue && ue.udpListening);
    const http = !!(ue && ue.httpListening);
    const listening = udp || http;
    document.getElementById("listen-label").textContent = listening ? `Listening on ${ue.ip}:${port}` : "Not listening";
    setTone(document.getElementById("listen-pill"), udp && http ? "ok" : listening ? "busy" : "idle");
    const via = [udp && "UDP", http && "HTTP POST /notify (TCP)"].filter(Boolean).join(" and ");
    document.getElementById("listen-note").textContent = listening
      ? `The phone receives ${via} messages on port ${port}.`
      : `The phone receives UDP and HTTP POST /notify (TCP) messages on port ${port}.`;
  }

  function renderInboxBadge() {
    const ue = dpTarget();
    const n = ue ? (state.udp[ue.id] || []).length : 0;
    const badge = document.getElementById("inbox-badge");
    badge.textContent = String(n);
    badge.classList.toggle("has", n > 0);
  }

  async function runCommand(cmd) {
    const ue = dpTarget();
    cmd = (cmd || "").trim();
    if (!ue || !cmd) return;
    const input = document.getElementById("dp-input");
    try {
      await api("POST", `/api/ues/${ue.id}/exec`, { cmd });
      input.value = "";
    } catch (e) {
      appendConsoleLine({ kind: "exit", text: "rejected: " + e.message });
    }
  }

  async function stopCommand() {
    const ue = dpTarget();
    if (!ue) return;
    try {
      await api("POST", `/api/ues/${ue.id}/exec/stop`);
    } catch (e) {
      note(e.message, "bad");
    }
  }

  function clearUdp() {
    const ue = dpTarget();
    if (ue) state.udp[ue.id] = [];
    udpEmpty();
    renderInboxBadge();
  }

  // ── node actions ─────────────────────────────────────────────────────────
  async function act(action) {
    const ue = theUe();
    try {
      switch (action) {
        case "gnb-start":
          note("Starting gNB…", "busy");
          await api("POST", "/api/gnb/start");
          break;
        case "gnb-stop":
          note("Stopping gNB", "idle");
          await api("POST", "/api/gnb/stop");
          break;
        case "ue-start":
          note("Powering on the phone…", "busy");
          await api("POST", `/api/ues/${ue.id}/start`);
          break;
        case "ue-stop":
          note("Powering off the phone", "idle");
          await api("POST", `/api/ues/${ue.id}/stop`);
          break;
        case "pdu-establish":
          note("Opening an internet session…", "busy");
          await api("POST", `/api/ues/${ue.id}/pdu/establish`);
          break;
        case "pdu-release":
          await api("POST", `/api/ues/${ue.id}/pdu/release-all`);
          state.pduReleased[ue.id] = true;
          note("Internet session closed", "idle");
          renderAll();
          break;
      }
    } catch (e) {
      note(e.message, "bad");
    }
  }

  // ── view toggles ─────────────────────────────────────────────────────────
  function applyPrefs() {
    document.getElementById("details").hidden = !prefs.details;
    document.getElementById("btn-details").setAttribute("aria-expanded", String(prefs.details));
    document.getElementById("details-label").textContent = prefs.details ? "Hide technical details" : "Technical details";

    const term = prefs.tab !== "inbox";
    document.getElementById("tab-term").setAttribute("aria-selected", String(term));
    document.getElementById("tab-inbox").setAttribute("aria-selected", String(!term));
    document.getElementById("pane-term").hidden = !term;
    document.getElementById("pane-inbox").hidden = term;

    const hi = prefs.logView !== "raw";
    document.getElementById("tab-hi").setAttribute("aria-selected", String(hi));
    document.getElementById("tab-raw").setAttribute("aria-selected", String(!hi));
    document.getElementById("log-hi").hidden = !hi;
    const raw = document.getElementById("log-raw");
    raw.hidden = hi;
    if (!hi) raw.scrollTop = raw.scrollHeight;
  }

  function setPref(key, value) {
    prefs[key] = value;
    savePrefs();
    applyPrefs();
  }

  // ── wiring ───────────────────────────────────────────────────────────────
  document.getElementById("net-map").addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-action]");
    if (btn && !btn.disabled) act(btn.dataset.action);
  });
  document.getElementById("btn-details").onclick = () => setPref("details", !prefs.details);
  document.getElementById("tab-term").onclick = () => setPref("tab", "term");
  document.getElementById("tab-inbox").onclick = () => setPref("tab", "inbox");
  document.getElementById("tab-hi").onclick = () => setPref("logView", "hi");
  document.getElementById("tab-raw").onclick = () => setPref("logView", "raw");

  document.getElementById("dp-form").addEventListener("submit", (ev) => {
    ev.preventDefault();
    runCommand(document.getElementById("dp-input").value);
  });
  document.getElementById("dp-stop").onclick = stopCommand;
  document.getElementById("dp-udp-clear").onclick = clearUdp;
  document.getElementById("dp-presets").addEventListener("click", (e) => {
    const chip = e.target.closest(".chip");
    if (!chip || chip.disabled) return;
    document.getElementById("dp-input").value = chip.dataset.cmd;
    runCommand(chip.dataset.cmd);
  });

  applyPrefs();
  renderAll();
  renderLogs();
  connectWs();
})();
