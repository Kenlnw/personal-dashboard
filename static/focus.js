// Focus sessions (a timer that lives on the server) and exams.
// Loaded before app.js; uses its helpers ($, esc, api, send, md, fmt, openModal, card, tile,
// columnChart, barList, tableView, dayLabel, dayLong, todayISO) and tasks.js (popMenu, toast,
// recurringModal, T, loadTasks, tDraw) only at call time.
"use strict";

let FOCUS = null;                 // the running session (+ t0 = local start time) or null
let focusTimer = null;

const focusFmt = (sec) => {
  sec = Math.max(0, Math.floor(sec));
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}` : `${m}:${String(s).padStart(2, "0")}`;
};
const focusElapsed = () => (FOCUS ? (Date.now() - FOCUS.t0) / 1000 : 0);
const mins = (m) => { if (m == null) return "—"; const t = Math.round(m); return t < 60 ? `${t} min` : `${Math.floor(t / 60)} h ${String(t % 60).padStart(2, "0")} min`; };

function setFocus(s) {
  const same = FOCUS && s && FOCUS.id === s.id;
  FOCUS = s ? { ...s, t0: Date.now() - s.elapsed_seconds * 1000, notified: same ? FOCUS.notified : false } : null;
  drawFocusBar();
  document.querySelectorAll("[data-focus-btn]").forEach(syncFocusButton);
}

async function focusRefresh() {
  try { setFocus((await api("/focus/current")).active); } catch { /* not signed in / offline */ }
}

function drawFocusBar() {
  let bar = document.querySelector(".focus-bar");
  clearInterval(focusTimer);
  if (!FOCUS) { bar?.remove(); return; }
  if (!bar) {
    document.body.insertAdjacentHTML("beforeend", `<div class="focus-bar" role="status" aria-live="off"></div>`);
    bar = document.querySelector(".focus-bar");
  }
  const link = FOCUS.task ? `#/tasks/${FOCUS.task.id}` : FOCUS.workspace ? `#/workspaces/${FOCUS.workspace}` : "#/focus";
  bar.innerHTML = `<span class="fb-dot" aria-hidden="true"></span>
    <a class="fb-title" href="${esc(link)}" title="${esc(FOCUS.title)}">${esc(FOCUS.title)}</a>
    <span class="fb-time"></span>
    <button class="fb-stop" data-focus-stop>Stop</button>`;
  bar.querySelector("[data-focus-stop]").onclick = () => stopFocusDialog();
  const tick = () => {
    const el = focusElapsed(), plan = FOCUS.planned_minutes;
    const over = plan && el >= plan * 60;
    bar.querySelector(".fb-time").textContent = plan ? `${focusFmt(el)} / ${plan}:00` : focusFmt(el);
    bar.classList.toggle("done", !!over);
    if (over && !FOCUS.notified) { FOCUS.notified = true; focusAlarm(); }
  };
  tick();
  focusTimer = setInterval(tick, 1000);
}

function focusAlarm() {
  const text = `${FOCUS.planned_minutes} min done: ${FOCUS.title}`;
  try { if ("Notification" in window && Notification.permission === "granted") new Notification("Focus time is up", { body: text, tag: "focus" }); } catch { /* ignore */ }
  try {                                               // two short beeps
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    [0, 0.35].forEach((t) => {
      const o = ctx.createOscillator(), g = ctx.createGain();
      o.frequency.value = 880; g.gain.value = 0.08;
      o.connect(g).connect(ctx.destination); o.start(ctx.currentTime + t); o.stop(ctx.currentTime + t + 0.18);
    });
  } catch { /* no audio */ }
  toast(`⏰ ${esc(text)}`, 8000);
}

/** Start a session: pick the length from a small menu. `what` = {task_id} or {workspace} or {}. */
function startFocus(what, anchor) {
  if (FOCUS) { stopFocusDialog(() => startFocus(what, anchor)); return; }
  popMenu(anchor, {
    placeholder: "Focus for…",
    items: [[25, "25 min"], [50, "50 min"], [90, "90 min"], [0, "No time limit"]].map(([v, label], i) => ({ value: v, label, icon: "▶", key: String(i + 1) })),
    onPick: async (v) => {
      if ("Notification" in window && Notification.permission === "default") Notification.requestPermission().catch(() => {});
      try {
        setFocus(await send("/focus/start", "POST", { ...what, planned_minutes: +v || null }));
        document.dispatchEvent(new CustomEvent("focus-changed"));
      } catch (e) { toast(esc(e.message)); }
    },
  });
}

/** Stop dialog: length (editable), a note, and "mark the task done". */
function stopFocusDialog(after = null) {
  if (!FOCUS) return;
  const s = FOCUS, el = Math.round(focusElapsed() / 60);
  const m = openModal(after ? "Stop the running session first?" : "Stop focus");
  m.el.classList.add("t-modal");
  const taskOpen = s.task && !["done", "canceled"].includes(s.task.status);
  m.body.innerHTML = `<form class="t-new" id="fs-form">
      <div class="fs-head"><strong>${esc(s.title)}</strong>${s.workspace_name ? `<span class="pill">${esc(s.workspace_name)}</span>` : ""}</div>
      <label class="field">Minutes <input type="number" name="minutes" min="0" max="1440" step="1" value="${el}"></label>
      ${el > 180 ? `<p class="desc">That's over 3 hours. If you forgot to stop, set the real length.</p>` : ""}
      <textarea name="note" rows="2" placeholder="What did you do? (optional) e.g. finished units 1–2, stuck on genitive plural"></textarea>
      ${taskOpen ? `<label class="check"><input type="checkbox" name="done"> Mark ${esc(s.task.key)} done</label>` : ""}
      <div class="t-new-foot"><button type="button" class="btn" data-discard>Discard session</button><span class="spacer" style="flex:1"></span>
        <button class="btn primary" type="submit">Save</button></div>
      <div class="form-msg"></div></form>`;
  const f = $("#fs-form", m.body);
  const finish = async (body) => {
    try {
      const r = await send("/focus/stop", "POST", body);
      m.close(); setFocus(null);
      toast(body.discard ? "Session discarded" : `Saved ${mins(r.minutes)}${s.task ? ` on ${esc(s.task.key)}` : ""}`);
      document.dispatchEvent(new CustomEvent("focus-changed"));
      if (typeof loadTasks === "function" && $("#t-root")) { await loadTasks(); tDraw(); if (tDrawer) drawerRefresh(); }
      after?.();
    } catch (e) { $(".form-msg", f).textContent = e.message; }
  };
  f.onsubmit = (e) => { e.preventDefault(); finish({ minutes: +f.elements.minutes.value, note: f.elements.note.value, mark_done: !!f.elements.done?.checked }); };
  f.querySelector("[data-discard]").onclick = () => { if (confirm("Throw this session away? It won't count.")) finish({ discard: true }); };
  setTimeout(() => f.elements.note.focus());
}

function syncFocusButton(b) {
  const tid = b.dataset.taskId ? +b.dataset.taskId : null;
  const mine = FOCUS && (tid ? FOCUS.task_id === tid : !FOCUS.task_id && FOCUS.workspace === (b.dataset.workspace || null));
  b.textContent = mine ? "■ Stop" : "▶ Focus";
  b.classList.toggle("on", !!mine);
}

/** A ▶ Focus / ■ Stop button. Wire it with wireFocusButton after inserting. */
const focusButton = ({ task_id = null, workspace = null, cls = "icon-btn" } = {}) =>
  `<button class="${cls} focus-btn" data-focus-btn${task_id ? ` data-task-id="${task_id}"` : ""}${workspace ? ` data-workspace="${esc(workspace)}"` : ""}>▶ Focus</button>`;
function wireFocusButtons(root) {
  root.querySelectorAll("[data-focus-btn]").forEach((b) => {
    syncFocusButton(b);
    b.onclick = (e) => {
      e.stopPropagation();
      if (b.classList.contains("on")) stopFocusDialog();
      else startFocus(b.dataset.taskId ? { task_id: +b.dataset.taskId } : b.dataset.workspace ? { workspace: b.dataset.workspace } : {}, b);
    };
  });
}

// ---------- exams ----------
const EXAM_KINDS = { exam: "Exam", quiz: "Quiz", report: "Report", presentation: "Presentation", other: "Other" };
const daysText = (n) => (n === 0 ? "today" : n === 1 ? "tomorrow" : n > 0 ? `in ${n} days` : n === -1 ? "yesterday" : `${-n} days ago`);
const dayBefore = (d) => { const x = parseDay(d); x.setDate(x.getDate() - 1); return isoOf(x); };

/** Exams list + add form. On a workspace page pass {workspace, onPlan}; on the Focus page pass {}. */
async function examsPanel(host, { workspace = null, onPlan = null, compact = false } = {}) {
  let list = [], wss = [];
  const load = async () => {
    [list, wss] = await Promise.all([api(`/exams${workspace ? `?workspace=${encodeURIComponent(workspace)}` : ""}`),
      workspace ? Promise.resolve([]) : api("/tasks?closed_days=0").then((d) => d.workspaces).catch(() => [])]);
    draw();
  };
  const row = (e) => {
    const p = e.prep, up = e.days_left >= 0;
    const prep = `${mins(p.minutes)} studied · ${p.sessions} session${p.sessions === 1 ? "" : "s"}${p.review_scheduled ? ` · review ${p.review_done}/${p.review_scheduled}` : ""}`;
    const result = e.score != null ? `<span class="ex-score">${e.score}/${e.max_score ?? 100}</span>${e.percent != null ? ` <span class="meta">${e.percent}%</span>` : ""}${e.grade ? ` <span class="pill">${esc(e.grade)}</span>` : ""}` : "";
    return `<li class="ex ${up ? "up" : "past"}" data-exam="${e.id}">
      <div class="ex-when ${up && e.days_left <= 7 ? "soon" : ""}"><strong>${up ? (e.days_left === 0 ? "Today" : e.days_left) : esc(dayLabel(e.date))}</strong>${up && e.days_left > 0 ? `<span>day${e.days_left === 1 ? "" : "s"}</span>` : ""}</div>
      <div class="main">
        <div class="title">${esc(e.title)} <span class="pill">${esc(EXAM_KINDS[e.kind] || e.kind)}</span>${!workspace && e.workspace_name ? ` <span class="meta">${esc(e.workspace_name)}</span>` : ""}</div>
        <div class="meta">${esc(dayLong(e.date))} · ${esc(daysText(e.days_left))}${e.scope ? ` · ${esc(e.scope)}` : ""}</div>
        <div class="meta">${esc(prep)}</div>
        ${result ? `<div class="ex-result">${result} <span class="meta">after ${esc(mins(p.minutes))} of focus</span></div>` : ""}
        ${e.reflection ? `<div class="ex-refl md">${md(e.reflection)}</div>` : ""}
        <div class="ex-actions">
          ${up && e.workspace ? `<button class="link" data-plan="${e.id}">Plan for this exam</button> · <button class="link" data-repeat="${e.id}">Repeat until exam</button> · ` : ""}
          ${!up || e.score != null ? `<button class="link" data-result="${e.id}">${e.score != null ? "Edit result" : "Enter result"}</button> · ` : ""}
          <button class="link danger-link" data-del-exam="${e.id}">Delete</button>
        </div>
        <form class="ex-form" data-result-form="${e.id}" hidden>
          <label class="field">Score <input type="number" name="score" step="any" value="${e.score ?? ""}" style="width:80px"></label>
          <label class="field">/ <input type="number" name="max" step="any" min="0.1" value="${e.max_score ?? 100}" style="width:70px"></label>
          <label class="field">Grade <input name="grade" maxlength="20" value="${esc(e.grade)}" placeholder="optional" style="width:80px"></label>
          <textarea name="reflection" rows="2" placeholder="How did it go? What would you study differently?">${esc(e.reflection)}</textarea>
          <button class="btn small primary">Save result</button>
        </form>
      </div></li>`;
  };
  const draw = () => {
    const missing = list.filter((e) => e.days_left < 0 && e.score == null).length;
    host.innerHTML = `${list.length ? `<ul class="ex-list">${(compact ? list.filter((e) => e.days_left >= -14) : list).map(row).join("")}</ul>` : `<div class="empty">No exams yet${workspace ? " for this subject" : ""}.</div>`}
      ${missing ? `<p class="desc">${missing} past exam${missing === 1 ? " has" : "s have"} no result yet.</p>` : ""}
      <details class="ex-add"><summary>+ Add exam</summary>
        <form class="ex-form" id="ex-new">
          <input name="title" required maxlength="200" placeholder="e.g. Russian test, Midterm, Report 3">
          <div class="row-form">
            <label class="field">Kind <select name="kind">${Object.entries(EXAM_KINDS).map(([k, v]) => `<option value="${k}">${v}</option>`).join("")}</select></label>
            <label class="field">Date <input type="date" name="date" required min="2020-01-01"></label>
            ${workspace ? "" : `<label class="field">Subject <select name="workspace"><option value="">None</option>${wss.map((w) => `<option value="${esc(w.slug)}">${esc(w.name)}</option>`).join("")}</select></label>`}
          </div>
          <input name="scope" maxlength="1000" placeholder="Scope (optional), e.g. units 1–5, chapters 3–4">
          <button class="btn primary small">Add exam</button><div class="form-msg"></div>
        </form></details>`;
    renderMath(host);
    const nf = $("#ex-new", host);
    nf.onsubmit = async (ev) => {
      ev.preventDefault();
      try {
        await send("/exams", "POST", { title: nf.elements.title.value, kind: nf.elements.kind.value, date: nf.elements.date.value,
          scope: nf.elements.scope.value, workspace: workspace || nf.elements.workspace?.value || null });
        toast("Exam added"); load();
      } catch (err) { $(".form-msg", nf).textContent = err.message; }
    };
    host.querySelectorAll("[data-result]").forEach((b) => b.onclick = () => { const f = host.querySelector(`[data-result-form="${b.dataset.result}"]`); f.hidden = !f.hidden; });
    host.querySelectorAll("[data-result-form]").forEach((f) => f.onsubmit = async (ev) => {
      ev.preventDefault();
      const score = f.elements.score.value;
      await send(`/exams/${f.dataset.resultForm}`, "PATCH", { score: score === "" ? null : +score, max_score: +f.elements.max.value || 100,
        grade: f.elements.grade.value, reflection: f.elements.reflection.value });
      toast("Result saved"); load();
    });
    host.querySelectorAll("[data-del-exam]").forEach((b) => b.onclick = async () => {
      if (!confirm("Delete this exam? Your focus sessions stay.")) return;
      await send(`/exams/${b.dataset.delExam}`, "DELETE"); load();
    });
    host.querySelectorAll("[data-plan]").forEach((b) => b.onclick = () => {
      const e = list.find((x) => x.id === +b.dataset.plan);
      if (onPlan) onPlan(e); else location.hash = `#/workspaces/${e.workspace}?plan=${e.id}`;
    });
    host.querySelectorAll("[data-repeat]").forEach((b) => b.onclick = () => {
      const e = list.find((x) => x.id === +b.dataset.repeat);
      recurringModal({ title: `Review: ${e.title}`, workspace: e.workspace, labels: ["review"], priority: 3, end: dayBefore(e.date) });
    });
  };
  await load();
  return { reload: load };
}

// ---------- the Focus page ----------
async function viewFocus(main) {
  main.innerHTML = `<div class="top"><h1>Focus</h1><span class="sub">Study time and exams</span><span class="spacer"></span>
      <button class="btn primary" id="fp-start">▶ Start focus</button></div>
    <div class="grid" id="fp-body"><div class="card span-12 empty">Loading…</div></div>`;
  const s = await api("/focus/summary?days=14");
  const exams = await api("/exams").catch(() => []);
  const next = exams.find((e) => e.days_left >= 0);
  const days = s.per_day;
  $("#fp-body", main).innerHTML = `
    ${tile("Today", mins(s.today_minutes), FOCUS ? "a session is running" : s.today_minutes ? "focused" : "nothing yet")}
    ${tile("Last 7 days", mins(s.week_minutes), `${s.week_by_workspace.length} subject${s.week_by_workspace.length === 1 ? "" : "s"}`)}
    ${tile("Streak", `${s.streak_days} day${s.streak_days === 1 ? "" : "s"}`, s.streak_days ? "in a row with a session" : "start one today")}
    ${tile("Next exam", next ? (next.days_left === 0 ? "Today" : `${next.days_left} d`) : "—", next ? `${next.title}${next.workspace_name ? ` · ${next.workspace_name}` : ""}` : "none planned")}
    ${card("Focus time per day", "Last 14 days, all subjects", `<div id="fp-chart"></div>${tableView(days.map((d) => [d.date, mins(d.minutes)]).reverse(), ["Day", "Focus"])}`, 8)}
    ${card("Last 7 days by subject", "Focus time per workspace", s.week_by_workspace.length ? barList(s.week_by_workspace.map((w) => ({ name: w.name, value: w.minutes })), mins) : `<div class="empty">No sessions this week</div>`, 4)}
    <section class="card span-12"><div class="list-head"><h2>Exams</h2><span class="count">${exams.filter((e) => e.days_left >= 0).length} upcoming</span></div><div id="fp-exams"></div></section>
    <section class="card span-12"><div class="list-head"><h2>Recent sessions</h2></div><div id="fp-sessions"></div></section>`;
  columnChart($("#fp-chart", main), days.map((d) => ({ key: d.date, label: dayLabel(d.date), tip: dayLong(d.date), y: d.minutes ? d.minutes / 60 : null })),
    { valueFmt: (h) => mins(h * 60), axisFmt: (h) => `${h}h`, emptyText: "No focus sessions in the last 14 days" });
  examsPanel($("#fp-exams", main));
  const sess = $("#fp-sessions", main);
  sess.innerHTML = s.recent.length ? `<ul class="list compact">${s.recent.map((x) => `<li data-sid="${x.id}"><div class="main">
      <div class="title">${x.task ? `<a href="#/tasks/${x.task.id}">${esc(x.title)}</a>` : esc(x.title)} ${x.workspace_name ? `<span class="meta">· ${esc(x.workspace_name)}</span>` : ""}</div>
      <div class="meta">${esc(dayLabel(x.started_at.slice(0, 10)))} ${esc(x.started_at.slice(11, 16))} · <strong>${esc(mins(x.minutes))}</strong>${x.planned_minutes ? ` of ${x.planned_minutes} planned` : ""}${x.note ? ` · ${esc(x.note)}` : ""}</div></div>
      <button class="btn small" data-edit-s="${x.id}" data-m="${x.minutes}">Edit</button><button class="x" data-del-s="${x.id}" aria-label="Delete session">×</button></li>`).join("")}</ul>`
    : `<div class="empty">No sessions yet. Press ▶ Focus on a task, or Start focus above.</div>`;
  sess.querySelectorAll("[data-edit-s]").forEach((b) => b.onclick = async () => {
    const v = prompt("Minutes for this session:", Math.round(+b.dataset.m));
    if (v == null || v === "" || isNaN(+v)) return;
    await send(`/focus/sessions/${b.dataset.editS}`, "PATCH", { minutes: +v }); viewFocus(main);
  });
  sess.querySelectorAll("[data-del-s]").forEach((b) => b.onclick = async () => {
    if (!confirm("Delete this session?")) return;
    await send(`/focus/sessions/${b.dataset.delS}`, "DELETE"); viewFocus(main);
  });
  $("#fp-start", main).onclick = async (e) => {
    const btn = e.currentTarget;
    if (FOCUS) { stopFocusDialog(); return; }
    const wss = (await api("/tasks?closed_days=0")).workspaces;
    popMenu(btn, { placeholder: "Focus on which subject?", items: [{ value: "", label: "No subject", icon: "·" }, ...wss.map((w) => ({ value: w.slug, label: w.name, icon: "📘" }))],
      onPick: (v) => setTimeout(() => startFocus(v ? { workspace: v } : {}, btn)) });
  };
}

// keep every device in sync: refresh when the tab comes back, and every minute
document.addEventListener("visibilitychange", () => { if (!document.hidden) focusRefresh(); });
setInterval(() => { if (!document.hidden) focusRefresh(); }, 60000);
document.addEventListener("focus-changed", () => { if (location.hash.startsWith("#/focus")) viewFocus($("#main")); });
