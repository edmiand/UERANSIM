(() => {
  "use strict";

  const state = {
    gnb: { state: "stopped", fields: {} },
    ues: [],
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
        renderAll();
        clearLog();
        msg.logs.forEach(appendLog);
      } else if (msg.type === "gnb_state") {
        state.gnb.state = msg.state;
        renderAll();
      } else if (msg.type === "ue_state") {
        const idx = state.ues.findIndex((u) => u.id === msg.id);
        const snap = { id: msg.id, name: msg.name, imsi: msg.imsi, state: msg.state,
                       regState: msg.regState, rrcState: msg.rrcState, connected: msg.connected, iface: msg.iface };
        if (idx === -1) state.ues.push(snap); else state.ues[idx] = snap;
        renderAll();
      } else if (msg.type === "log") {
        appendLog(msg);
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
    container.parentElement.scrollTop = container.parentElement.scrollHeight;
  }

  function escapeHtml(s) {
    const d = document.createElement("div");
    d.textContent = s;
    return d.innerHTML;
  }

  // ── rendering ────────────────────────────────────────────────────────────
  function renderAll() {
    renderTopBar();
    renderStatCards();
    renderGnbCard();
    renderTopology();
    renderUeTable();
    document.getElementById("nav-ue-badge").textContent = String(state.ues.filter((u) => u.connected).length);
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

  function statCard(label, value, sub, dotColor, pulsing) {
    const card = document.createElement("div");
    card.className = "stat-card";
    card.innerHTML = `
      <div class="stat-card-head">
        <span class="stat-card-label">${escapeHtml(label)}</span>
        <span class="stat-card-dot" style="background:${dotColor}"></span>
      </div>
      <div class="stat-card-value">${escapeHtml(value)}</div>
      <div class="stat-card-sub">${escapeHtml(sub)}</div>`;
    return card;
  }

  function renderStatCards() {
    const container = document.getElementById("stat-cards");
    container.innerHTML = "";
    const g = state.gnb.state;
    const connected = state.ues.filter((u) => u.connected).length;
    const registered = state.ues.filter((u) => u.regState === "registered").length;

    container.appendChild(statCard(
      "gNB status", g === "attached" ? "Running" : g === "starting" ? "Starting" : g === "failed" ? "Failed" : "Stopped",
      g === "attached" ? "NGAP up · AMF linked" : "Not broadcasting",
      g === "attached" ? "var(--green-dot)" : g === "failed" ? "var(--red-text)" : "var(--neutral-dot)",
    ));
    container.appendChild(statCard("Registered UEs", String(registered), `of ${state.ues.length} total`, "var(--blue)"));
    container.appendChild(statCard("Active sessions", String(connected), "PDU sessions up", "var(--green-dot)"));
    container.appendChild(statCard("UEs added", String(state.ues.length), "including stopped", "var(--amber-dot)"));
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
    document.getElementById("connected-ue-count").textContent = String(connected);
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
    const empty = document.getElementById("ue-empty");
    document.getElementById("ue-count").textContent = String(state.ues.length);
    body.innerHTML = "";
    if (state.ues.length === 0) {
      empty.hidden = false;
      return;
    }
    empty.hidden = true;

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
      ifaceCell.textContent = ue.iface;
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

      const removeBtn = document.createElement("button");
      removeBtn.className = "icon-btn danger";
      removeBtn.title = "Remove";
      removeBtn.textContent = "✕";
      removeBtn.onclick = () => removeUe(ue);
      actions.appendChild(removeBtn);

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

  async function removeUe(ue) {
    try {
      await api("DELETE", `/api/ues/${ue.id}`);
      state.ues = state.ues.filter((u) => u.id !== ue.id);
      renderAll();
    } catch (e) {
      alert(e.message);
    }
  }

  // ── Add UE modal ─────────────────────────────────────────────────────────
  function openModal() {
    document.getElementById("input-ue-name").value = "";
    document.getElementById("input-ue-imsi").value = "";
    document.getElementById("add-ue-modal").hidden = false;
  }
  function closeModal() {
    document.getElementById("add-ue-modal").hidden = true;
  }
  async function confirmAddUe() {
    const name = document.getElementById("input-ue-name").value.trim();
    const imsi = document.getElementById("input-ue-imsi").value.trim();
    if (!imsi) { alert("IMSI is required"); return; }
    try {
      await api("POST", "/api/ues", { name, imsi });
      closeModal();
    } catch (e) {
      alert(e.message);
    }
  }

  // ── wiring ───────────────────────────────────────────────────────────────
  document.getElementById("btn-toggle-gnb").onclick = toggleGnb;
  document.getElementById("btn-add-ue").onclick = openModal;
  document.getElementById("btn-modal-cancel").onclick = closeModal;
  document.getElementById("btn-modal-confirm").onclick = confirmAddUe;
  document.getElementById("add-ue-modal").addEventListener("click", (e) => {
    if (e.target.id === "add-ue-modal") closeModal();
  });

  renderAll();
  connectWs();
})();
