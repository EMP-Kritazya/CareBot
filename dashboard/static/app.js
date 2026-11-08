// CareBot Response Console. Every robot message arrives over the WebSocket as {topic, msg};
// `apply` folds it into state S and `render` draws the board and the map from S. Nothing here
// decides anything: the robot's state machine does. The console shows what it was told.
"use strict";

const SEARCH_MS = 1500;   // how long the nearest-responder ranking is shown before the rings start
const EVENT_TEXT = { fall: "Fall detected", stillness: "No movement detected", manual: "Manual alert" };

let conf = null, map = null, pin = null, mode = "live";
const rx = {}, lines = {};
let S = fresh(), seq = 0, drawn = { log: -1, table: "" };

function fresh() {
  return { robot: null, eyes: "off", awaiting: null, ask: 0, lastBeat: 0, incident: null, sent: [],
           dispatch: {}, notes: {}, statuses: {}, real: [], wave: 0, searchUntil: 0, searchShown: false,
           log: [], raw: [] };
}

// ---------------------------------------------------------------- helpers

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const tsOf = (m) => (m.ts ? new Date(typeof m.ts === "number" ? m.ts * 1000 : m.ts) : new Date());
const clock = (d) => d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
const walk = (s) => (s < 60 ? `${Math.round(s)} s` : `${(s / 60).toFixed(1)} min`);
const meters = (m) => (m >= 1000 ? `${(m / 1000).toFixed(2)} km` : `${Math.round(m)} m`);
const R = (id) => conf.receivers.find((r) => r.id === id);
const first = (n) => String(n).split(" ")[0];
const dist = (r) => conf.routes[r.id]?.distance_m ?? L.latLng(r.lat, r.lng).distanceTo([conf.home.lat, conf.home.lng]);

function log(m, cls, html, quote) {
  S.log.push({ t: tsOf(m), cls, html, quote });
  seq++;
}

// ---------------------------------------------------------------- messages -> state

function apply(topic, m) {
  S.raw.push(`${topic.padEnd(9)} ${JSON.stringify(m)}`);
  if (S.raw.length > 150) S.raw.shift();
  if (topic === "receivers") return applyReceivers(m);
  const name = conf.resident;
  switch (m.type) {
    case "heartbeat": {
      const prevAsk = S.ask;
      Object.assign(S, { robot: m.mode, eyes: m.eyes, awaiting: m.awaiting, ask: m.ask || 0, lastBeat: Date.now() });
      if (S.incident && m.incident_id === S.incident.id) Object.assign(S.incident, { neighbor: m.neighbor, helper: m.helper });
      if (m.mode === "CHECK_IN" && m.awaiting === "check_in" && S.ask > prevAsk && S.ask > 1) {
        log(m, "pending", `<b>Check-in ${S.ask} of 2.</b> No answer to the first; asking ${esc(name)} again.`);
      }
      return;
    }
    case "status":
      if (m.mode === "CHECK_IN" && m.incident_id) {
        S = Object.assign(fresh(), { raw: S.raw, lastBeat: S.lastBeat, robot: "CHECK_IN" });
        S.incident = { id: m.incident_id, event: m.event, who: m.who, started: tsOf(m) };
        seq++;
        clearLines();
        const who = m.who === "resident" ? name : `a visitor (not ${name})`;
        log(m, "pending", `<b>${esc(EVENT_TEXT[m.event] || m.event)}</b>, ${esc(who)}. The robot is checking in by voice.`);
      } else if (m.mode === "MONITORING" && S.incident && !S.incident.closed) {
        S.incident.closed = tsOf(m);
        S.incident.outcome = "ok";
        log(m, "ok", `<b>${esc(name)} answered the check-in.</b> No alert sent.`);
      }
      if (m.mode) S.robot = m.mode;
      return;
    case "alert":
      if (!S.incident || S.incident.id !== m.incident_id) S.incident = { id: m.incident_id, event: m.event, who: m.who, started: tsOf(m) };
      S.incident.alerted = tsOf(m);
      S.robot = "ALERTED";
      log(m, "alert", `<b>ALERT.</b> No answer to 2 check-ins. Dispatching 911, ${esc(conf.family)} and the nearest neighbors.`);
      return;
    case "sent": {
      S.sent = m.channels || [];
      const missing = ["911", "family", "neighbors"].filter((c) => !S.sent.includes(c));
      if (missing.length) log(m, "alert", `<b>Not reached:</b> ${esc(missing.map(label).join(", "))}.`);
      return;
    }
    case "dispatch":
      return applyDispatch(m);
    case "call":
      if (m.status === "unanswered") log(m, "alert", `<b>${esc(label(m.target))} did not pick up.</b>`);
      return;
    case "update":
      return applyUpdate(m);
    case "close":
      if (S.incident) { S.incident.closed = tsOf(m); S.incident.outcome = m.outcome; }
      S.robot = "MONITORING";
      log(m, "ok", `<b>Incident closed:</b> ${esc(closeText(m.outcome))}.`);
      return;
  }
}

const label = (t) => ({ "911": "911", family: conf.family, neighbors: "neighbors" }[t] || t);

function applyDispatch(m) {
  const t = m.target;
  const d = S.dispatch[t] = S.dispatch[t] || {};
  if (m.kind === "update") {
    S.notes[t] = { status: m.status, t: tsOf(m), sim: m.simulated };
  } else {
    Object.assign(d, { status: m.status, t: tsOf(m), sim: m.simulated, channel: m.channel });
  }
  const who = t === "911" ? "911" : conf.family;
  if (m.kind !== "update") {
    const line = {
      dialing: ["pending", `<b>Calling 911</b> (stand-in phone).`],
      connected: ["ok", `<b>911 connected.</b> Briefing delivered; the dispatcher can ask questions.`],
      no_answer: ["alert", `<b>${esc(who)} did not answer.</b>`],
      failed: ["alert", `<b>${esc(who)}: ${t === "family" ? "text" : "call"} failed.</b>`],
    }[m.status];
    if (t === "family" && m.status === "sent") log(m, "ok", `<b>Text sent to ${esc(who)}</b> (${esc(chan(m.channel))}).`);
    else if (t === "family" && m.status === "read") log(m, "ok", `<b>${esc(who)} read the alert.</b>`);
    else if (line && !(t === "family" && m.status === "dialing")) log(m, line[0], line[1]);
  }
}

const chan = (c) => ({ whatsapp: "WhatsApp", sms: "SMS", call: "call" }[c] || c || "text");

function applyUpdate(m) {
  const name = conf.resident;
  if (S.incident && m.status === "on_the_way") Object.assign(S.incident, { neighbor: "on_the_way", helper: m.helper });
  if (S.incident && m.status === "with_resident") S.incident.neighbor = "with_resident";
  S.lastUpdate = m.status === "on_the_way" ? `${m.helper} on the way` : ({ hospital: "going to the hospital",
    with_resident: "someone is with him", got_up: "camera saw him get up", resident_said: `${name} spoke`,
    ambulance_here: "ambulance is there", resident_ok: `${name} says he's OK`, unclear: "message from the robot" }[m.status] || m.status);
  const by = { resident: name, in_room: "Someone at the robot", sms: "A reply by text" }[m.source] || "Someone";
  const q = m.quote ? { text: m.quote, by } : null;
  const text = {
    on_the_way: `<b>Update sent to ${esc(conf.family)} and 911:</b> ${esc(m.helper)} is on the way.`,
    got_up: `<b>Update sent:</b> the camera saw ${esc(name)} get back up. Help is not called off.`,
    resident_said: `<b>${esc(name)} spoke.</b> Passed on to ${esc(conf.family)} and 911 word for word:`,
    hospital: `<b>Update sent to ${esc(conf.family)} and 911:</b> going to the hospital. Word for word:`,
    with_resident: `<b>Someone is with ${esc(name)}.</b> Passed on:`,
    ambulance_here: `<b>Update sent:</b> the ambulance is there.`,
    resident_ok: `<b>${esc(name)} says he's OK.</b>`,
    unclear: `<b>Someone at the robot spoke.</b> Passed on as said:`,
  }[m.status] || `<b>Update:</b> ${esc(m.status)}`;
  log(m, "ok", text, q);
}

function closeText(o) {
  const n = conf.resident;
  return { ok: `${n} said he's OK`, resident_ok: `${n} said he's OK`, hospital: "on the way to the hospital",
           ambulance_here: "the ambulance is there", reset: "reset by the operator", got_up_no_answer: `${conf.family} was told` }[o] || o;
}

function applyReceivers(m) {
  if (!S.incident || (m.incident_id && m.incident_id !== S.incident.id)) return;
  const before = S.statuses, after = m.statuses || {};
  const newly = (st) => Object.keys(after).filter((id) => after[id] === st && before[id] !== st);
  const rung = newly("contacted");
  if (rung.length) {
    if (m.wave === 1) {
      S.searchUntil = Date.now() + SEARCH_MS;  // show the ranking first, then the rings
      log(m, "pending", `<b>Finding the nearest responders.</b> ${conf.receivers.length} receivers ranked by walking time.`);
      setTimeout(render, SEARCH_MS + 30);
    }
    log(m, "pending", `<b>Ringing wave ${m.wave}:</b> ${esc(rung.map((id) => `${first(R(id).name)} (${meters(dist(R(id)))})`).join(", "))}.`);
  }
  for (const id of newly("yes")) log(m, "ok", `<b>${esc(R(id).name)} accepted.</b> Responding, ${meters(dist(R(id)))} · ${walk(R(id).walk_s)} walk.`);
  for (const id of newly("no")) log(m, "", `${esc(R(id).name)} declined.`);
  const quiet = newly("no_reply");
  if (quiet.length) log(m, "pending", `<b>No answer</b> from ${esc(quiet.map((id) => first(R(id).name)).join(", "))}. Moving to the next nearest.`);
  if (newly("stood_down").length) log(m, "", `Other ringing receivers stood down.`);
  Object.assign(S, { statuses: after, wave: m.wave, real: m.real || [] });
}

// ---------------------------------------------------------------- derived view state

function view(id) {
  const st = S.statuses[id] || "waiting";
  const searching = Date.now() < S.searchUntil;
  if (searching) return "candidate";
  return { waiting: "idle", contacted: "ringing", yes: "accepted", no: "declined", no_reply: "no_answer", stood_down: "stood_down" }[st];
}

function incidentState() {
  const inc = S.incident;
  if (!inc) return "idle";
  if (inc.closed) return "closed";
  if (S.robot === "CHECK_IN" && !inc.alerted) return "check";
  if (inc.alerted) return ["on_the_way", "with_resident"].includes(inc.neighbor) ? "responding" : "alert";
  return "check";
}

// ---------------------------------------------------------------- render

function render() {
  const online = Date.now() - S.lastBeat < 10000;
  $("link").className = "link " + (S.lastBeat ? (online ? "online" : "offline") : "");
  $("link-text").textContent = S.lastBeat ? (online ? `online · ${conf.link}` : "robot offline") : `waiting · ${conf.link}`;

  const inc = S.incident, state = incidentState();
  $("app").classList.toggle("closed", state === "closed" || state === "idle");
  $("incident").dataset.state = state;
  const name = conf.resident;
  const titles = {
    idle: "Monitoring",
    check: `Checking in with ${name}`,
    alert: `ALERT · ${EVENT_TEXT[inc?.event] || "Alert"}${inc?.who === "other" ? " (visitor)" : ""}, no response`,
    responding: inc?.neighbor === "with_resident" ? "ALERT · Someone is with him" : `ALERT · ${inc?.helper || "A neighbor"} responding`,
    closed: `Closed · ${closeText(inc?.outcome)}`,
  };
  $("inc-title").textContent = titles[state];
  $("inc-sub").textContent = state === "idle" ? `${name} · ${conf.home.street || "home"} (block level) · robot ${online ? "online" : "offline"}`
    : state === "check" ? `${EVENT_TEXT[inc.event] || ""} · asking out loud${S.ask ? ` (${S.ask} of 2)` : ""}`
    : `${inc.who === "other" ? `Visitor at ${name}'s home` : name} · ${conf.home.street || ""} · since ${clock(inc.started)}`;
  const live = inc && !inc.closed;
  $("inc-clock").textContent = live ? elapsed(inc.alerted || inc.started) : "";

  renderDispatch(state);
  renderTable();
  renderLog();
  renderMap(state);
  if (!$("raw").hidden) { $("raw").textContent = S.raw.slice(-60).join("\n"); }
}

function elapsed(since) {
  const s = Math.max(0, Math.floor((Date.now() - since) / 1000));
  return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
}

function setRow(id, active, status, cls, sub) {
  const li = $(id);
  li.classList.toggle("active", active);
  const st = li.querySelector(".st");
  st.className = `st ${cls}`;
  st.textContent = status;
  li.querySelector(".d-sub").innerHTML = sub;
}

const STATUS = {  // dispatch status -> [label, class]
  dialing: ["Dialing", "pending"], ringing: ["Ringing", "pending"], connected: ["Connected", "ok"], ended: ["Ended", "done"],
  no_answer: ["No answer", "bad"], failed: ["Failed", "bad"], sending: ["Sending", "pending"], sent: ["Sent", "done"],
  delivered: ["Delivered", "ok"], read: ["Read", "ok"],
};

function renderDispatch(state) {
  const active = !!(S.incident?.alerted);
  const sim = (d) => (d?.sim ? `<span class="sim">SIM</span>` : "");
  const note = (t) => {
    const n = S.notes[t];
    return n ? ` · update <b>${esc(STATUS[n.status]?.[0].toLowerCase() || n.status)}</b> ${clock(n.t)}` : "";
  };

  // 911
  const c = S.dispatch["911"];
  if (!active || !c) setRow("d-911", false, active ? "Queued" : "Idle", active ? "pending" : "idle", active ? "Placing the call" : "Not contacted");
  else {
    const [lab, cls] = STATUS[c.status] || [c.status, "done"];
    const sub = { dialing: "Placing the call", ringing: "Ringing the stand-in phone", connected: "Briefed · on the line",
                  ended: "Call ended", no_answer: "No answer · CareBot will keep trying", failed: "Call could not be placed" }[c.status] || "";
    setRow("d-911", true, lab, cls, `${sub}${note("911")} ${sim(c)}`);
  }

  // family
  const f = S.dispatch.family;
  $("family-chan").textContent = `${conf.relation || "family"} · ${chan(f?.channel || conf.texts)}`;
  if (!active || !f) setRow("d-family", false, active ? "Queued" : "Idle", active ? "pending" : "idle", active ? "Preparing the alert text" : "Not contacted");
  else {
    const [lab, cls] = STATUS[f.status] || [f.status, "done"];
    setRow("d-family", true, lab, cls, `Alert ${lab.toLowerCase()} ${clock(f.t)}${note("family")} ${sim(f)}`);
  }

  // neighbors
  const vals = Object.values(S.statuses);
  const yes = Object.keys(S.statuses).find((id) => S.statuses[id] === "yes");
  const ringing = Object.keys(S.statuses).filter((id) => S.statuses[id] === "contacted");
  if (!active || !vals.length) setRow("d-neighbors", false, active ? "Queued" : "Idle", active ? "pending" : "idle",
                                      active ? "Locating receivers" : `${conf.receivers.length} receivers within ${meters(maxDist())}`);
  else if (Date.now() < S.searchUntil) setRow("d-neighbors", true, "Ranking", "pending", `Ranking ${conf.receivers.length} receivers by walking time`);
  else if (yes) setRow("d-neighbors", true, "Responding", "ok", `<b>${esc(R(yes).name)}</b> accepted · ${meters(dist(R(yes)))} · ${walk(R(yes).walk_s)} walk`);
  else if (ringing.length) setRow("d-neighbors", true, `Ringing ${ringing.length}`, "pending",
                                  `Wave ${S.wave}: ${esc(ringing.map((id) => first(R(id).name)).join(", "))}`);
  else setRow("d-neighbors", true, "No responder", "bad", "Every receiver was tried");
}

const maxDist = () => Math.max(...conf.receivers.map(dist));

function renderTable() {
  const searching = Date.now() < S.searchUntil;
  const sig = JSON.stringify([S.statuses, S.real, searching]);
  if (sig === drawn.table) return;
  drawn.table = sig;
  const LABEL = { idle: ["Standby", "idle"], candidate: ["Ranked", "done"], ringing: ["Ringing", "pending"], accepted: ["Accepted", "ok"],
                  declined: ["Declined", "done"], no_answer: ["No answer", "bad"], stood_down: ["Stood down", "done"] };
  $("resp-count").textContent = `${conf.receivers.length} receivers`;
  $("resp-body").innerHTML = conf.receivers.map((r, i) => {
    const v = view(r.id);
    const [lab, cls] = LABEL[v];
    const row = v === "accepted" ? "accepted" : v === "ringing" ? "selected" : ["declined", "no_answer", "stood_down"].includes(v) ? "dim" : "";
    const real = S.real.includes(r.id) ? `<span class="real">PHONE</span>` : "";
    return `<tr class="${row}${searching ? " scan" : ""}" style="${searching ? `animation-delay:${i * 110}ms` : ""}">
      <td class="rank">${r.rank}</td><td class="name">${esc(r.name)}${real}</td>
      <td class="num">${meters(dist(r))}</td><td class="num">${walk(r.walk_s)}</td>
      <td><span class="st ${cls}">${lab}</span></td></tr>`;
  }).join("");
}

function renderLog() {
  if (drawn.log === seq) return;
  drawn.log = seq;
  $("log").innerHTML = S.log.length ? S.log.slice().reverse().map((e) => `
    <li class="${e.cls}"><time>${clock(e.t)}</time><div class="msg">${e.html}
      ${e.quote ? `<blockquote>“${esc(e.quote.text)}”<br><small>${esc(e.quote.by)}</small></blockquote>` : ""}</div></li>`).join("")
    : `<li class="empty">No events. The robot is monitoring.</li>`;
}

// ---------------------------------------------------------------- map

function renderMap(state) {
  const el = pin.getElement()?.querySelector(".pin");
  if (el) el.className = "pin " + ({ check: "check", alert: "alert", responding: "alert" }[state] || "");
  pin.getTooltip()?.getElement()?.classList.toggle("alert", state === "alert" || state === "responding");
  const searching = Date.now() < S.searchUntil;
  for (const r of conf.receivers) {
    const v = view(r.id);
    const m = rx[r.id].getElement()?.querySelector(".rx");
    if (m && m.dataset.v !== v) { m.className = "rx " + v; m.dataset.v = v; }
    const showTip = ["ringing", "accepted"].includes(v);
    const tipHtml = `${esc(first(r.name))}<span class="m">${meters(dist(r))}</span>`;
    const tip = rx[r.id].getTooltip();
    if (showTip && !tip) rx[r.id].bindTooltip(tipHtml, { permanent: true, direction: "right", offset: [10, 0], className: "tag" }).openTooltip();
    if (!showTip && tip) rx[r.id].unbindTooltip();
    rx[r.id].getTooltip()?.getElement()?.classList.toggle("accepted", v === "accepted");
    rx[r.id].getTooltip()?.getElement()?.classList.toggle("ringing", v === "ringing");
    setLine(r, searching ? "scan" : v === "ringing" ? "ring" : v === "accepted" ? "route" : null);
  }
}

function setLine(r, kind) {
  const have = lines[r.id];
  if (have?.kind === kind) return;
  if (have) { have.line.remove(); delete lines[r.id]; }
  if (!kind) return;
  const geo = kind === "route" ? [[r.lat, r.lng], ...(conf.routes[r.id]?.geometry || []), [conf.home.lat, conf.home.lng]]
                               : [[conf.home.lat, conf.home.lng], [r.lat, r.lng]];
  const line = L.polyline(geo, { className: `line-${kind}`, weight: kind === "route" ? 4 : 1.5, interactive: false,
                                 lineCap: "round", lineJoin: "round" }).addTo(map);
  lines[r.id] = { kind, line };
  if (kind === "route") map.flyToBounds(L.latLngBounds(geo).pad(1.2), { duration: 1.0, maxZoom: 17 });
}

function clearLines() {
  for (const id of Object.keys(lines)) { lines[id].line.remove(); delete lines[id]; }
}

function popupHtml() {
  const active = S.incident && !S.incident.closed;
  if (mode !== "manual") {
    return `<div class="pop"><h3>${esc(conf.resident)} · ${esc(conf.unit)}</h3>
      <p>Live mode: alerts come from the robot's camera. Switch to <b>Manual</b> to send one from here.</p></div>`;
  }
  return `<div class="pop"><h3>${esc(conf.resident)} · ${esc(conf.unit)}</h3>
    <p>Send an alert through the robot's real pipeline: it checks in with ${esc(conf.resident)} by voice, and if there's
       no answer it dispatches 911, ${esc(conf.family)} and the nearest neighbors.</p>
    <button class="btn-alert" id="pop-send" ${active ? "disabled" : ""}>${active ? "Incident in progress" : "Send alert"}</button>
    ${active ? `<button class="btn-reset" id="pop-reset">Reset incident</button>` : ""}</div>`;
}

function setupMap() {
  map = L.map("map", { zoomControl: true, zoomSnap: 0.25 }).setView([conf.home.lat, conf.home.lng], 16);
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors · routes: OSRM',
  }).addTo(map);
  const icon = L.divIcon({ className: "", iconSize: [22, 22], iconAnchor: [11, 11], html: '<div class="pin"><span class="halo"></span><span class="core"></span></div>' });
  pin = L.marker([conf.home.lat, conf.home.lng], { icon, zIndexOffset: 1000, title: `${conf.resident}'s home` }).addTo(map)
    .bindTooltip(`${esc(conf.resident)}<span class="m">${esc(conf.unit)}</span>`, { permanent: true, direction: "left", offset: [-14, 0], className: "tag sandy" });
  pin.bindPopup(popupHtml, { offset: [0, -8], closeButton: true });
  pin.on("popupopen", () => {
    $("pop-send")?.addEventListener("click", () => { command("trigger"); pin.closePopup(); });
    $("pop-reset")?.addEventListener("click", () => { command("reset"); pin.closePopup(); });
  });
  const pts = [[conf.home.lat, conf.home.lng]];
  for (const r of conf.receivers) {
    const i = L.divIcon({ className: "", iconSize: [14, 14], iconAnchor: [7, 7], html: '<div class="rx idle"></div>' });
    rx[r.id] = L.marker([r.lat, r.lng], { icon: i, keyboard: false, title: `${r.name} · CareBot receiver · ${meters(dist(r))}` }).addTo(map);
    pts.push([r.lat, r.lng]);
  }
  map.fitBounds(L.latLngBounds(pts).pad(0.2));
}

function setMode(m) {
  mode = m;
  try { localStorage.setItem("carebot-console-mode", m); } catch (e) { /* private mode: not remembered */ }
  $("app").classList.toggle("manual", m === "manual");
  $("mode-live").setAttribute("aria-pressed", String(m === "live"));
  $("mode-manual").setAttribute("aria-pressed", String(m === "manual"));
  $("mode-hint").textContent = m === "manual" ? `Manual: click ${conf.resident}'s home to send an alert.`
                                              : `Live: following ${conf.unit}. Alerts come from its camera.`;
  pin.closePopup();
}

// ---------------------------------------------------------------- wiring

function connect() {
  const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
  let replay = true;
  ws.onopen = () => {
    S = fresh(); clearLines(); drawn = { log: -1, table: "" }; replay = true;
    setTimeout(() => { replay = false; S.searchUntil = 0; render(); }, 300);
  };
  ws.onmessage = (ev) => {
    const { topic, msg } = JSON.parse(ev.data);
    apply(topic, msg);
    if (replay) S.searchUntil = 0; else render();  // history: no ranking animation
  };
  ws.onclose = () => setTimeout(connect, 1500);
}

async function command(kind) {
  try { await fetch(`/api/cmd/${kind}`, { method: "POST" }); } catch (e) { console.warn(e); }
}

async function main() {
  conf = await (await fetch("/api/config")).json();
  $("unit").textContent = `${conf.unit} · ${conf.home.street || ""}`;
  $("family-label").textContent = conf.family;
  $("sim-note").textContent = conf.simulated ? "Neighbors without a phone are simulated." : "";
  setupMap();
  let saved = "live";
  try { saved = localStorage.getItem("carebot-console-mode") || "live"; } catch (e) { /* ignore */ }
  setMode(saved);
  $("mode-live").onclick = () => setMode("live");
  $("mode-manual").onclick = () => setMode("manual");
  $("show-raw").onclick = () => { $("raw").hidden = !$("raw").hidden; render(); };
  connect();
  render();
  setInterval(render, 500);
}

main();
