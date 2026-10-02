(() => {
  "use strict";

  const state = {
    gnb: { state: "stopped", fields: {} },
    ues: [],
    // per-UE data plane buffers, keyed by UE id
    console: {},
    udp: {},
    cmdBusy: {},
  };

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
        renderAll();
        renderDataPlane(true);
        clearLog();
        msg.logs.forEach(appendLog);
      } else if (msg.type === "gnb_state") {
        state.gnb.state = msg.state;
        renderAll();
      } else if (msg.type === "ue_state") {
        const idx = state.ues.findIndex((u) => u.id === msg.id);
        const snap = { id: msg.id, name: msg.name, imsi: msg.imsi, state: msg.state,
                       regState: msg.regState, rrcState: msg.rrcState, connected: msg.connected, iface: msg.iface,
                       ip: msg.ip, udpPort: msg.udpPort, udpListening: msg.udpListening };
        if (idx === -1) state.ues.push(snap); else state.ues[idx] = snap;
        renderAll();
      } else if (msg.type === "log") {
        appendLog(msg);
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
        (state.udp[msg.id] ||= []).push({ time: msg.time, src: msg.src, text: msg.text });
        trimBuffer(state.udp[msg.id], DP_UDP_MAX);
        if (dpTarget() && dpTarget().id === msg.id) appendUdpMessage(msg);
      }
    };

    ws.onclose = () => setTimeout(connectWs, 1500);
  }

  // ── log panel ────────────────────────────────────────────────────────────
  const LOG_MAX_LINES = 50;
  function clearLog() {
    document.getElementById("log-lines").innerHTML = "";
  }
  function appendLog(entry) {
    const container = document.getElementById("log-lines");
    const line = document.createElement("div");
    line.className = "log-line";
    line.innerHTML =
      `<span class="log-time">${escapeHtml(entry.time)}</span> ` +
      `<span style="color:${entry.color}">${escapeHtml(entry.tag)}</span> ` +
      escapeHtml(entry.text);
    container.appendChild(line);
    while (container.children.length > LOG_MAX_LINES) container.removeChild(container.firstChild);
    container.scrollTop = container.scrollHeight;
  }

  function escapeHtml(s) {
    const d = document.createElement("div");
    d.textContent = s;
    return d.innerHTML;
  }

  // ── rendering ────────────────────────────────────────────────────────────
  function renderAll() {
    renderTopBar();
    renderSummary();
    renderGnbCard();
    renderTopology();
    renderUeTable();
    renderDataPlane(false);
  }

  // ── UE data plane (command console + UDP inbox) ──────────────────────────
  const DP_CONSOLE_MAX = 300;
  const DP_UDP_MAX = 200;
  let dpShownId = null;

  function trimBuffer(buf, max) {
    while (buf.length > max) buf.shift();
  }

  // Only one UE can run at a time (see UeRegistry.start), so the data plane card
  // simply follows the attached UE, falling back to one that is still starting.
  function dpTarget() {
    return state.ues.find((u) => u.connected) || state.ues.find((u) => u.state === "starting") || null;
  }

  function scrollToEnd(el) {
    el.scrollTop = el.scrollHeight;
  }

  function appendConsoleLine(entry) {
    const out = document.getElementById("dp-console");
    out.querySelector(".dp-empty")?.remove();
    const line = document.createElement("div");
    if (entry.kind === "cmd") {
      line.className = "dp-line-cmd";
      line.textContent = "$ " + entry.text;
    } else if (entry.kind === "exit") {
      line.className = "dp-line-exit" + (entry.text === "exit code 0" ? "" : " bad");
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
    out.querySelector(".dp-empty")?.remove();
    const row = document.createElement("div");
    row.className = "dp-msg";
    row.innerHTML =
      `<span class="log-time">${escapeHtml(msg.time)}</span> ` +
      `<span class="dp-msg-src">${escapeHtml(msg.src)}</span> ` +
      escapeHtml(msg.text);
    out.appendChild(row);
    while (out.children.length > DP_UDP_MAX) out.removeChild(out.firstChild);
    scrollToEnd(out);
  }

  function emptyNote(el, text) {
    el.innerHTML = `<div class="dp-empty">${escapeHtml(text)}</div>`;
  }

  // Rebuilds both panes' contents only when the target UE changes (or on a fresh
  // snapshot), so streaming lines and the half-typed command aren't disturbed.
  function renderDataPlane(force) {
    const ue = dpTarget();
    const id = ue ? ue.id : null;
    if (force || id !== dpShownId) {
      dpShownId = id;
      const consoleEl = document.getElementById("dp-console");
      const udpEl = document.getElementById("dp-udp");
      consoleEl.innerHTML = "";
      udpEl.innerHTML = "";
      const lines = id != null ? state.console[id] || [] : [];
      const msgs = id != null ? state.udp[id] || [] : [];
      if (lines.length) lines.forEach(appendConsoleLine); else emptyNote(consoleEl, "No commands run yet.");
      if (msgs.length) msgs.forEach(appendUdpMessage); else emptyNote(udpEl, "No messages received yet.");
    }
    renderDataPlaneControls();
  }

  function renderDataPlaneControls() {
    const ue = dpTarget();
    const up = !!(ue && ue.connected && ue.ip);
    const busy = !!(ue && state.cmdBusy[ue.id]);

    document.getElementById("dp-target").textContent = ue
      ? `— ${ue.name}${ue.ip ? ` · ${ue.iface} · ${ue.ip}` : ""}`
      : "— no UE attached";
    const pill = document.getElementById("dp-pill");
    pill.style.background = up ? "var(--green-bg)" : "var(--border-lighter)";
    pill.style.color = up ? "var(--green-text)" : "var(--text-dim)";
    document.getElementById("dp-pill-label").textContent = up ? "PDU session up" : "No PDU session";

    document.getElementById("dp-prompt").textContent = up ? `${ue.iface} $` : "ue $";
    document.getElementById("dp-input").disabled = !up;
    document.getElementById("dp-run").disabled = !up || busy;
    document.getElementById("dp-stop").disabled = !up || !busy;
    document.querySelectorAll("#dp-presets .chip").forEach((c) => { c.disabled = !up; });

    const port = ue && ue.udpPort ? ue.udpPort : 9000;
    document.getElementById("dp-udp-addr").textContent = ue && ue.udpListening
      ? `listening on ${ue.ip}:${port}/udp`
      : "not listening";
    document.getElementById("dp-udp-help").innerHTML =
      `From the core host: <code>echo "hello" | nc -u -w1 ${escapeHtml(up ? ue.ip : "<UE IP>")} ${port}</code>`;
  }

  async function runCommand(ev) {
    ev.preventDefault();
    const ue = dpTarget();
    const input = document.getElementById("dp-input");
    const cmd = input.value.trim();
    if (!ue || !cmd) return;
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
      alert(e.message);
    }
  }

  function clearUdp() {
    const ue = dpTarget();
    if (ue) state.udp[ue.id] = [];
    emptyNote(document.getElementById("dp-udp"), "No messages received yet.");
  }

  function renderTopBar() {
    const running = state.gnb.state === "attached" || state.gnb.state === "starting";
    const btn = document.getElementById("btn-toggle-gnb");
    btn.textContent = running ? "■ Stop gNB" : "▶ Start gNB";
    btn.className = "btn " + (running ? "btn-outline" : "btn-outline");
    btn.style.color = running ? "var(--red-text)" : "var(--green-text)";
    btn.style.borderColor = running ? "var(--red-border)" : "var(--green-border)";
    btn.style.background = running ? "#fff" : "var(--green-bg)";
    const amfNote = document.getElementById("amf-note");
    amfNote.textContent = state.gnb.state === "attached" ? "AMF reachable" : "AMF not linked";
  }

  function summaryItem(dotColor, label, value) {
    return `<span class="summary-item"><span class="stat-card-dot" style="background:${dotColor}"></span>` +
      `${escapeHtml(label)} <b>${escapeHtml(value)}</b></span>`;
  }

  function renderSummary() {
    const g = state.gnb.state;
    const connected = state.ues.filter((u) => u.connected).length;
    const registered = state.ues.some((u) => u.regState === "registered");
    document.getElementById("summary").innerHTML = [
      summaryItem(
        g === "attached" ? "var(--green-dot)" : g === "failed" ? "var(--red-text)" : "var(--neutral-dot)",
        "gNB", g === "attached" ? "Running" : g === "starting" ? "Starting" : g === "failed" ? "Failed" : "Stopped",
      ),
      summaryItem(registered ? "var(--blue)" : "var(--neutral-dot)", "UE", registered ? "Registered" : "Deregistered"),
      summaryItem(connected ? "var(--green-dot)" : "var(--neutral-dot)", "PDU session", connected ? "Up" : "Down"),
    ].join("");
  }

  const GNB_FIELD_LABELS = [
    ["mcc", "MCC"], ["mnc", "MNC"], ["nci", "NCI"], ["tac", "TAC"],
    ["linkIp", "Link IP"], ["amfAddress", "AMF address"], ["gtpIp", "GTP IP"],
  ];

  function renderGnbCard() {
    const g = state.gnb.state;
    const dot = document.getElementById("gnb-status-dot");
    const label = document.getElementById("gnb-status-label");
    const pill = document.getElementById("gnb-status-pill");
    const map = {
      attached: ["Active", "var(--green-bg)", "var(--green-text)", true],
      starting: ["Starting…", "var(--border-lighter)", "var(--text-dim)", true],
      failed: ["Failed", "oklch(0.94 0.05 25)", "var(--red-text)", false],
      stopped: ["Stopped", "var(--border-lighter)", "var(--text-dim)", false],
    };
    const [text, bg, color, pulsing] = map[g] || map.stopped;
    label.textContent = text;
    pill.style.background = bg;
    pill.style.color = color;
    dot.classList.toggle("pulsing", pulsing);

    const grid = document.getElementById("gnb-fields");
    grid.innerHTML = "";
    for (const [key, label2] of GNB_FIELD_LABELS) {
      const val = state.gnb.fields ? state.gnb.fields[key] : undefined;
      const div = document.createElement("div");
      div.innerHTML = `<div class="field-label">${escapeHtml(label2)}</div><div class="field-value">${escapeHtml(val == null ? "—" : String(val))}</div>`;
      grid.appendChild(div);
    }
  }

  function renderTopology() {
    const g = state.gnb.state;
    const connected = state.ues.filter((u) => u.connected).length;
    document.getElementById("link-line-1").style.background = g === "attached" ? "var(--green-line)" : "var(--neutral-line)";
    document.getElementById("link-line-2").style.background = connected > 0 ? "var(--blue-line)" : "var(--neutral-line)";
    const gnbIcon = document.getElementById("gnb-node-icon");
    gnbIcon.style.background = g === "attached" ? "var(--green-bg)" : "var(--neutral-node-bg)";
    gnbIcon.style.borderColor = g === "attached" ? "var(--green-node-border)" : "var(--neutral-node-border)";
  }

  const STATE_SIGNAL_LEVEL = { attached: 4, starting: 2, failed: 1, stopped: 0 };
  const BAR_HEIGHTS = [5, 8, 11, 13];

  function signalCell(ueState) {
    const level = STATE_SIGNAL_LEVEL[ueState] ?? 0;
    const bars = document.createElement("div");
    bars.className = "signal-bars";
    BAR_HEIGHTS.forEach((h, i) => {
      const bar = document.createElement("div");
      bar.className = "signal-bar" + (i < level ? " on" : "");
      bar.style.height = h + "px";
      bars.appendChild(bar);
    });
    const wrap = document.createElement("div");
    wrap.className = "signal";
    wrap.appendChild(bars);
    const lbl = document.createElement("span");
    lbl.className = "signal-label";
    lbl.textContent = ueState;
    wrap.appendChild(lbl);
    return wrap;
  }

  function renderUeTable() {
    const body = document.getElementById("ue-table-body");
    body.innerHTML = "";

    for (const ue of state.ues) {
      const row = document.createElement("div");
      row.className = "ue-row";

      const nameCell = document.createElement("div");
      nameCell.innerHTML = `<div class="ue-name">${escapeHtml(ue.name)}</div><div class="ue-imsi">imsi-${escapeHtml(ue.imsi)}</div>`;
      row.appendChild(nameCell);

      const regCell = document.createElement("div");
      const pill = document.createElement("span");
      pill.className = "pill" + (ue.regState === "registered" ? " registered" : "");
      pill.textContent = ue.regState;
      regCell.appendChild(pill);
      row.appendChild(regCell);

      const rrcCell = document.createElement("div");
      rrcCell.className = "rrc-state";
      rrcCell.textContent = ue.rrcState;
      row.appendChild(rrcCell);

      row.appendChild(signalCell(ue.state));

      const ifaceCell = document.createElement("div");
      ifaceCell.className = "data-iface";
      ifaceCell.textContent = ue.ip ? `${ue.iface} · ${ue.ip}` : ue.iface;
      row.appendChild(ifaceCell);

      const actions = document.createElement("div");
      actions.className = "row-actions";

      const toggleBtn = document.createElement("button");
      toggleBtn.className = "icon-btn";
      const running = ue.state === "starting" || ue.state === "attached";
      toggleBtn.title = running ? "Stop" : "Start";
      toggleBtn.textContent = running ? "⏸" : "▶";
      toggleBtn.onclick = () => toggleUe(ue);
      actions.appendChild(toggleBtn);

      row.appendChild(actions);
      body.appendChild(row);
    }
  }

  // ── actions ──────────────────────────────────────────────────────────────
  async function toggleGnb() {
    const running = state.gnb.state === "attached" || state.gnb.state === "starting";
    try {
      await api("POST", running ? "/api/gnb/stop" : "/api/gnb/start");
    } catch (e) {
      alert(e.message);
    }
  }

  async function toggleUe(ue) {
    const running = ue.state === "starting" || ue.state === "attached";
    try {
      await api("POST", `/api/ues/${ue.id}/${running ? "stop" : "start"}`);
    } catch (e) {
      alert(e.message);
    }
  }

  // ── wiring ───────────────────────────────────────────────────────────────
  document.getElementById("btn-toggle-gnb").onclick = toggleGnb;
  document.getElementById("dp-form").addEventListener("submit", runCommand);
  document.getElementById("dp-stop").onclick = stopCommand;
  document.getElementById("dp-udp-clear").onclick = clearUdp;
  document.getElementById("dp-presets").addEventListener("click", (e) => {
    const cmd = e.target.dataset && e.target.dataset.cmd;
    if (!cmd) return;
    const input = document.getElementById("dp-input");
    input.value = cmd;
    input.focus();
  });

  renderAll();
  connectWs();
})();
