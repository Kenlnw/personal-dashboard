// Tasks: a small Linear-style tracker (list + board, quick property menus, keyboard first).
// Loaded before app.js; uses its helpers ($, esc, api, send, md, store, openModal, ago, renderMath)
// only at call time.
"use strict";

const T_ORDER = ["in_progress", "todo", "backlog", "done", "canceled"];          // list/board order
const T_MENU_ORDER = ["backlog", "todo", "in_progress", "done", "canceled"];     // status menu (keys 1-5)
const T_VIEWS = {
  active: { name: "Active", statuses: ["in_progress", "todo"] },
  backlog: { name: "Backlog", statuses: ["backlog"] },
  all: { name: "All", statuses: T_ORDER },
};
const T_STATUS = { backlog: "Backlog", todo: "Todo", in_progress: "In Progress", done: "Done", canceled: "Canceled" };
const T_PRIO = ["No priority", "Urgent", "High", "Medium", "Low"];
const T_CLOSED = new Set(["done", "canceled"]);
const T_SORTS = { manual: "Manual", priority: "Priority", due: "Due date", updated: "Last updated", created: "Newest" };

let T = null;                    // last GET /tasks
let tDrawer = null;              // id of the task open in the drawer
// read localStorage directly: app.js (and its `store`) loads after this file
const tUi = Object.assign({ view: "active", layout: "list", order: "manual", project: "", priority: "", label: "", workspace: "", q: "", closedAll: false },
  (() => { try { return JSON.parse(localStorage.getItem("tasks.ui")) || {}; } catch { return {}; } })(), { q: "" });
const tSave = () => store.set("tasks.ui", { ...tUi, q: "" });
let tSel = null;                 // selected (keyboard) task id
let tFlat = [];                  // ids in the order they are drawn

// ---------- small helpers ----------
const isoOf = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const addDays = (n) => { const d = new Date(); d.setDate(d.getDate() + n); return isoOf(d); };
const tById = (id) => T?.tasks.find((t) => t.id === id);
const tProject = (id) => T?.projects.find((p) => p.id === id);
const tWs = (slug) => slug && (T?.workspaces || []).find((w) => w.slug === slug);
const WS_ICON = `<svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20V3H6.5A2.5 2.5 0 0 0 4 5.5z"/><path d="M4 19.5A2.5 2.5 0 0 0 6.5 22H20v-5"/></svg>`;
const wsChip = (w) => (w ? `<span class="t-proj t-wsc" title="Workspace">${WS_ICON}${esc(w.name)}</span>` : "");
const tLabelColor = (name) => T?.labels.find((l) => l.name.toLowerCase() === name.toLowerCase())?.color || "var(--muted)";
function dueText(due) {
  if (!due) return "";
  const diff = Math.round((parseDay(due) - parseDay(T.today)) / 864e5);
  if (diff === 0) return "Today";
  if (diff === 1) return "Tomorrow";
  if (diff === -1) return "Yesterday";
  return diff > 1 && diff < 7 ? WD[parseDay(due).getDay()] : dayLabel(due);
}
const dueClass = (t) => (!t.due || T_CLOSED.has(t.status) ? "" : t.due < T.today ? " overdue" : t.due === T.today ? " today" : "");

function statusIcon(s) {
  const c = `<svg class="st st-${s}" viewBox="0 0 14 14" width="14" height="14" aria-hidden="true">`;
  switch (s) {
    case "backlog": return `${c}<circle cx="7" cy="7" r="6" fill="none" stroke="currentColor" stroke-width="1.5" stroke-dasharray="1.4 1.74"/></svg>`;
    case "todo": return `${c}<circle cx="7" cy="7" r="6" fill="none" stroke="currentColor" stroke-width="1.5"/></svg>`;
    case "in_progress": return `${c}<circle cx="7" cy="7" r="6" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M7 3.5a3.5 3.5 0 0 1 0 7z" fill="currentColor"/></svg>`;
    case "done": return `${c}<circle cx="7" cy="7" r="6.5" fill="currentColor"/><path d="M4.3 7.2l1.8 1.8 3.6-3.7" fill="none" stroke="var(--surface)" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
    default: return `${c}<circle cx="7" cy="7" r="6.5" fill="currentColor"/><path d="M5 5l4 4M9 5l-4 4" stroke="var(--surface)" stroke-width="1.5" stroke-linecap="round"/></svg>`;
  }
}
function prioIcon(p) {
  const c = `<svg class="pr pr-${p}" viewBox="0 0 14 14" width="14" height="14" aria-hidden="true">`;
  if (p === 0) return `${c}${[1.5, 5.5, 9.5].map((x) => `<rect x="${x}" y="6.25" width="3" height="1.5" rx=".6" fill="currentColor"/>`).join("")}</svg>`;
  if (p === 1) return `${c}<rect x="1" y="1" width="12" height="12" rx="3" fill="currentColor"/><rect x="6.25" y="3.5" width="1.5" height="4.5" rx=".5" fill="var(--surface)"/><rect x="6.25" y="9.25" width="1.5" height="1.5" rx=".5" fill="var(--surface)"/></svg>`;
  const on = 5 - p;     // high 3 bars, medium 2, low 1
  return `${c}${[[1.5, 8, 4], [5.5, 5, 7], [9.5, 2, 10]].map(([x, y, h], i) => `<rect x="${x}" y="${y}" width="3" height="${h}" rx="1" fill="currentColor" opacity="${i < on ? 1 : 0.28}"/>`).join("")}</svg>`;
}
const labelChip = (name) => `<span class="t-label"><i style="background:${esc(tLabelColor(name))}"></i>${esc(name)}</span>`;
const projectChip = (p) => (p ? `<span class="t-proj"><i style="background:${esc(p.color)}"></i>${esc(p.name)}</span>` : "");

function toast(html, ms = 3500) {
  document.querySelector(".t-toast")?.remove();
  document.body.insertAdjacentHTML("beforeend", `<div class="t-toast" role="status">${html}</div>`);
  const el = document.querySelector(".t-toast");
  setTimeout(() => el.remove(), ms);
  return el;
}

// ---------- popover menu (status, priority, labels, project, due, parent) ----------
function popMenu(anchor, { items, multi = false, selected = [], onPick, placeholder = "Filter…", onCreate, footer }) {
  document.querySelector(".pop")?.remove();
  const sel = new Set(selected.map(String));
  document.body.insertAdjacentHTML("beforeend", `<div class="pop" role="dialog"><input class="pop-q" placeholder="${esc(placeholder)}" aria-label="${esc(placeholder)}"><div class="pop-list" role="listbox"></div>${footer ? `<div class="pop-foot">${footer}</div>` : ""}</div>`);
  const pop = document.querySelector(".pop"), q = $(".pop-q", pop), listEl = $(".pop-list", pop);
  let shown = [], active = 0;
  const close = () => { pop.remove(); document.removeEventListener("mousedown", outside, true); anchor.focus?.({ preventScroll: true }); };
  const outside = (e) => { if (!pop.contains(e.target)) close(); };
  const draw = () => {
    const term = q.value.trim().toLowerCase();
    shown = items.filter((i) => !term || i.label.toLowerCase().includes(term));
    if (onCreate && term && !items.some((i) => i.label.toLowerCase() === term)) shown.push({ create: q.value.trim(), label: `Create “${q.value.trim()}”`, icon: "+" });
    active = Math.min(active, Math.max(shown.length - 1, 0));
    listEl.innerHTML = shown.length ? shown.map((i, n) => `<div class="pop-item${n === active ? " on" : ""}" role="option" data-n="${n}" aria-selected="${sel.has(String(i.value))}">
        ${multi && !i.create ? `<span class="pop-check">${sel.has(String(i.value)) ? "✓" : ""}</span>` : ""}<span class="pop-ico">${i.icon || ""}</span>
        <span class="pop-label">${esc(i.label)}</span>${!multi && sel.has(String(i.value)) ? `<span class="pop-tick">✓</span>` : ""}${i.key ? `<kbd>${esc(i.key)}</kbd>` : ""}</div>`).join("")
      : `<div class="pop-empty">Nothing matches</div>`;
  };
  const pick = (i) => {
    if (!i) return;
    if (i.create && multi) {                 // new label: add it, keep the menu open for more
      items.push({ value: i.create, label: i.create, icon: `<span class="t-dot" style="background:var(--muted)"></span>` });
      sel.add(i.create); onCreate(i.create); q.value = ""; draw(); q.focus(); return;
    }
    if (i.create) { onCreate(i.create); close(); return; }
    if (multi) {
      const k = String(i.value); sel.has(k) ? sel.delete(k) : sel.add(k);
      onPick([...sel]); q.value = ""; draw(); q.focus();
    } else { close(); onPick(i.value); }
  };
  listEl.onmousedown = (e) => e.preventDefault();                     // keep focus in the input
  listEl.onclick = (e) => { const el = e.target.closest("[data-n]"); if (el) pick(shown[+el.dataset.n]); };
  listEl.onmousemove = (e) => { const el = e.target.closest("[data-n]"); if (el && +el.dataset.n !== active) { active = +el.dataset.n; draw(); } };
  q.oninput = () => { active = 0; draw(); };
  q.onkeydown = (e) => {
    e.stopPropagation();
    if (e.key === "Escape") { e.preventDefault(); close(); }
    else if (e.key === "ArrowDown" || (e.key === "j" && e.ctrlKey)) { e.preventDefault(); active = (active + 1) % Math.max(shown.length, 1); draw(); }
    else if (e.key === "ArrowUp" || (e.key === "k" && e.ctrlKey)) { e.preventDefault(); active = (active - 1 + shown.length) % Math.max(shown.length, 1); draw(); }
    else if (e.key === "Enter") { e.preventDefault(); pick(shown[active]); }
    else if (!q.value && /^[0-9]$/.test(e.key) && items.some((i) => i.key === e.key)) { e.preventDefault(); pick(items.find((i) => i.key === e.key)); }
  };
  draw();
  const r = anchor.getBoundingClientRect(), w = pop.offsetWidth, h = pop.offsetHeight;
  pop.style.left = `${Math.max(8, Math.min(r.left, innerWidth - w - 8))}px`;
  pop.style.top = `${r.bottom + h + 8 > innerHeight && r.top - h - 4 > 0 ? r.top - h - 4 : Math.min(r.bottom + 4, innerHeight - h - 8)}px`;
  setTimeout(() => document.addEventListener("mousedown", outside, true));
  q.focus({ preventScroll: true });
  return { el: pop, close };
}

/** Open the menu for one property. `cur` is the task (or draft); `apply(changes)` saves. */
function propMenu(kind, anchor, cur, apply) {
  if (kind === "status") return popMenu(anchor, { placeholder: "Change status…", selected: [cur.status], onPick: (v) => apply({ status: v }),
    items: T_MENU_ORDER.map((s, i) => ({ value: s, label: T_STATUS[s], icon: statusIcon(s), key: String(i + 1) })) });
  if (kind === "priority") return popMenu(anchor, { placeholder: "Set priority…", selected: [cur.priority], onPick: (v) => apply({ priority: +v }),
    items: [0, 1, 2, 3, 4].map((p) => ({ value: p, label: T_PRIO[p], icon: prioIcon(p), key: String(p) })) });
  if (kind === "project") return popMenu(anchor, { placeholder: "Move to project…", selected: [cur.project_id ?? "null"],
    onPick: (v) => apply({ project_id: v === "null" || v === null ? null : +v }),
    onCreate: async (name) => { const p = await send("/tasks/projects", "POST", { name, color: randomColor() }); T.projects.push({ ...p, open: 0, total: 0 }); apply({ project_id: p.id }); },
    items: [{ value: "null", label: "No project", icon: `<span class="t-dot" style="border:1px dashed var(--muted)"></span>` },
      ...T.projects.filter((p) => !p.archived || p.id === cur.project_id).map((p) => ({ value: p.id, label: p.name, icon: `<span class="t-dot" style="background:${esc(p.color)}"></span>` }))] });
  if (kind === "workspace") return popMenu(anchor, { placeholder: "Link to workspace…", selected: [cur.workspace ?? "null"],
    onPick: (v) => apply({ workspace: v === "null" ? null : v }),
    items: [{ value: "null", label: "No workspace", icon: "×" }, ...(T.workspaces || []).map((w) => ({ value: w.slug, label: w.name, icon: WS_ICON }))] });
  if (kind === "labels") {
    let now = [...cur.labels];
    return popMenu(anchor, { multi: true, placeholder: "Add labels…", selected: now,
      onPick: (v) => { now = v; apply({ labels: v }); },
      onCreate: (name) => { now = [...now, name]; T.labels.push({ name, color: "var(--muted)" }); apply({ labels: now }); },
      items: T.labels.map((l) => ({ value: l.name, label: l.name, icon: `<span class="t-dot" style="background:${esc(l.color)}"></span>` })) });
  }
  if (kind === "due") {
    const fri = (5 - new Date().getDay() + 7) % 7, mon = (8 - new Date().getDay()) % 7 || 7;
    const m = popMenu(anchor, { placeholder: "Due date…", selected: [cur.due ?? "null"], onPick: (v) => apply({ due: v === "null" ? null : v }),
      footer: `<label class="pop-date">Pick a date <input type="date" value="${esc(cur.due || "")}"></label>`,
      items: [["Today", addDays(0)], ["Tomorrow", addDays(1)], [`This Friday`, addDays(fri)], ["Next Monday", addDays(mon)], ["In 2 weeks", addDays(14)],
        ...(cur.due ? [["No due date", "null"]] : [])].map(([label, v]) => ({ value: v, label: v === "null" ? label : `${label} · ${dayLabel(v)}`, icon: v === "null" ? "×" : "" })) });
    $(".pop-date input", m.el).onchange = (e) => { if (e.target.value) { m.close(); apply({ due: e.target.value }); } };
    return m;
  }
  if (kind === "parent") return popMenu(anchor, { placeholder: "Make sub-issue of…", selected: [cur.parent_id ?? "null"],
    onPick: (v) => apply({ parent_id: v === "null" ? null : +v }),
    items: [{ value: "null", label: "No parent", icon: "×" },
      ...T.tasks.filter((t) => t.id !== cur.id && !T_CLOSED.has(t.status)).map((t) => ({ value: t.id, label: `${t.key} ${t.title}`, icon: statusIcon(t.status) }))] });
}
const randomColor = () => ["#2a78d6", "#eb6834", "#1baf7a", "#9b59d0", "#d03b3b", "#d4a21a", "#3aa6b9"][Math.floor(Math.random() * 7)];

// ---------- data ----------
async function loadTasks() {
  T = await api(`/tasks?closed_days=${tUi.closedAll ? -1 : 14}`);
  if (tUi.project && tUi.project !== "none" && !tProject(+tUi.project)) tUi.project = "";
  if (tUi.workspace && tUi.workspace !== "none" && !tWs(tUi.workspace)) tUi.workspace = "";
}

async function patchTask(id, changes) {
  const t = tById(id);
  if (t) {
    if (changes.status && changes.status !== t.status) t.completed_at = T_CLOSED.has(changes.status) ? new Date().toISOString() : null;
    Object.assign(t, changes);
  }
  tDraw();
  try { await send(`/tasks/${id}`, "PATCH", changes); }
  catch (e) { toast(esc(e.message)); }
  await loadTasks(); tDraw();
  if (tDrawer === id) drawerRefresh();
}

// ---------- list / board ----------
function tVisible() {
  const statuses = T_VIEWS[tUi.view].statuses, q = tUi.q.trim().toLowerCase();
  let list = T.tasks.filter((t) => statuses.includes(t.status)
    && (!tUi.project || (tUi.project === "none" ? t.project_id == null : t.project_id === +tUi.project))
    && (tUi.priority === "" || t.priority === +tUi.priority)
    && (!tUi.label || t.labels.includes(tUi.label))
    && (!tUi.workspace || (tUi.workspace === "none" ? !t.workspace : t.workspace === tUi.workspace))
    && (!q || t.title.toLowerCase().includes(q) || t.key.toLowerCase() === q || t.description.toLowerCase().includes(q)));
  const pr = (t) => (t.priority === 0 ? 9 : t.priority);
  const cmp = {
    manual: (a, b) => a.sort - b.sort || b.id - a.id,
    priority: (a, b) => pr(a) - pr(b) || a.sort - b.sort,
    due: (a, b) => (a.due || "9999").localeCompare(b.due || "9999") || pr(a) - pr(b),
    updated: (a, b) => b.updated_at.localeCompare(a.updated_at),
    created: (a, b) => b.id - a.id,
  }[tUi.order];
  // closed columns: most recently closed first, unless ordered by hand
  return list.sort((a, b) => (tUi.order === "manual" && T_CLOSED.has(a.status) && a.status === b.status
    ? (b.completed_at || "").localeCompare(a.completed_at || "") : cmp(a, b)));
}

function subInfo(t) {
  const kids = T.tasks.filter((c) => c.parent_id === t.id);
  if (!kids.length) return "";
  return `<span class="t-sub" title="Sub-issues done">${kids.filter((c) => c.status === "done").length}/${kids.length}</span>`;
}

function rowHtml(t) {
  const parent = t.parent_id && tById(t.parent_id);
  return `<div class="t-row${t.id === tSel ? " sel" : ""}${t.id === tDrawer ? " open" : ""}" data-id="${t.id}" data-act="open" tabindex="-1">
    <button class="t-ico" data-act="priority" title="Priority: ${esc(T_PRIO[t.priority])} (P)" aria-label="Priority: ${esc(T_PRIO[t.priority])}">${prioIcon(t.priority)}</button>
    <span class="t-key">${esc(t.key)}</span>
    <button class="t-ico" data-act="status" title="${esc(T_STATUS[t.status])} (S)" aria-label="Status: ${esc(T_STATUS[t.status])}">${statusIcon(t.status)}</button>
    <span class="t-title">${parent ? `<span class="t-parent">${esc(parent.title)} ›</span>` : ""}${isRec(t) ? `<span class="t-rec" title="Recurring">↻</span>` : ""}<span class="t-name">${esc(t.title)}</span>${subInfo(t)}</span>
    <span class="t-chips">${t.labels.map(labelChip).join("")}${projectChip(tProject(t.project_id))}${wsChip(tWs(t.workspace))}</span>
    ${t.due ? `<button class="t-due${dueClass(t)}" data-act="due" title="Due ${esc(dayLong(t.due))} (D)">${esc(dueText(t.due))}</button>` : ""}
    <span class="t-date" title="Created ${esc(t.created_at.slice(0, 16).replace("T", " "))}">${esc(dayLabel(t.created_at.slice(0, 10)))}</span>
  </div>`;
}

function cardHtml(t) {
  const p = tProject(t.project_id);
  return `<div class="t-card${t.id === tSel ? " sel" : ""}" data-id="${t.id}" data-act="open" draggable="true">
    <div class="t-card-top"><span class="t-key">${esc(t.key)}</span>${isRec(t) ? `<span class="t-rec" title="Recurring">↻</span>` : ""}${subInfo(t)}</div>
    <div class="t-card-title"><button class="t-ico" data-act="status" aria-label="Status: ${esc(T_STATUS[t.status])}">${statusIcon(t.status)}</button><span>${esc(t.title)}</span></div>
    <div class="t-card-meta"><button class="t-ico boxed" data-act="priority" aria-label="Priority: ${esc(T_PRIO[t.priority])}">${prioIcon(t.priority)}</button>
      ${t.due ? `<button class="t-due boxed${dueClass(t)}" data-act="due">${esc(dueText(t.due))}</button>` : ""}
      ${t.labels.map(labelChip).join("")}${projectChip(p)}${wsChip(tWs(t.workspace))}</div>
  </div>`;
}

function tDraw() {
  const host = $("#t-body");
  if (!host || !T) return;
  const list = tVisible();
  // board columns follow the workflow (Backlog → Todo → In Progress → Done); the list puts work in progress first
  const statuses = tUi.layout === "board" ? T_MENU_ORDER.filter((s) => T_VIEWS[tUi.view].statuses.includes(s)) : T_VIEWS[tUi.view].statuses;
  const groups = statuses.map((s) => [s, list.filter((t) => t.status === s)]);
  if (tUi.layout === "board") {
    host.className = "t-board";
    host.innerHTML = groups.map(([s, items]) => `<section class="t-col" data-status="${s}">
      <header>${statusIcon(s)}<span>${T_STATUS[s]}</span><span class="count">${items.length}</span><span class="spacer"></span>
        <button class="t-add" data-act="new" data-status="${s}" title="New task in ${T_STATUS[s]}" aria-label="New task in ${T_STATUS[s]}">+</button></header>
      <div class="t-col-body">${items.map(cardHtml).join("")}</div></section>`).join("");
  } else {
    host.className = "t-list";
    const shown = groups.filter(([s, items]) => items.length || !T_CLOSED.has(s));
    host.innerHTML = list.length || T.tasks.length ? shown.map(([s, items]) => `<section class="t-group" data-status="${s}">
      <header>${statusIcon(s)}<span>${T_STATUS[s]}</span><span class="count">${items.length}</span><span class="spacer"></span>
        <button class="t-add" data-act="new" data-status="${s}" title="New task in ${T_STATUS[s]}" aria-label="New task in ${T_STATUS[s]}">+</button></header>
      ${items.map(rowHtml).join("")}</section>`).join("") + (list.length ? "" : `<div class="empty">No tasks match. ${tUi.q || tUi.project || tUi.label || tUi.workspace || tUi.priority !== "" ? `<button class="link" data-act="clear">Clear filters</button>` : ""}</div>`)
      : `<div class="t-empty"><h2>No tasks yet</h2><p>Press <kbd>C</kbd> to create your first one, or add them from your iPhone with a Shortcut.</p><button class="btn primary" data-act="new">New task</button></div>`;
  }
  tFlat = groups.flatMap(([, items]) => items.map((t) => t.id));
  if (tSel && !tFlat.includes(tSel)) tSel = null;
  const closedShown = T.tasks.filter((t) => T_CLOSED.has(t.status)).length;
  $("#t-foot").innerHTML = statuses.some((s) => T_CLOSED.has(s)) && T.closed_total > closedShown
    ? `Showing tasks closed in the last 14 days · <button class="link" data-act="closed-all">Show all ${T.closed_total} closed</button>`
    : tUi.closedAll && statuses.some((s) => T_CLOSED.has(s)) ? `Showing every closed task · <button class="link" data-act="closed-recent">Only the last 14 days</button>` : "";
  $("#t-count").textContent = `${list.length}`;
  const nf = [tUi.project, tUi.priority, tUi.label, tUi.workspace].filter((v) => v !== "").length, ft = $(".t-ftoggle");
  if (ft) ft.textContent = nf ? `Filters · ${nf}` : "Filters";
  if (tUi.layout === "board") boardDnD(host);
  if (/\$/.test(host.textContent)) renderMath(host);
}

function boardDnD(host) {
  let dragId = null;
  const marker = document.createElement("div"); marker.className = "t-drop";
  host.querySelectorAll(".t-card").forEach((c) => {
    c.ondragstart = (e) => { dragId = +c.dataset.id; e.dataTransfer.effectAllowed = "move"; e.dataTransfer.setData("text/plain", c.dataset.id); requestAnimationFrame(() => c.classList.add("dragging")); };
    c.ondragend = () => { c.classList.remove("dragging"); marker.remove(); host.querySelectorAll(".t-col.over").forEach((x) => x.classList.remove("over")); };
  });
  const indexAt = (body, y) => {
    const cards = [...body.querySelectorAll(".t-card:not(.dragging)")];
    const i = cards.findIndex((c) => { const r = c.getBoundingClientRect(); return y < r.top + r.height / 2; });
    return [cards, i < 0 ? cards.length : i];
  };
  host.querySelectorAll(".t-col").forEach((col) => {
    const body = $(".t-col-body", col);
    col.ondragover = (e) => {
      if (dragId == null) return;
      e.preventDefault();
      host.querySelectorAll(".t-col.over").forEach((x) => x !== col && x.classList.remove("over"));
      col.classList.add("over");
      const [cards, i] = indexAt(body, e.clientY);
      cards[i] ? body.insertBefore(marker, cards[i]) : body.appendChild(marker);
    };
    col.ondragleave = (e) => { if (!col.contains(e.relatedTarget)) { col.classList.remove("over"); marker.remove(); } };
    col.ondrop = (e) => {
      e.preventDefault();
      const id = dragId; dragId = null;
      const [cards, i] = indexAt(body, e.clientY);
      marker.remove(); col.classList.remove("over");
      const t = tById(id), status = col.dataset.status, changes = {};
      if (t.status !== status) changes.status = status;
      if (tUi.order === "manual") {
        const before = cards[i - 1] && tById(+cards[i - 1].dataset.id)?.sort, after = cards[i] && tById(+cards[i].dataset.id)?.sort;
        const sort = before == null && after == null ? 0 : before == null ? after - 1 : after == null ? before + 1 : (before + after) / 2;
        if (sort !== t.sort) changes.sort = sort;
      }
      if (Object.keys(changes).length) patchTask(id, changes);
    };
  });
}

// ---------- create ----------
function newTaskModal(preset = {}, onCreated = null) {
  const project = tUi.project && tUi.project !== "none" ? +tUi.project : null;
  const ws = tUi.workspace && tUi.workspace !== "none" && location.hash.startsWith("#/tasks") ? tUi.workspace : null;
  const draft = { status: tUi.view === "backlog" ? "backlog" : "todo", priority: 0, labels: tUi.label ? [tUi.label] : [], project_id: project, due: null, parent_id: null, workspace: ws, ...preset };
  const m = openModal(preset.parent_id ? `New sub-issue of ${tById(preset.parent_id)?.key || ""}` : "New task");
  m.el.classList.add("t-modal");
  m.body.innerHTML = `<form class="t-new">
      <input name="title" class="t-new-title" placeholder="Task title" required maxlength="300" autocomplete="off" aria-label="Title">
      <textarea name="description" rows="4" placeholder="Add description… (Markdown)" aria-label="Description"></textarea>
      <div class="t-props-row" id="t-new-props"></div>
      <div class="t-new-foot"><label class="check"><input type="checkbox" name="more"${store.get("tasks.more", false) ? " checked" : ""}> Create more</label>
        <span class="hint">Ctrl+Enter to create</span><button class="btn primary" type="submit">Create task</button></div>
      <div class="form-msg"></div></form>`;
  const form = $("form", m.body);
  const drawProps = () => {
    const p = tProject(draft.project_id);
    $("#t-new-props", m.body).innerHTML = `
      <button type="button" class="t-prop" data-k="status">${statusIcon(draft.status)}${T_STATUS[draft.status]}</button>
      <button type="button" class="t-prop" data-k="priority">${prioIcon(draft.priority)}${draft.priority ? T_PRIO[draft.priority] : "Priority"}</button>
      <button type="button" class="t-prop" data-k="project">${p ? `<span class="t-dot" style="background:${esc(p.color)}"></span>${esc(p.name)}` : "Project"}</button>
      <button type="button" class="t-prop" data-k="labels">${draft.labels.length ? draft.labels.map(labelChip).join("") : "Labels"}</button>
      <button type="button" class="t-prop" data-k="due">${draft.due ? `Due ${esc(dueText(draft.due))}` : "Due date"}</button>
      <button type="button" class="t-prop" data-k="workspace">${tWs(draft.workspace) ? `${WS_ICON}${esc(tWs(draft.workspace).name)}` : "Workspace"}</button>`;
    m.body.querySelectorAll("[data-k]").forEach((b) => b.onclick = () => propMenu(b.dataset.k, b, draft, (ch) => {
      const inMenu = !!document.querySelector(".pop");          // multi-select (labels) stays open
      Object.assign(draft, ch); drawProps();
      if (!inMenu) $(`[data-k="${b.dataset.k}"]`, m.body)?.focus();   // keep focus in the form (Ctrl+Enter)
    }));
  };
  drawProps();
  form.onkeydown = (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); form.requestSubmit(); } };
  $("[name=title]", form).onkeydown = (e) => { if (e.key === "Enter" && !e.ctrlKey) { e.preventDefault(); form.requestSubmit(); } };
  form.onsubmit = async (e) => {
    e.preventDefault();
    const f = new FormData(form), more = !!f.get("more");
    store.set("tasks.more", more);
    try {
      const t = await send("/tasks", "POST", { ...draft, title: f.get("title"), description: f.get("description") });
      await loadTasks(); tDraw(); if (tDrawer) drawerRefresh(); onCreated?.(t);
      const el = toast(`Created <button class="link" data-open="${t.id}">${esc(t.key)}</button> ${esc(t.title)}`);
      $("[data-open]", el).onclick = () => { location.hash = `#/tasks/${t.id}`; };
      if (more) { form.reset(); $("[name=more]", form).checked = true; $("[name=title]", form).focus(); }
      else m.close();
    } catch (err) { $(".form-msg", form).textContent = err.message; }
  };
  setTimeout(() => $("[name=title]", form).focus());
}

// ---------- detail drawer ----------
function activityLine(a) {
  const d = a.data, P = (id) => tProject(id)?.name || "a deleted project";
  switch (a.kind) {
    case "created": return !d.origin ? "Created the task" : d.origin.startsWith("rec:") ? "Created by a recurring schedule"
      : `Created from a ${d.origin.startsWith("plan:") ? "Study plan" : "workspace chat"} suggestion`;
    case "status": return `Status ${statusIcon(d.from)} ${esc(T_STATUS[d.from] || d.from)} → ${statusIcon(d.to)} ${esc(T_STATUS[d.to] || d.to)}`;
    case "priority": return `Priority ${esc(T_PRIO[d.from])} → ${esc(T_PRIO[d.to])}`;
    case "labels": {
      const add = d.to.filter((x) => !d.from.includes(x)), rem = d.from.filter((x) => !d.to.includes(x));
      return [add.length && `Added ${add.map(labelChip).join(" ")}`, rem.length && `Removed ${rem.map(labelChip).join(" ")}`].filter(Boolean).join(" · ");
    }
    case "project_id": return d.to == null ? `Removed from ${esc(P(d.from))}` : `Moved to ${esc(P(d.to))}`;
    case "parent_id": return d.to == null ? "No longer a sub-issue" : `Made a sub-issue of T-${d.to}`;
    case "workspace": return d.to == null ? `Unlinked from workspace ${esc(tWs(d.from)?.name || d.from)}` : `Linked to workspace ${esc(tWs(d.to)?.name || d.to)}`;
    case "due": return d.to == null ? "Removed the due date" : `Due date set to ${esc(dayLong(d.to))}`;
    case "title": return `Renamed from “${esc(d.from)}”`;
    case "description": return "Updated the description";
    case "missed": return "Closed automatically: the day ended before it was done";
    case "focus": return `⏱ Focused ${esc(mins(d.minutes))}${a.body ? `: ${esc(a.body)}` : ""}`;
    default: return esc(a.kind);
  }
}
const when = (iso) => `${dayLabel(iso.slice(0, 10))} ${iso.slice(11, 16)}`;

async function openDrawer(id) {
  tDrawer = id;
  let el = $(".t-drawer");
  if (!el) {
    document.body.insertAdjacentHTML("beforeend", `<aside class="t-drawer" role="dialog" aria-label="Task"><div class="t-d-inner"><div class="empty">Loading…</div></div></aside>`);
    el = $(".t-drawer");
  }
  document.querySelectorAll(".t-row.open").forEach((r) => r.classList.remove("open"));
  document.querySelector(`.t-row[data-id="${id}"]`)?.classList.add("open");
  let t;
  try { t = await api(`/tasks/${id}`); }
  catch (e) { $(".t-d-inner", el).innerHTML = `<div class="card error">${esc(e.message)}</div>`; return; }
  if (tDrawer !== id) return;
  $(".t-d-inner", el).innerHTML = `
    <header class="t-d-head">
      <span class="t-key">${esc(t.key)}</span><span id="td-crumb"></span><span class="spacer"></span>
      ${focusButton({ task_id: t.id })}
      <button class="icon-btn" data-d="prev" title="Previous (K)" aria-label="Previous task">↑</button>
      <button class="icon-btn" data-d="next" title="Next (J)" aria-label="Next task">↓</button>
      <button class="icon-btn danger" data-d="delete">Delete</button>
      <button class="icon-btn" data-d="close" title="Close (Esc)">Close</button>
    </header>
    <div class="t-d-body">
      <div class="t-d-main">
        <textarea class="t-d-title" rows="1" maxlength="300" aria-label="Title">${esc(t.title)}</textarea>
        <div class="t-d-desc" id="td-desc"></div>
        <section class="t-d-sec"><h3>Sub-issues <span class="count" id="td-subcount"></span></h3><div id="td-subs"></div>
          <button class="link" data-d="add-sub">+ Add sub-issue</button></section>
        <section class="t-d-sec"><h3>Activity</h3><div id="td-activity"></div>
          <form class="t-comment" id="td-comment"><textarea rows="2" placeholder="Leave a comment… (Markdown, Ctrl+Enter)" aria-label="Comment"></textarea>
            <button class="btn small" type="submit">Comment</button></form></section>
      </div>
      <aside class="t-d-props" id="td-props"></aside>
    </div>`;
  const title = $(".t-d-title", el);
  const fit = () => { title.style.height = "auto"; title.style.height = `${title.scrollHeight}px`; };
  fit(); title.oninput = fit;
  title.onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); title.blur(); } if (e.key === "Escape") { title.value = t.title; title.blur(); } };
  title.onblur = () => { const v = title.value.trim(); if (v && v !== t.title) { t.title = v; patchTask(id, { title: v }); } else title.value = t.title; fit(); };
  drawDesc(t);
  drawerParts(t);
  wireFocusButtons(el);
  $("#td-comment", el).onsubmit = async (e) => {
    e.preventDefault();
    const ta = $("textarea", e.target);
    if (!ta.value.trim()) return;
    await send(`/tasks/${id}/comments`, "POST", { body: ta.value });
    ta.value = ""; drawerRefresh();
  };
  $("#td-comment textarea", el).onkeydown = (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); e.target.form.requestSubmit(); } };
  el.onclick = async (e) => {
    const b = e.target.closest("[data-d]");
    if (b) {
      const act = b.dataset.d;
      if (act === "close") closeDrawer();
      else if (act === "prev" || act === "next") stepDrawer(act === "next" ? 1 : -1);
      else if (act === "add-sub") newTaskModal({ parent_id: id, project_id: t.project_id, workspace: t.workspace, status: "todo" });
      else if (act === "delete") {
        if (!confirm(`Delete ${t.key} “${t.title}”? This can't be undone.`)) return;
        await send(`/tasks/${id}`, "DELETE"); closeDrawer(); await loadTasks(); tDraw(); toast(`Deleted ${esc(t.key)}`);
      } else if (act === "del-comment") {
        if (!confirm("Delete this comment?")) return;
        await send(`/tasks/${id}/comments/${b.dataset.cid}`, "DELETE"); drawerRefresh();
      } else if (act === "open-task") location.hash = `#/tasks/${b.dataset.id}`;
      else if (act === "recurring") recurringModal();
      return;
    }
    const p = e.target.closest("[data-p]");
    if (p) propMenu(p.dataset.p, p, tById(id) || t, (ch) => patchTask(id, ch));
  };
}

function drawDesc(t) {
  const host = $("#td-desc");
  host.innerHTML = t.description.trim() ? `<div class="md">${md(t.description)}</div>` : `<div class="t-placeholder">Add description…</div>`;
  renderMath(host);
  host.onclick = (e) => {
    if (e.target.closest("a") || host.querySelector("textarea")) return;
    host.innerHTML = `<textarea rows="8" aria-label="Description" placeholder="Markdown: **bold**, - lists, $math$, [links](https://…)">${esc(t.description)}</textarea>
      <div class="t-desc-actions"><span class="hint">Ctrl+Enter to save · Esc to cancel</span><button class="btn small" data-x="cancel">Cancel</button><button class="btn small primary" data-x="save">Save</button></div>`;
    const ta = $("textarea", host);
    ta.focus(); ta.style.height = `${Math.max(160, ta.scrollHeight + 4)}px`;
    const save = () => { const v = ta.value; if (v !== t.description) { t.description = v; patchTask(t.id, { description: v }); } drawDesc(t); };
    ta.onkeydown = (ev) => {
      ev.stopPropagation();
      if (ev.key === "Enter" && (ev.ctrlKey || ev.metaKey)) { ev.preventDefault(); save(); }
      if (ev.key === "Escape") { ev.preventDefault(); drawDesc(t); }
    };
    $("[data-x=save]", host).onclick = (ev) => { ev.stopPropagation(); save(); };
    $("[data-x=cancel]", host).onclick = (ev) => { ev.stopPropagation(); drawDesc(t); };
  };
}

function drawerParts(t) {
  const el = $(".t-drawer");
  if (!el) return;
  const p = tProject(t.project_id);
  $("#td-crumb", el).innerHTML = t.parent ? `<span class="t-crumb">Sub-issue of <button class="link" data-d="open-task" data-id="${t.parent.id}">${esc(t.parent.key)} ${esc(t.parent.title)}</button></span>` : "";
  $("#td-props", el).innerHTML = `
    <div class="t-pl">Status</div><button class="t-prop" data-p="status">${statusIcon(t.status)}${T_STATUS[t.status]}</button>
    <div class="t-pl">Priority</div><button class="t-prop" data-p="priority">${prioIcon(t.priority)}${T_PRIO[t.priority]}</button>
    <div class="t-pl">Labels</div><button class="t-prop" data-p="labels">${t.labels.length ? t.labels.map(labelChip).join("") : `<span class="muted">Add label</span>`}</button>
    <div class="t-pl">Project</div><button class="t-prop" data-p="project">${p ? `<span class="t-dot" style="background:${esc(p.color)}"></span>${esc(p.name)}` : `<span class="muted">No project</span>`}</button>
    <div class="t-pl">Due date</div><button class="t-prop${dueClass(t)}" data-p="due">${t.due ? esc(`${dueText(t.due)} · ${dayLong(t.due)}`) : `<span class="muted">No due date</span>`}</button>
    <div class="t-pl">Workspace</div><span class="t-prop-wrap"><button class="t-prop" data-p="workspace">${tWs(t.workspace) ? `${WS_ICON}${esc(tWs(t.workspace).name)}` : `<span class="muted">No workspace</span>`}</button>${tWs(t.workspace) ? `<a class="t-open-ws" href="#/workspaces/${esc(t.workspace)}" title="Open the workspace">Open →</a>` : ""}</span>
    <div class="t-pl">Parent</div><button class="t-prop" data-p="parent">${t.parent ? esc(t.parent.key) : `<span class="muted">No parent</span>`}</button>
    ${t.recurrence ? `<div class="t-recinfo"><button class="link" data-d="recurring">↻ ${esc(t.recurrence.title)}</button><br>${esc(recProgress(t.recurrence.stats))}</div>` : ""}
    ${t.focus?.sessions ? `<div class="t-recinfo">⏱ Focused ${esc(mins(t.focus.minutes))} · ${t.focus.sessions} session${t.focus.sessions === 1 ? "" : "s"}</div>` : ""}
    <div class="t-meta">Created ${esc(when(t.created_at))}<br>Updated ${esc(ago(t.updated_at))}${t.completed_at ? `<br>${t.status === "done" ? "Completed" : "Canceled"} ${esc(when(t.completed_at))}` : ""}</div>`;
  $("#td-subcount", el).textContent = t.children.length ? `${t.children.filter((c) => c.status === "done").length}/${t.children.length}` : "";
  $("#td-subs", el).innerHTML = t.children.map((c) => `<div class="t-subrow" data-d="open-task" data-id="${c.id}">${prioIcon(c.priority)}<span class="t-key">${esc(c.key)}</span>${statusIcon(c.status)}<span class="t-name">${esc(c.title)}</span>
      ${c.due ? `<span class="t-due${dueClass(c)}">${esc(dueText(c.due))}</span>` : ""}</div>`).join("");
  $("#td-activity", el).innerHTML = t.activity.map((a) => a.kind === "comment"
    ? `<div class="t-cmt"><div class="t-cmt-head"><span>Comment</span><span class="t-when">${esc(when(a.created_at))}</span><span class="spacer"></span>
        <button class="x" data-d="del-comment" data-cid="${a.id}" aria-label="Delete comment">×</button></div><div class="md">${md(a.body)}</div></div>`
    : `<div class="t-act"><span class="t-act-dot"></span><span>${activityLine(a)}</span><span class="t-when">${esc(when(a.created_at))}</span></div>`).join("");
  renderMath($("#td-activity", el));
}

async function drawerRefresh() {
  if (!tDrawer) return;
  try { const t = await api(`/tasks/${tDrawer}`); if (t.id === tDrawer) drawerParts(t); } catch { /* deleted */ }
}

function closeDrawer() {
  tDrawer = null;
  $(".t-drawer")?.remove();
  document.querySelectorAll(".t-row.open").forEach((r) => r.classList.remove("open"));
  if (/^#\/tasks\/\d+/.test(location.hash)) history.replaceState(null, "", "#/tasks");
}

function stepDrawer(dir) {
  if (!tFlat.length) return;
  const i = tFlat.indexOf(tDrawer ?? tSel);
  const next = tFlat[i < 0 ? 0 : Math.max(0, Math.min(tFlat.length - 1, i + dir))];
  tSel = next;
  if (tDrawer) location.hash = `#/tasks/${next}`;
  else tSelect(next);
}

function tSelect(id) {
  tSel = id;
  document.querySelectorAll(".t-row.sel, .t-card.sel").forEach((r) => r.classList.remove("sel"));
  const el = document.querySelector(`[data-id="${id}"].t-row, [data-id="${id}"].t-card`);
  el?.classList.add("sel");
  el?.scrollIntoView({ block: "nearest" });
}

// ---------- projects & labels ----------
function manageModal() {
  const m = openModal("Projects & labels");
  const draw = () => {
    m.body.innerHTML = `
      <h3 class="subhead">Projects</h3>
      <ul class="list compact">${T.projects.map((p) => `<li data-pid="${p.id}">
          <input type="color" value="${esc(p.color)}" aria-label="Color of ${esc(p.name)}" class="t-color">
          <input class="t-pname" value="${esc(p.name)}" maxlength="80" aria-label="Project name">
          <span class="meta">${p.open} open · ${p.total} total</span>
          <label class="check"><input type="checkbox" class="t-arch"${p.archived ? " checked" : ""}> Archived</label>
          <button class="x" data-del-p="${p.id}" aria-label="Delete ${esc(p.name)}">×</button></li>`).join("") || `<li class="muted">No projects yet</li>`}</ul>
      <form class="row-form" id="t-newp"><input name="name" required maxlength="80" placeholder="New project, e.g. Denshi report" aria-label="New project"><button class="btn" type="submit">Add project</button></form>
      <h3 class="subhead" style="margin-top:20px">Labels</h3>
      <div class="t-labels">${T.labels.map((l) => `<span class="t-label big">${labelChip(l.name)}<button class="x" data-del-l="${esc(l.name)}" aria-label="Delete label ${esc(l.name)}">×</button></span>`).join("") || `<span class="muted">Labels are created when you type a new one in a task's label menu.</span>`}</div>`;
    const save = async (id, ch) => { await send(`/tasks/projects/${id}`, "PATCH", ch); await loadTasks(); tDraw(); };
    m.body.querySelectorAll("[data-pid]").forEach((li) => {
      const id = +li.dataset.pid;
      $(".t-color", li).onchange = (e) => save(id, { color: e.target.value });
      $(".t-pname", li).onchange = (e) => e.target.value.trim() && save(id, { name: e.target.value.trim() });
      $(".t-arch", li).onchange = (e) => save(id, { archived: e.target.checked });
    });
    m.body.querySelectorAll("[data-del-p]").forEach((b) => b.onclick = async () => {
      if (!confirm("Delete this project? Its tasks stay, without a project.")) return;
      await send(`/tasks/projects/${b.dataset.delP}`, "DELETE"); await loadTasks(); tDraw(); draw();
    });
    m.body.querySelectorAll("[data-del-l]").forEach((b) => b.onclick = async () => {
      if (!confirm(`Delete the label “${b.dataset.delL}” and remove it from every task?`)) return;
      await send(`/tasks/labels/${encodeURIComponent(b.dataset.delL)}`, "DELETE"); await loadTasks(); tDraw(); draw();
    });
    $("#t-newp", m.body).onsubmit = async (e) => {
      e.preventDefault();
      await send("/tasks/projects", "POST", { name: new FormData(e.target).get("name"), color: randomColor() });
      await loadTasks(); tDraw(); draw(); $("#t-newp input", m.body).focus();
    };
  };
  draw();
}

function shortcutsModal() {
  const m = openModal("Keyboard shortcuts");
  const rows = [["C", "New task"], ["/", "Search"], ["J / K  or  ↓ / ↑", "Next / previous task"], ["Enter", "Open the selected task"], ["Esc", "Close the task"],
    ["S", "Change status"], ["P", "Set priority"], ["L", "Labels"], ["D", "Due date"], ["M", "Move to project"], ["W", "Link to a workspace"], ["F", "Focus on the task (start / stop)"],
    ["1 – 5 (in a menu)", "Pick an item"], ["V", "Switch list / board"], ["?", "This list"]];
  m.body.innerHTML = `<table class="t-keys">${rows.map(([k, v]) => `<tr><td>${k.split("  or  ").map((x) => `<kbd>${esc(x)}</kbd>`).join(" or ")}</td><td>${esc(v)}</td></tr>`).join("")}</table>`;
}

// ---------- the view ----------
async function viewTasks(main, arg) {
  const id = arg && /^\d+$/.test(arg) ? +arg : null;
  if ($("#t-root", main) && T) {                    // already showing: refresh, open / close the drawer
    await loadTasks(); tDraw();
    if (id) { tSel = id; tSelect(id); openDrawer(id); } else closeDrawer();
    return;
  }
  main.innerHTML = `<div id="t-root">
    <div class="top t-top"><h1>Tasks</h1><span class="count" id="t-count"></span>
      <div class="seg" role="group" aria-label="View">${Object.entries(T_VIEWS).map(([k, v]) => `<button data-view="${k}" aria-pressed="${tUi.view === k}">${v.name}</button>`).join("")}</div>
      <span class="spacer"></span>
      <div class="seg" role="group" aria-label="Layout">${[["list", "List"], ["board", "Board"]].map(([k, v]) => `<button data-layout="${k}" aria-pressed="${tUi.layout === k}">${v}</button>`).join("")}</div>
      <button class="btn primary" data-act="new" title="New task (C)">New task</button></div>
    <div class="t-filters">
      <input type="search" id="t-q" placeholder="Search  /" aria-label="Search tasks" value="${esc(tUi.q)}">
      <button class="icon-btn t-ftoggle" data-act="filters" aria-expanded="false">Filters</button>
      <select id="t-fp" aria-label="Project"></select>
      <select id="t-fpr" aria-label="Priority"><option value="">Any priority</option>${[1, 2, 3, 4, 0].map((p) => `<option value="${p}">${T_PRIO[p]}</option>`).join("")}</select>
      <select id="t-fl" aria-label="Label"></select>
      <select id="t-fw" aria-label="Workspace"></select>
      <select id="t-ord" aria-label="Order">${Object.entries(T_SORTS).map(([k, v]) => `<option value="${k}">Order: ${v}</option>`).join("")}</select>
      <span class="spacer"></span>
      <button class="icon-btn" data-act="recurring" title="Tasks that repeat on a schedule">↻ Recurring</button>
      <button class="icon-btn" data-act="manage">Projects & labels</button>
      <button class="icon-btn" data-act="keys" title="Keyboard shortcuts (?)">?</button>
    </div>
    <div id="t-body"><div class="card empty">Loading…</div></div>
    <div class="t-foot" id="t-foot"></div></div>`;
  await loadTasks();
  const fillSelects = () => {
    $("#t-fp", main).innerHTML = `<option value="">All projects</option><option value="none">No project</option>${T.projects.filter((p) => !p.archived).map((p) => `<option value="${p.id}">${esc(p.name)}</option>`).join("")}`;
    $("#t-fl", main).innerHTML = `<option value="">Any label</option>${T.labels.map((l) => `<option value="${esc(l.name)}">${esc(l.name)}</option>`).join("")}`;
    $("#t-fw", main).innerHTML = `<option value="">Any workspace</option><option value="none">No workspace</option>${(T.workspaces || []).map((w) => `<option value="${esc(w.slug)}">${esc(w.name)}</option>`).join("")}`;
    $("#t-fw", main).value = tUi.workspace;
    $("#t-fp", main).value = tUi.project; $("#t-fpr", main).value = tUi.priority; $("#t-fl", main).value = tUi.label; $("#t-ord", main).value = tUi.order;
  };
  fillSelects();
  const root = $("#t-root", main);
  const set = (k, v) => { tUi[k] = v; tSave(); tDraw(); };
  root.querySelectorAll("[data-view]").forEach((b) => b.onclick = () => { root.querySelectorAll("[data-view]").forEach((x) => x.setAttribute("aria-pressed", x === b)); set("view", b.dataset.view); });
  root.querySelectorAll("[data-layout]").forEach((b) => b.onclick = () => { root.querySelectorAll("[data-layout]").forEach((x) => x.setAttribute("aria-pressed", x === b)); set("layout", b.dataset.layout); });
  $("#t-q", main).oninput = (e) => set("q", e.target.value);
  $("#t-q", main).onkeydown = (e) => { if (e.key === "Escape" || e.key === "Enter") e.target.blur(); };
  $("#t-fp", main).onchange = (e) => set("project", e.target.value);
  $("#t-fpr", main).onchange = (e) => set("priority", e.target.value);
  $("#t-fl", main).onchange = (e) => set("label", e.target.value);
  $("#t-fw", main).onchange = (e) => set("workspace", e.target.value);
  $("#t-ord", main).onchange = (e) => set("order", e.target.value);
  root.onfocusin = (e) => { if (e.target.tagName === "SELECT") fillSelects(); };   // new projects/labels
  root.onclick = async (e) => {
    const el = e.target.closest("[data-act]");
    if (!el) return;
    const act = el.dataset.act, row = el.closest("[data-id]"), t = row && tById(+row.dataset.id);
    if (act === "open" && t) { location.hash = `#/tasks/${t.id}`; return; }
    if (["status", "priority", "due", "labels"].includes(act) && t) { e.stopPropagation(); tSelect(t.id); propMenu(act, el, t, (ch) => patchTask(t.id, ch)); return; }
    if (act === "new") newTaskModal(el.dataset.status ? { status: el.dataset.status } : {});
    else if (act === "manage") manageModal();
    else if (act === "recurring") recurringModal();
    else if (act === "filters") { const f = $(".t-filters", root); f.classList.toggle("open"); el.setAttribute("aria-expanded", f.classList.contains("open")); }
    else if (act === "keys") shortcutsModal();
    else if (act === "clear") { Object.assign(tUi, { q: "", project: "", priority: "", label: "", workspace: "" }); tSave(); $("#t-q", main).value = ""; fillSelects(); tDraw(); }
    else if (act === "closed-all" || act === "closed-recent") { tUi.closedAll = act === "closed-all"; tSave(); await loadTasks(); tDraw(); }
  };
  tDraw();
  if (id) { tSelect(id); openDrawer(id); } else closeDrawer();
}

// keyboard: only on the Tasks page, never while typing or while a menu/modal is open
document.addEventListener("keydown", (e) => {
  if (!location.hash.startsWith("#/tasks") || !T || !$("#t-root")) return;
  if (e.ctrlKey || e.metaKey || e.altKey || document.querySelector(".pop, .modal")) return;
  if (e.target.closest("input, textarea, select, [contenteditable]")) return;
  const k = e.key, target = tDrawer ?? tSel, t = target && tById(target);
  const anchorFor = (kind) => (tDrawer ? $(`.t-drawer [data-p="${kind}"]`) : document.querySelector(`[data-id="${target}"] [data-act="${kind}"]`)) || document.querySelector(`[data-id="${target}"]`);
  const menuKeys = { s: "status", p: "priority", l: "labels", d: "due", m: "project", w: "workspace" };
  if (k === "c") { e.preventDefault(); newTaskModal(); }
  else if (k === "f" && t) { e.preventDefault(); if (FOCUS && FOCUS.task_id === t.id) stopFocusDialog(); else startFocus({ task_id: t.id }, anchorFor("status")); }
  else if (k === "/") { e.preventDefault(); $("#t-q").focus(); }
  else if (k === "?") shortcutsModal();
  else if (k === "v") $(`[data-layout="${tUi.layout === "list" ? "board" : "list"}"]`).click();
  else if (k === "j" || k === "ArrowDown") { e.preventDefault(); stepDrawer(1); }
  else if (k === "k" || k === "ArrowUp") { e.preventDefault(); stepDrawer(-1); }
  else if ((k === "Enter" || k === "o") && tSel && !tDrawer) { e.preventDefault(); location.hash = `#/tasks/${tSel}`; }
  else if (k === "Escape" && tDrawer) closeDrawer();
  else if (menuKeys[k] && t) {
    e.preventDefault();
    const a = anchorFor(menuKeys[k]);
    if (a) propMenu(menuKeys[k], a, t, (ch) => patchTask(t.id, ch));
  }
});
// leaving the Tasks page closes the drawer
window.addEventListener("hashchange", () => { if (!location.hash.startsWith("#/tasks")) { tDrawer = null; $(".t-drawer")?.remove(); } });

/** Overview card: overdue, due today and in-progress tasks (most urgent first). */
function upNextCard(data, exams = []) {
  if (!data) return "";
  const soon = (exams || []).filter((e) => e.days_left >= 0 && e.days_left <= 21);
  const examRows = soon.map((e) => `<a class="t-row" href="${e.workspace ? `#/workspaces/${esc(e.workspace)}` : "#/focus"}">
      <span class="t-ico">📝</span><span class="t-title"><span class="t-name">${esc(e.title)}</span>${e.workspace_name ? `<span class="t-parent">· ${esc(e.workspace_name)}</span>` : ""}</span>
      <span class="t-due${e.days_left <= 3 ? " overdue" : e.days_left <= 7 ? " today" : ""}">${e.days_left === 0 ? "Today" : `in ${e.days_left} d`}</span>
      <span class="t-date" title="Focus time for it so far">${esc(mins(e.prep.minutes))}</span></a>`).join("");
  T = T || data;                                   // icons/labels helpers read T
  const today = data.today, pr = (t) => (t.priority === 0 ? 9 : t.priority);
  const open = data.tasks.filter((t) => !T_CLOSED.has(t.status));
  const items = open.filter((t) => t.status === "in_progress" || (t.due && t.due <= today))
    .sort((a, b) => (a.due || "9999").localeCompare(b.due || "9999") || pr(a) - pr(b)).slice(0, 8);
  const overdue = open.filter((t) => t.due && t.due < today).length;
  const desc = `${open.length} open${overdue ? ` · ${overdue} overdue` : ""}`;
  const body = (examRows ? `<div class="t-list t-upnext ex-upnext">${examRows}</div>` : "") + (items.length ? `<div class="t-list t-upnext">${items.map((t) => `<a class="t-row" href="#/tasks/${t.id}">
      ${prioIcon(t.priority)}<span class="t-key">${esc(t.key)}</span>${statusIcon(t.status)}<span class="t-title"><span class="t-name">${esc(t.title)}</span></span>
      ${t.due ? `<span class="t-due${t.due < today ? " overdue" : t.due === today ? " today" : ""}">${esc(t.due === today ? "Today" : dayLabel(t.due))}</span>` : ""}</a>`).join("")}</div>`
    : `<div class="empty">Nothing due today and nothing in progress. <a href="#/tasks">Open tasks</a></div>`);
  return `<section class="card span-12"><div class="list-head"><h2>Up next</h2><span class="count">${esc(desc)}</span><a href="#/tasks" style="margin-left:auto;font-size:13px">All tasks →</a></div>${body}</section>`;
}

// ---------- workspaces ↔ tasks ----------
const T_FENCE = "`".repeat(3);

/** Split a chat answer into Markdown parts and ```tasks blocks: [{text} | {tasks: [...]} | {bad: raw}]. */
function splitSuggestions(content) {
  const out = [], re = new RegExp(`${T_FENCE}tasks[^\\n]*\\n([\\s\\S]*?)(?:${T_FENCE}|$)`, "g");
  let last = 0, m;
  while ((m = re.exec(content))) {
    if (m.index > last) out.push({ text: content.slice(last, m.index) });
    try {
      const raw = JSON.parse(m[1]);
      const list = (Array.isArray(raw) ? raw : raw?.tasks || []).map((x) => normSuggestion(x)).filter(Boolean);
      out.push(list.length ? { tasks: list } : { bad: m[1] });
    } catch { out.push({ bad: m[1] }); }
    last = m.index + m[0].length;
  }
  if (last < content.length) out.push({ text: content.slice(last) });
  return out;
}

function normSuggestion(x, deep = true) {
  if (!x || typeof x.title !== "string" || !x.title.trim()) return null;
  const due = typeof x.due === "string" && /^\d{4}-\d{2}-\d{2}$/.test(x.due) ? x.due : null;
  return {
    title: x.title.trim().slice(0, 300), description: typeof x.description === "string" ? x.description.slice(0, 20000) : "",
    priority: Math.min(4, Math.max(0, parseInt(x.priority, 10) || 0)), due,
    labels: Array.isArray(x.labels) ? x.labels.filter((l) => typeof l === "string" && l.trim()).slice(0, 5) : [],
    source: typeof x.source === "string" ? x.source : "",
    subtasks: deep && Array.isArray(x.subtasks) ? x.subtasks.map((st) => normSuggestion(st, false)).filter(Boolean).slice(0, 30) : [],
  };
}

/** Every task (any status), to know which suggestions were already added (by origin). */
async function originIndex() {
  const all = await api("/tasks?closed_days=-1");
  if (!T) T = all;
  else { T.workspaces = all.workspaces; T.projects = all.projects; T.labels = all.labels; }
  return new Map(all.tasks.filter((t) => t.origin).map((t) => [t.origin, t]));
}

/** A checklist of suggested tasks with "Add". `prefix` makes each item's origin unique and stable. */
function suggestionWidget(host, items, { prefix, workspace, origins }) {
  const pick = new Set();
  items.forEach((it, i) => {
    if (!origins.has(`${prefix}${i}`)) pick.add(`${i}`);
    it.subtasks.forEach((_, j) => { if (!origins.has(`${prefix}${i}.${j}`)) pick.add(`${i}.${j}`); });
  });
  const line = (it, key, sub) => {
    const added = origins.get(`${prefix}${key}`), parentOff = sub && !pick.has(key.split(".")[0]) && !origins.has(`${prefix}${key.split(".")[0]}`);
    return `<li class="sug-item${sub ? " sub" : ""}${added ? " added" : ""}">
      <label class="sug-row">${added ? `<span class="sug-done" aria-label="Added">✓</span>` : `<input type="checkbox" data-k="${key}"${pick.has(key) ? " checked" : ""}${parentOff ? " disabled" : ""}>`}
        ${prioIcon(it.priority)}<span class="sug-title">${esc(it.title)}</span></label>
      <span class="sug-meta">${it.due ? `<span class="t-due">${esc(dueText(it.due))}</span>` : ""}${it.labels.map(labelChip).join("")}
        ${added ? `<a href="#/tasks/${added.id}">${esc(added.key)}</a>` : ""}</span>
      ${it.description || it.source ? `<div class="sug-desc md">${md(it.description)}${it.source ? `<span class="fc-src">${esc(it.source)}</span>` : ""}</div>` : ""}</li>`;
  };
  const draw = () => {
    const n = pick.size, total = items.reduce((a, it) => a + 1 + it.subtasks.length, 0);
    const addedCount = items.reduce((a, it, i) => a + (origins.has(`${prefix}${i}`) ? 1 : 0) + it.subtasks.filter((_, j) => origins.has(`${prefix}${i}.${j}`)).length, 0);
    host.innerHTML = `<div class="sug">
      <div class="sug-head"><strong>Suggested tasks</strong><span class="count">${total}${addedCount ? ` · ${addedCount} added` : ""}</span><span class="spacer"></span>
        ${addedCount < total ? `<button class="link" data-all>${n ? "Select none" : "Select all"}</button>` : ""}</div>
      <ul class="sug-list">${items.map((it, i) => line(it, `${i}`, false) + it.subtasks.map((st, j) => line(st, `${i}.${j}`, true)).join("")).join("")}</ul>
      ${addedCount < total ? `<div class="sug-foot"><label class="field">Add to <select data-status><option value="todo">Todo</option><option value="backlog">Backlog</option></select></label>
        <label class="field">Project <select data-project><option value="">None</option>${(T?.projects || []).filter((p) => !p.archived).map((p) => `<option value="${p.id}">${esc(p.name)}</option>`).join("")}</select></label>
        <span class="spacer"></span><button class="btn primary small" data-add${n ? "" : " disabled"}>Add ${n} task${n === 1 ? "" : "s"}</button></div>`
        : `<div class="sug-foot muted">All added${workspace ? ` and linked to this workspace` : ""}. <a href="#/tasks" data-open-tasks>Open Tasks →</a></div>`}
      <div class="form-msg"></div></div>`;
    renderMath(host);
    host.querySelectorAll("[data-k]").forEach((c) => c.onchange = () => {
      const k = c.dataset.k;
      c.checked ? pick.add(k) : pick.delete(k);
      if (!k.includes(".")) items[+k].subtasks.forEach((_, j) => { if (!origins.has(`${prefix}${k}.${j}`)) c.checked ? pick.add(`${k}.${j}`) : pick.delete(`${k}.${j}`); });
      draw();
    });
    host.querySelector("[data-all]")?.addEventListener("click", () => {
      const none = pick.size > 0; pick.clear();
      if (!none) items.forEach((it, i) => { if (!origins.has(`${prefix}${i}`)) pick.add(`${i}`); it.subtasks.forEach((_, j) => { if (!origins.has(`${prefix}${i}.${j}`)) pick.add(`${i}.${j}`); }); });
      draw();
    });
    host.querySelector("[data-open-tasks]")?.addEventListener("click", () => { if (workspace) { tUi.workspace = workspace; tSave(); } });
    host.querySelector("[data-add]")?.addEventListener("click", async (e) => {
      e.target.disabled = true;
      const payload = items.map((it, i) => {
        const subs = it.subtasks.map((st, j) => [st, `${i}.${j}`]).filter(([, k]) => pick.has(k));
        if (!pick.has(`${i}`) && !(origins.has(`${prefix}${i}`) && subs.length)) return null;
        const { subtasks, source, ...rest } = it;
        const desc = (d, src) => (src ? `${d}${d ? "\n\n" : ""}*Source: ${src}*` : d);
        return { ...rest, description: desc(it.description, source), origin: `${prefix}${i}`,
          subtasks: subs.map(([st, k]) => ({ title: st.title, description: st.description, priority: st.priority, due: st.due, labels: st.labels, origin: `${prefix}${k}` })) };
      }).filter(Boolean);
      try {
        const proj = host.querySelector("[data-project]").value;
        const r = await send("/tasks/batch", "POST", { tasks: payload, workspace: workspace || null, status: host.querySelector("[data-status]").value, project_id: proj ? +proj : null });
        r.created.forEach((t) => origins.set(t.origin, t));
        pick.clear(); draw();
        toast(`Added ${r.created.length} task${r.created.length === 1 ? "" : "s"}${workspace ? " to this workspace" : ""}`);
        document.dispatchEvent(new CustomEvent("tasks-changed"));
      } catch (err) { host.querySelector(".form-msg").textContent = err.message; e.target.disabled = false; }
    });
  };
  draw();
}

/** Fill the .t-suggest placeholders of chat messages (data-sug = JSON key into `store`). */
async function hydrateSuggestions(box, slug, bag) {
  const hosts = box.querySelectorAll(".t-suggest[data-sug]");
  if (!hosts.length) return;
  let origins;
  try { origins = await originIndex(); } catch { return; }
  hosts.forEach((h) => { const s = bag[h.dataset.sug]; if (s) suggestionWidget(h, s.items, { prefix: s.prefix, workspace: slug, origins }); });
}

/** Chat answer → HTML, with ```tasks blocks replaced by placeholders (filled by hydrateSuggestions). */
function answerHtml(content, msgId, bag) {
  let b = 0;
  return splitSuggestions(content).map((part) => {
    if (part.text != null) return md(part.text);
    if (part.bad != null) return `<div class="sug-bad">Claude suggested tasks, but they couldn't be read.</div>${md(`${T_FENCE}\n${part.bad}\n${T_FENCE}`)}`;
    const key = `${msgId}:${b++}`;
    bag[key] = { items: part.tasks, prefix: `chat:${msgId}:${b - 1}.` };
    return `<div class="t-suggest" data-sug="${esc(key)}"><div class="empty">Loading suggestions…</div></div>`;
  }).join("");
}

/** Study plan output (*.plan.json) in the Studio viewer. */
async function planViewer(body, data, slug, name) {
  body.innerHTML = `<article class="md doc">${md(data.overview || "")}</article><div id="plan-sug"><div class="empty">Loading…</div></div>`;
  renderMath(body);
  const items = (data.tasks || []).map((x) => normSuggestion(x)).filter(Boolean);
  const origins = await originIndex();
  suggestionWidget($("#plan-sug", body), items, { prefix: `plan:${slug}/${name}:`, workspace: slug, origins });
}

/** The Tasks card on a workspace page: open tasks linked to it. */
function workspaceTasksPanel(host, slug) {
  const draw = async () => {
    let data;
    try { data = await api(`/tasks?closed_days=7&workspace=${encodeURIComponent(slug)}`); } catch (e) { host.innerHTML = `<div class="error">${esc(e.message)}</div>`; return; }
    if (!T) T = data; else { T.workspaces = data.workspaces; T.projects = data.projects; T.labels = data.labels; }
    const rank = { in_progress: 0, todo: 1, backlog: 2, done: 3, canceled: 4 }, pr = (t) => (t.priority === 0 ? 9 : t.priority);
    const cmp = (a, b) => rank[a.status] - rank[b.status] || (a.due || "9999").localeCompare(b.due || "9999") || pr(a) - pr(b);
    const all = data.tasks.filter((t) => t.status !== "canceled"), ids = new Set(all.map((t) => t.id));
    // sub-issues right under their parent (when the parent is in the list too)
    const list = all.filter((t) => !ids.has(t.parent_id)).sort(cmp)
      .flatMap((t) => [t, ...all.filter((c) => c.parent_id === t.id).sort(cmp)]);
    const open = list.filter((t) => !T_CLOSED.has(t.status)), shown = list.slice(0, 12);
    $(".count", host.closest(".card")).textContent = open.length ? `${open.length} open` : "";
    host.innerHTML = (shown.length ? `<ul class="list compact ws-tasks">${shown.map((t) => `<li class="${T_CLOSED.has(t.status) ? "closed" : ""}${ids.has(t.parent_id) ? " sub" : ""}">
        <button class="t-ico" data-st="${t.id}" title="${esc(T_STATUS[t.status])}" aria-label="Status: ${esc(T_STATUS[t.status])}">${statusIcon(t.status)}</button>
        <div class="main"><div class="title"><a href="#/tasks/${t.id}">${esc(t.title)}</a></div>
          <div class="meta">${esc(t.key)}${t.parent_id && !ids.has(t.parent_id) ? ` · sub-issue of T-${t.parent_id}` : ""}${t.priority ? ` · ${esc(T_PRIO[t.priority])}` : ""}</div></div>
        ${t.due ? `<span class="t-due${dueClass({ ...t, due: t.due })}">${esc(dueText(t.due))}</span>` : ""}
        ${T_CLOSED.has(t.status) ? "" : focusButton({ task_id: t.id, cls: "icon-btn small-focus" })}</li>`).join("")}</ul>
        ${list.length > shown.length ? `<div class="meta">and ${list.length - shown.length} more</div>` : ""}`
      : `<div class="empty">No tasks yet. Add one, ask Claude for a plan in the chat, or use <strong>Study plan</strong> in Studio.</div>`)
      + `<div class="ws-tasks-foot"><button class="btn small" data-new>+ New task</button><span class="spacer"></span><a href="#/tasks" data-all>Open in Tasks →</a></div>`;
    renderMath(host);
    wireFocusButtons(host);
    host.querySelectorAll("[data-st]").forEach((b) => b.onclick = () => {
      const t = data.tasks.find((x) => x.id === +b.dataset.st);
      propMenu("status", b, t, async (ch) => { await send(`/tasks/${t.id}`, "PATCH", ch); draw(); });
    });
    host.querySelector("[data-new]").onclick = async () => { await loadTasks(); newTaskModal({ workspace: slug }, () => draw()); };
    host.querySelector("[data-all]").onclick = () => { tUi.workspace = slug; tUi.view = "all"; tSave(); };
  };
  document.addEventListener("tasks-changed", () => { if (host.isConnected) draw(); });
  document.addEventListener("focus-changed", () => { if (host.isConnected) draw(); });
  draw();
  return { refresh: draw };
}

// ---------- recurring tasks ----------
const isRec = (t) => (t.origin || "").startsWith("rec:");
const WD_MON = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];          // 0 = Monday (server numbering)
function recSchedule(r) {
  const d = [...r.weekdays].sort();
  const days = d.length === 7 ? "Every day" : d.join() === "0,1,2,3,4" ? "Weekdays" : d.join() === "5,6" ? "Weekends" : d.map((i) => WD_MON[i]).join(", ");
  return `${days} · ${dayLabel(r.start)}${r.end ? ` → ${dayLabel(r.end)}` : " onwards"}`;
}
function recProgress(st) {
  const of = st.total ?? st.scheduled_so_far;
  return `${st.done}/${of} done${st.streak > 1 ? ` · ${st.streak}-day streak` : ""}${st.missed ? ` · ${st.missed} missed` : ""}`;
}
const countDays = (start, end, wd) => {
  if (!start || !end || end < start) return null;
  let n = 0; for (let d = parseDay(start); d <= parseDay(end); d.setDate(d.getDate() + 1)) if (wd.includes((d.getDay() + 6) % 7)) n++;
  return n;
};

async function recurringModal(preset = null) {
  if (!T) await loadTasks();
  const m = openModal("Recurring tasks");
  m.el.classList.add("t-modal");
  let list = [];
  const draw = () => {
    m.body.innerHTML = `
      ${list.length ? `<ul class="rec-list">${list.map((r) => {
        const st = r.stats, of = st.total ?? st.scheduled_so_far, pct = of ? Math.round((st.done / of) * 100) : 0;
        const today = st.ended ? "Finished" : r.paused ? "Paused" : st.today === "done" ? "Today: done ✓" : st.today ? "Today: to do" : `Next: ${st.next ? dueText(st.next) : "—"}`;
        return `<li class="${r.paused || st.ended ? "off" : ""}"><div class="main">
            <div class="title">${prioIcon(r.priority)} ${esc(r.title)}</div>
            <div class="meta">${esc(recSchedule(r))}${r.workspace && tWs(r.workspace) ? ` · ${esc(tWs(r.workspace).name)}` : ""}${r.link ? ` · <a href="${esc(r.link)}">${esc(r.link_label || "link")}</a>` : ""}</div>
            <div class="rec-bar" role="img" aria-label="${pct}% done"><i style="width:${pct}%"></i></div>
            <div class="meta">${esc(recProgress(st))} · <strong>${esc(today)}</strong></div></div>
          <div class="rec-actions">${st.ended ? "" : `<button class="btn small" data-pause="${r.id}" data-on="${r.paused ? 0 : 1}">${r.paused ? "Resume" : "Pause"}</button>`}
            <button class="x" data-del-rec="${r.id}" title="Stop repeating" aria-label="Delete ${esc(r.title)}">×</button></div></li>`;
      }).join("")}</ul>` : `<p class="desc">Nothing repeats yet. A recurring task creates one task on each scheduled day, due that day.</p>`}
      <form class="t-new rec-form" id="rec-form">
        <h3 class="subhead">New recurring task</h3>
        <input name="title" class="t-new-title" required maxlength="300" placeholder="e.g. Flashcards: Russian vocab" value="${esc(preset?.title || "")}" aria-label="Title">
        <div class="rec-days" role="group" aria-label="Days">
          <div class="seg">${[["all", "Every day"], ["wk", "Weekdays"], ["custom", "Custom"]].map(([k, v], i) => `<button type="button" data-preset="${k}" aria-pressed="${i === 0}">${v}</button>`).join("")}</div>
          <div class="rec-wd">${WD_MON.map((d, i) => `<label class="wd"><input type="checkbox" name="wd" value="${i}" checked><span>${d}</span></label>`).join("")}</div>
        </div>
        <div class="row-form rec-dates">
          <label class="field">From <input type="date" name="start" required value="${todayISO()}"></label>
          <label class="field">Until <input type="date" name="end" value="${esc(preset?.end || "")}"></label>
          <span class="rec-quick">${[[7, "1 week"], [30, "30 days"], [60, "60 days"]].map(([n, v]) => `<button type="button" class="link" data-len="${n}">${v}</button>`).join(" · ")} · <button type="button" class="link" data-len="0">no end</button></span>
        </div>
        <div class="hint rec-count" id="rec-count"></div>
        <div class="row-form">
          <label class="field">Priority <select name="priority">${[0, 1, 2, 3, 4].map((p) => `<option value="${p}"${(preset?.priority ?? 0) === p ? " selected" : ""}>${T_PRIO[p]}</option>`).join("")}</select></label>
          <label class="field">Workspace <select name="workspace"><option value="">None</option>${(T.workspaces || []).map((w) => `<option value="${esc(w.slug)}"${preset?.workspace === w.slug ? " selected" : ""}>${esc(w.name)}</option>`).join("")}</select></label>
          <label class="field">Labels <input name="labels" placeholder="review, russian" value="${esc((preset?.labels || []).join(", "))}"></label>
        </div>
        <textarea name="description" rows="2" placeholder="Notes for each day (optional, Markdown)"></textarea>
        ${preset?.link ? `<div class="rec-link">Each task links to: <a href="${esc(preset.link)}">${esc(preset.link_label || "link")}</a> <button type="button" class="x" data-nolink aria-label="Remove link">×</button></div>` : ""}
        <label class="check"><input type="checkbox" name="close_missed" checked> Close a day's task automatically when the day is over and it isn't done (no overdue pile-up)</label>
        <div class="t-new-foot"><span class="hint">Today's task is created right away if today is one of the days</span><button class="btn primary" type="submit">Start repeating</button></div>
        <div class="form-msg"></div>
      </form>`;
    const f = $("#rec-form", m.body), wd = () => [...f.querySelectorAll("[name=wd]:checked")].map((c) => +c.value);
    const recount = () => {
      const n = countDays(f.elements.start.value, f.elements.end.value, wd());
      $("#rec-count", m.body).textContent = !f.elements.end.value ? "Repeats until you stop it" : n == null ? "The end is before the start" : `${n} task${n === 1 ? "" : "s"} in total, one per day`;
    };
    f.querySelectorAll("[data-preset]").forEach((b) => b.onclick = () => {
      f.querySelectorAll("[data-preset]").forEach((x) => x.setAttribute("aria-pressed", x === b));
      if (b.dataset.preset !== "custom") f.querySelectorAll("[name=wd]").forEach((c) => (c.checked = b.dataset.preset === "all" || +c.value < 5));
      recount();
    });
    f.querySelectorAll("[name=wd]").forEach((c) => c.onchange = () => { f.querySelectorAll("[data-preset]").forEach((x) => x.setAttribute("aria-pressed", x.dataset.preset === "custom")); recount(); });
    f.querySelectorAll("[data-len]").forEach((b) => b.onclick = () => {
      const n = +b.dataset.len;
      if (!n) f.elements.end.value = ""; else { const d = parseDay(f.elements.start.value || todayISO()); d.setDate(d.getDate() + n - 1); f.elements.end.value = isoOf(d); }
      recount();
    });
    f.elements.start.onchange = f.elements.end.onchange = recount;
    f.querySelector("[data-nolink]")?.addEventListener("click", (e) => { preset = { ...preset, link: null, link_label: null }; e.target.closest(".rec-link").remove(); });
    recount();
    f.onsubmit = async (e) => {
      e.preventDefault();
      if (!wd().length) { $(".form-msg", f).textContent = "Pick at least one day"; return; }
      try {
        await send("/tasks/recurring", "POST", {
          title: f.elements.title.value, description: f.elements.description.value, priority: +f.elements.priority.value, weekdays: wd(),
          labels: f.elements.labels.value.split(",").map((x) => x.trim()).filter(Boolean), workspace: f.elements.workspace.value || null,
          start: f.elements.start.value, end: f.elements.end.value || null, close_missed: f.elements.close_missed.checked,
          link: preset?.link || null, link_label: preset?.link_label || null });
        preset = null;
        toast("Recurring task created");
        await reload();
        await loadTasks(); tDraw();
        document.dispatchEvent(new CustomEvent("tasks-changed"));
      } catch (err) { $(".form-msg", f).textContent = err.message; }
    };
    m.body.querySelectorAll("[data-pause]").forEach((b) => b.onclick = async () => {
      await send(`/tasks/recurring/${b.dataset.pause}`, "PATCH", { paused: b.dataset.on === "1" }); reload();
    });
    m.body.querySelectorAll("[data-del-rec]").forEach((b) => b.onclick = async () => {
      if (!confirm("Stop repeating? Tasks it already created stay in your list.")) return;
      await send(`/tasks/recurring/${b.dataset.delRec}`, "DELETE"); reload();
    });
  };
  const reload = async () => { list = await api("/tasks/recurring"); draw(); };
  await reload();
  if (preset) setTimeout(() => $("#rec-form [name=title]", m.body)?.focus());
}
