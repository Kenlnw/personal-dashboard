// Personal dashboard: hash-routed views over the /summary/* endpoints.
// Auth: requests come through `tailscale serve`, which identifies the user, so no token here.
"use strict";

const $ = (sel, el = document) => el.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

async function api(path) {
  const r = await fetch(path, { credentials: "same-origin" });
  if (r.status === 401) throw new Error("Not signed in. Open this page through your Tailscale address (https://…ts.net).");
  if (!r.ok) throw new Error(`${path} → ${r.status}`);
  return r.json();
}

const store = {
  get(k, d) { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* private mode */ } },
};

// ---------- formatting ----------
const fmt = {
  int: (n) => (n == null ? "—" : Math.round(n).toLocaleString()),
  hm: (min) => {
    if (min == null) return "—";
    if (min > 0 && min < 1) return "<1m";
    const t = Math.round(min), h = Math.floor(t / 60), m = t % 60;
    return h ? `${h}h ${String(m).padStart(2, "0")}m` : `${m}m`;
  },
  hours: (h) => (h == null ? "—" : `${h.toFixed(1)} h`),
  compact: (n) => (n >= 10000 ? `${Math.round(n / 1000)}K` : n >= 1000 && n % 1000 ? `${(n / 1000).toFixed(1)}K` : n >= 1000 ? `${n / 1000}K` : String(n)),
  time: (iso) => (iso ? iso.slice(11, 16) : "—"),
};
const WD = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const parseDay = (d) => { const [y, m, dd] = d.split("-").map(Number); return new Date(y, m - 1, dd); };
const dayLabel = (d) => { const t = parseDay(d); return `${MON[t.getMonth()]} ${t.getDate()}`; };
const dayLong = (d) => { const t = parseDay(d); return `${WD[t.getDay()]}, ${MON[t.getMonth()]} ${t.getDate()}`; };
const todayISO = () => { const t = new Date(); return `${t.getFullYear()}-${String(t.getMonth() + 1).padStart(2, "0")}-${String(t.getDate()).padStart(2, "0")}`; };
function relDay(d) {
  const diff = Math.round((parseDay(todayISO()) - parseDay(d)) / 864e5);
  return diff === 0 ? "today" : diff === 1 ? "yesterday" : dayLong(d);
}

// ---------- charts ----------
function niceTicks(max, count = 4) {
  if (!(max > 0)) return [0, 1];
  const raw = max / count, pow = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((f) => f * pow).find((s) => s >= raw);
  const ticks = [];
  for (let v = 0; v <= max + step * 0.0001 || ticks.length < 2; v += step) ticks.push(+v.toFixed(10));
  if (ticks[ticks.length - 1] < max) ticks.push(ticks[ticks.length - 1] + step);
  return ticks;
}

/** Single-series column chart. points: [{key, label, y|null, tip}] oldest → newest. */
function columnChart(host, points, { valueFmt, axisFmt = fmt.compact, emptyText = "No data in this range" }) {
  host.innerHTML = "";
  if (!points.some((p) => p.y != null && p.y > 0)) { host.innerHTML = `<div class="empty">${esc(emptyText)}</div>`; return; }
  const wrap = document.createElement("div"); wrap.className = "chart";
  const tip = document.createElement("div"); tip.className = "tip"; tip.hidden = true;
  wrap.appendChild(tip); host.appendChild(wrap);

  const draw = () => {
    const W = Math.max(wrap.clientWidth, 260), H = 190, m = { t: 10, r: 6, b: 22, l: 40 };
    const iw = W - m.l - m.r, ih = H - m.t - m.b, base = m.t + ih;
    const max = Math.max(...points.map((p) => p.y || 0));
    const ticks = niceTicks(max), top = ticks[ticks.length - 1];
    const band = iw / points.length, bw = Math.max(2, Math.min(24, band - 2));
    const every = Math.max(1, Math.ceil(points.length / Math.max(2, Math.floor(iw / 58))));
    let s = `<svg viewBox="0 0 ${W} ${H}" role="img">`;
    for (const t of ticks) {
      const y = base - (t / top) * ih;
      if (t > 0) s += `<line class="gridline" x1="${m.l}" x2="${W - m.r}" y1="${y}" y2="${y}"/>`;
      s += `<text x="${m.l - 6}" y="${y + 3.5}" text-anchor="end">${esc(axisFmt(t))}</text>`;
    }
    s += `<line class="baseline" x1="${m.l}" x2="${W - m.r}" y1="${base}" y2="${base}"/>`;
    points.forEach((p, i) => {
      const x = m.l + i * band + (band - bw) / 2;
      if (p.y > 0) {
        const h = Math.max(1, (p.y / top) * ih), y = base - h, r = Math.min(4, bw / 2, h);
        s += `<path class="bar" data-i="${i}" d="M${x},${base}V${y + r}Q${x},${y} ${x + r},${y}H${x + bw - r}Q${x + bw},${y} ${x + bw},${y + r}V${base}Z"/>`;
      }
      if ((points.length - 1 - i) % every === 0)
        s += `<text x="${x + bw / 2}" y="${H - 6}" text-anchor="middle">${esc(p.label)}</text>`;
      s += `<rect class="hit" data-i="${i}" x="${m.l + i * band}" y="${m.t}" width="${band}" height="${ih}"/>`;
    });
    wrap.querySelector("svg")?.remove();
    wrap.insertAdjacentHTML("afterbegin", s + "</svg>");
    wrap.querySelector("svg").setAttribute("aria-label", `${points.length} values; see the table below the chart`);
    wrap._geom = { band, m, base, top, ih };
  };

  const show = (i) => {
    const p = points[i], g = wrap._geom;
    wrap.classList.add("hovering");
    wrap.querySelectorAll(".bar").forEach((b) => b.classList.toggle("on", +b.dataset.i === i));
    const scale = wrap.clientWidth / Math.max(wrap.clientWidth, 260);
    const x = (g.m.l + (i + 0.5) * g.band) * scale;
    const y = (p.y > 0 ? g.base - (p.y / g.top) * g.ih : g.base) * scale;
    tip.innerHTML = `<div class="t">${esc(p.tip ?? p.label)}</div><div class="v">${p.y == null ? "No data" : esc(valueFmt(p.y))}</div>`;
    tip.hidden = false;
    const half = tip.offsetWidth / 2;
    tip.style.left = `${Math.min(Math.max(x, half), wrap.clientWidth - half)}px`;
    tip.style.top = `${Math.max(y, 40)}px`;
  };
  const hide = () => { tip.hidden = true; wrap.classList.remove("hovering"); };
  wrap.addEventListener("pointermove", (e) => { const i = e.target.dataset?.i; i == null ? hide() : show(+i); });
  wrap.addEventListener("pointerdown", (e) => { const i = e.target.dataset?.i; i == null ? hide() : show(+i); });   // tap on iPad / iPhone
  wrap.addEventListener("pointerleave", hide);
  draw();
  new ResizeObserver(() => { hide(); draw(); }).observe(wrap);
}

function tableView(rows, headers) {
  return `<details class="table-view"><summary>Table</summary><table><thead><tr>${headers.map((h, i) => `<th class="${i ? "num" : ""}">${esc(h)}</th>`).join("")}</tr></thead><tbody>${rows.map((r) => `<tr>${r.map((c, i) => `<td class="${i ? "num" : ""}">${esc(c)}</td>`).join("")}</tr>`).join("")}</tbody></table></details>`;
}

function barList(items, valueFmt, max = Math.max(...items.map((i) => i.value), 0)) {
  if (!items.length) return `<div class="empty">Nothing yet</div>`;
  return `<div class="bars">${items.map((i) => `<div class="name" title="${esc(i.path || i.name)}">${esc(i.name)}</div><div class="track"><div class="fill" style="width:${max ? (i.value / max) * 100 : 0}%"></div></div><div class="num">${esc(valueFmt(i.value))}</div>`).join("")}</div>`;
}

const tile = (label, value, context = "", link = "") => `<div class="card tile span-3"><div class="label">${esc(label)}${link ? `<a class="tile-link" href="${esc(link)}" target="_blank" rel="noopener" title="Open on GitHub">GitHub ↗</a>` : ""}</div><div class="value">${esc(value)}</div><div class="context">${esc(context)}</div></div>`;

function ago(iso) {
  if (!iso) return "never";
  const days = Math.floor((Date.now() - new Date(iso)) / 864e5);
  if (days < 1) return "today";
  if (days === 1) return "yesterday";
  if (days < 30) return `${days} days ago`;
  if (days < 365) { const m = Math.round(days / 30); return `${m} month${m > 1 ? "s" : ""} ago`; }
  const y = Math.floor(days / 365); return `${y} year${y > 1 ? "s" : ""} ago`;
}
const card = (title, desc, body, span = 6, id = "") => `<section class="card span-${span}"${id ? ` id="${id}"` : ""}><h2>${esc(title)}</h2>${desc ? `<p class="desc">${esc(desc)}</p>` : ""}${body}</section>`;

// ---------- views ----------
const RANGES = [7, 30, 90];

async function viewOverview(main) {
  const range = store.get("range", 30);
  main.innerHTML = `<div class="top"><h1>Overview</h1><span class="sub">${esc(dayLong(todayISO()))}</span><span class="spacer"></span>
    <div class="seg" role="group" aria-label="Range">${RANGES.map((r) => `<button data-range="${r}" aria-pressed="${r === range}">${r}d</button>`).join("")}</div>
    <button class="icon-btn" id="refresh" title="Reload data">Refresh</button></div><div id="body" class="grid"><div class="card span-12 empty">Loading…</div></div>`;
  main.querySelectorAll("[data-range]").forEach((b) => b.onclick = () => { store.set("range", +b.dataset.range); render(); });
  $("#refresh", main).onclick = render;

  const [daily, apps, sleep, docs, taskData, exams] = await Promise.all([
    api(`/summary/daily?days=${range}`), api(`/summary/apps?days=${Math.min(range, 90)}`),
    api(`/summary/sleep?days=${range}`), api(`/summary/docs`), api("/tasks?closed_days=0").catch(() => null),
    api("/exams").catch(() => []),
  ]);
  const days = [...daily].reverse();                          // oldest → newest
  const today = daily[0];
  const lastSteps = daily.find((d) => d.steps != null);
  const night = sleep[0];
  const reports = docs.repos.reduce((a, r) => a + r.reports, 0);
  const released = docs.repos.reduce((a, r) => a + r.releases, 0);
  const nightCtx = night ? `${relDay(night.date)} · ${fmt.time(night.bedtime)}–${fmt.time(night.wake)}${night.measured === "in_bed" ? " (in bed)" : ""}` : "no sleep data yet";

  const pcPts = days.map((d) => ({ key: d.date, label: dayLabel(d.date), tip: dayLong(d.date), y: d.pc_active_minutes || null }));
  const stepPts = days.map((d) => ({ key: d.date, label: dayLabel(d.date), tip: dayLong(d.date), y: d.steps }));
  const sleepPts = days.map((d) => ({ key: d.date, label: dayLabel(d.date), tip: `Night ending ${dayLong(d.date)}`, y: d.sleep_hours }));

  const stages = night?.stages_hours && Object.keys(night.stages_hours).some((k) => k !== "unspecified") ? night.stages_hours : null;
  const order = [["deep", "Deep", "--series-1"], ["core", "Core", "--series-2"], ["rem", "REM", "--series-3"]];
  const stageTotal = stages ? order.reduce((a, [k]) => a + (stages[k] || 0), 0) : 0;
  const stageBody = stages ? `
      <div class="segbar" role="img" aria-label="${order.map(([k, n]) => `${n} ${fmt.hours(stages[k] || 0)}`).join(", ")}">
        ${order.filter(([k]) => stages[k] > 0).map(([k, n, c]) => { const pct = Math.round((stages[k] / stageTotal) * 100); return `<div style="flex:${stages[k]};background:var(${c})" title="${n}: ${fmt.hours(stages[k])} (${pct}%)">${pct >= 14 ? pct + "%" : ""}</div>`; }).join("")}
      </div>
      <div class="legend">${order.map(([k, n, c]) => `<span><span class="sw" style="background:var(${c})"></span>${n} ${fmt.hours(stages[k] || 0)}</span>`).join("")}
        ${night.awake_hours ? `<span>Awake ${fmt.hours(night.awake_hours)}</span>` : ""}${night.in_bed_hours ? `<span>In bed ${fmt.hours(night.in_bed_hours)}</span>` : ""}</div>`
    : `<div class="empty">${night ? "This night has no sleep stages" : "No sleep data yet"}</div>`;

  $("#body", main).innerHTML = `
    ${tile("PC active today", fmt.hm(today.pc_active_minutes), today.top_apps[0] ? `Most: ${today.top_apps[0].app}` : "No PC activity yet")}
    ${tile("Steps", fmt.int(lastSteps?.steps), lastSteps ? relDay(lastSteps.date) : "no steps data yet")}
    ${tile("Last night", night ? fmt.hours(night.hours) : "—", nightCtx)}
    ${tile("Reports", fmt.int(reports), `${released} released as PDF`)}
    ${upNextCard(taskData, exams)}
    ${card("PC active time", "Per day, only while you were at the PC (not AFK)", `<div id="c-pc"></div>${tableView(daily.map((d) => [d.date, fmt.hm(d.pc_active_minutes)]), ["Day", "Active"])}`, 6)}
    ${card("Steps", "Per day, from the iPhone", `<div id="c-steps"></div>${tableView(daily.map((d) => [d.date, fmt.int(d.steps)]), ["Day", "Steps"])}`, 6)}
    ${card("Sleep", "Hours asleep per night, by wake-up day", `<div id="c-sleep"></div>${tableView(daily.map((d) => [d.date, fmt.hours(d.sleep_hours)]), ["Night ending", "Asleep"])}`, 6)}
    ${card("Last night's sleep stages", night ? `${dayLong(night.date)} · ${fmt.hours(night.hours)} asleep` : "", stageBody, 6)}
    ${card(`Top apps · last ${Math.min(range, 90)} days`, "Active time per app", barList(apps.slice(0, 10).map((a) => ({ name: a.app, value: a.minutes })), fmt.hm), 12)}`;

  columnChart($("#c-pc", main), pcPts.map((p) => ({ ...p, y: p.y && p.y / 60 })), { valueFmt: (h) => fmt.hm(h * 60), axisFmt: (h) => `${h}h` });
  columnChart($("#c-steps", main), stepPts, { valueFmt: (n) => `${fmt.int(n)} steps`, emptyText: "No steps in this range yet" });
  columnChart($("#c-sleep", main), sleepPts, { valueFmt: (h) => `${h.toFixed(2)} h asleep`, axisFmt: (h) => `${h}h`, emptyText: "No sleep in this range yet" });
}

async function viewDocs(main) {
  main.innerHTML = `<div class="top"><h1>Docs</h1><span class="sub" id="synced"></span></div><div id="body" class="grid"><div class="card span-12 empty">Loading…</div></div>`;
  const d = await api("/summary/docs");
  const short = (repo) => repo.split("/")[1].replace(/_docs$/i, "").replace(/^LaTeX$/, "LaTeX").replace(/^Typst$/, "Typst");
  const by = Object.fromEntries(d.repos.map((r) => [short(r.repo), r]));
  $("#synced", main).textContent = d.last_sync ? `Synced from GitHub ${new Date(d.last_sync).toLocaleString()}` : "";

  // reports per subject folder (LaTeX + Typst); notes per top-level folder (PersonalDocs)
  // subject = the folder's last part (e.g. "Denshi"); a semester/year part is added
  // only when two subjects would otherwise share a name (e.g. AI in both semesters)
  const semester = (p) => ({ First_Semester: "1st sem", Second_Semester: "2nd sem" }[p] || p);
  const reportFolders = [], noteFolders = {};
  for (const r of d.repos) {
    for (const [folder, n] of Object.entries(r.items_per_folder)) {
      const parts = folder.split("/");
      if (r.reports) reportFolders.push({ parts, path: `${short(r.repo)}: ${folder}`, value: n });
      else { const top = folder === "." ? "(root)" : parts[0]; noteFolders[top] = (noteFolders[top] || 0) + n; }
    }
  }
  const lastCount = {};
  reportFolders.forEach((f) => (lastCount[f.parts.at(-1)] = (lastCount[f.parts.at(-1)] || 0) + 1));
  reportFolders.forEach((f) => {
    const last = f.parts.at(-1);
    f.name = lastCount[last] > 1 && f.parts.length > 1 ? `${last} · ${semester(f.parts.at(-2))}` : last;
  });
  reportFolders.sort((a, b) => b.value - a.value);
  const noteList = Object.entries(noteFolders).map(([name, value]) => ({ name, value })).sort((a, b) => b.value - a.value);

  const months = [...new Set(d.added_per_month.map((m) => m.month))].sort();
  const sumFor = (kind) => months.map((m) => ({ key: m, label: `${MON[+m.slice(5) - 1]} ’${m.slice(2, 4)}`, tip: `${MON[+m.slice(5) - 1]} ${m.slice(0, 4)}`,
    y: d.added_per_month.filter((x) => x.month === m && x.kind === kind).reduce((a, x) => a + x.added, 0) || null }));

  const releases = d.recent_releases.map((r) => `<li><div class="main"><div class="title">${esc(r.name || r.tag)}</div>
      <div class="meta">${esc(short(r.repo))} · ${esc(new Date(r.published_at).toLocaleDateString())} · ${esc(r.assets.map((a) => a.name).join(", ") || "no files")}</div></div>
      <a class="pill" href="${esc(r.url)}" target="_blank" rel="noopener">GitHub ↗</a></li>`).join("");

  $("#body", main).innerHTML = `
    ${tile("LaTeX reports", fmt.int(by.LaTeX?.reports), `${fmt.int(by.LaTeX?.files)} files · ${by.LaTeX?.releases ?? 0} released`, by.LaTeX && `https://github.com/${by.LaTeX.repo}`)}
    ${tile("Typst reports", fmt.int(by.Typst?.reports), `${fmt.int(by.Typst?.files)} files · ${by.Typst?.releases ?? 0} released`, by.Typst && `https://github.com/${by.Typst.repo}`)}
    ${tile("Notes", fmt.int(by.PersonalDocs?.notes), `PersonalDocs · ${fmt.int(by.PersonalDocs?.files)} files`, by.PersonalDocs && `https://github.com/${by.PersonalDocs.repo}`)}
    ${tile("Releases", fmt.int(d.repos.reduce((a, r) => a + r.releases, 0)), "final PDFs on GitHub")}
    ${card("Recent releases", "Final versions published as GitHub releases", releases ? `<ul class="list">${releases}</ul>` : `<div class="empty">No releases yet</div>`, 12)}
    ${card("Reports per subject", "Folders with a .tex/.typ file, templates excluded", barList(reportFolders, fmt.int), 6)}
    ${card("Notes per folder", "Markdown notes in PersonalDocs", barList(noteList, fmt.int), 6)}
    ${card("Reports added per month", "By first commit to GitHub (files uploaded when a repo was created all count in that month)", `<div id="c-rep"></div>`, 6)}
    ${card("Notes added per month", "By first commit to GitHub", `<div id="c-notes"></div>`, 6)}`;
  columnChart($("#c-rep", main), sumFor("report"), { valueFmt: (n) => `${n} reports`, emptyText: "No reports yet" });
  columnChart($("#c-notes", main), sumFor("note"), { valueFmt: (n) => `${n} notes`, emptyText: "No notes yet" });
}

async function viewProjects(main) {
  main.innerHTML = `<div class="top"><h1>GitHub Projects</h1><span class="sub" id="synced"></span></div><div id="body" class="grid"><div class="card span-12 empty">Loading…</div></div>`;
  const d = await api("/summary/github");
  $("#synced", main).textContent = d.last_sync ? `Synced from GitHub ${new Date(d.last_sync).toLocaleString()}` : "";
  const repos = d.repos;
  const owned = repos.filter((r) => r.owned);
  const active = repos.filter((r) => r.pushed_at && Date.now() - new Date(r.pushed_at) < 30 * 864e5);
  const topLang = Object.entries(d.languages).find(([l]) => l !== "(none)");
  const state = { q: "", filter: store.get("projFilter", "all") };
  const FILTERS = [["all", "All"], ["owned", "Mine"], ["collab", "Collaborator"], ["private", "Private"], ["public", "Public"]];

  const row = (r) => {
    const [owner, name] = r.repo.split("/");
    const pills = [r.private ? "private" : "public", !r.owned && `by ${owner}`, r.fork && "fork", r.archived && "archived"].filter(Boolean);
    const meta = [r.language, r.stars ? `★ ${r.stars}` : "", r.open_issues ? `${r.open_issues} open issue${r.open_issues > 1 ? "s" : ""}` : "", `updated ${ago(r.pushed_at)}`].filter(Boolean);
    return `<li><div class="main"><div class="title"><a href="${esc(r.url)}" target="_blank" rel="noopener">${esc(r.owned ? name : r.repo)}</a>
      ${pills.map((p) => `<span class="pill">${esc(p)}</span>`).join(" ")}</div>
      ${r.description ? `<div class="desc-line">${esc(r.description)}</div>` : ""}
      <div class="meta">${esc(meta.join(" · "))}</div></div>
      <a class="pill" href="${esc(r.url)}" target="_blank" rel="noopener">GitHub ↗</a></li>`;
  };
  const drawList = () => {
    const q = state.q.toLowerCase();
    const shown = repos.filter((r) => ({ all: true, owned: r.owned, collab: !r.owned, private: r.private, public: !r.private }[state.filter]))
      .filter((r) => !q || `${r.repo} ${r.description ?? ""} ${r.language ?? ""} ${r.topics.join(" ")}`.toLowerCase().includes(q));
    $("#repo-list", main).innerHTML = shown.length ? `<ul class="list">${shown.map(row).join("")}</ul>` : `<div class="empty">No repos match</div>`;
    $("#repo-count", main).textContent = `${shown.length} of ${repos.length}`;
  };

  $("#body", main).innerHTML = `
    ${tile("Repositories", fmt.int(repos.length), `${owned.length} yours · ${repos.length - owned.length} as collaborator`)}
    ${tile("Private / public", `${repos.filter((r) => r.private).length} / ${repos.filter((r) => !r.private).length}`, "visibility")}
    ${tile("Active", fmt.int(active.length), "pushed in the last 30 days")}
    ${tile("Top language", topLang ? topLang[0] : "—", topLang ? `${topLang[1]} repo${topLang[1] > 1 ? "s" : ""}` : "")}
    <section class="card span-8"><div class="list-head"><h2>Repositories <span class="count" id="repo-count"></span></h2>
        <input type="search" id="repo-q" placeholder="Search name, description, language" aria-label="Search repositories"></div>
      <div class="seg filters" role="group" aria-label="Filter">${FILTERS.map(([k, n]) => `<button data-f="${k}" aria-pressed="${k === state.filter}">${n}</button>`).join("")}</div>
      <div id="repo-list"></div></section>
    ${card("Languages", "Main language of each repo, as GitHub detects it", barList(Object.entries(d.languages).map(([name, value]) => ({ name: name === "(none)" ? "No language" : name, value })), (n) => `${n} repo${n > 1 ? "s" : ""}`), 4)}`;
  $("#repo-q", main).oninput = (e) => { state.q = e.target.value; drawList(); };
  main.querySelectorAll("[data-f]").forEach((b) => b.onclick = () => {
    state.filter = b.dataset.f; store.set("projFilter", state.filter);
    main.querySelectorAll("[data-f]").forEach((x) => x.setAttribute("aria-pressed", x === b));
    drawList();
  });
  drawList();
}

// ---------- workspaces ----------
async function send(path, method, body) {
  const opts = { method, credentials: "same-origin" };
  if (body instanceof FormData) opts.body = body;
  else if (body !== undefined) { opts.body = JSON.stringify(body); opts.headers = { "Content-Type": "application/json" }; }
  const r = await fetch(path, opts);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(typeof data.detail === "string" ? data.detail : `${path} → ${r.status}`);
  return data;
}

/** Render $...$ / $$...$$ math inside an element with KaTeX (vendored, loaded with defer). */
function renderMath(el) {
  if (!el || !window.renderMathInElement) return;
  try {
    window.renderMathInElement(el, {
      delimiters: [{ left: "$$", right: "$$", display: true }, { left: "\\[", right: "\\]", display: true },
                   { left: "$", right: "$", display: false }, { left: "\\(", right: "\\)", display: false }],
      throwOnError: false, ignoredTags: ["script", "noscript", "style", "textarea", "pre", "code"],
    });
  } catch { /* leave the raw TeX visible */ }
}

/** Small, safe Markdown renderer for chat answers (escapes everything first). Math is kept
 *  out of the Markdown rules (so `*` or `_` inside a formula stay intact) and left for KaTeX. */
function md(src) {
  const blocks = [];
  const keep = (html) => { blocks.push(html); return `\u0000${blocks.length - 1}\u0000`; };
  let s = esc(src)
    .replace(/```(\w*)\n([\s\S]*?)```/g, (_, lang, code) => keep(`<pre><code>${code.replace(/\n$/, "")}</code></pre>`))
    .replace(/\$\$[\s\S]+?\$\$/g, (m) => keep(m.includes("\n") ? `<div class="math-block">${m}</div>` : m))
    .replace(/\$(?=\S)[^$\n]*?\S\$|\$\S\$/g, keep)
    .replace(/`([^`\n]+)`/g, (_, c) => keep(`<code>${c}</code>`))   // inline code: a | inside can't split a table cell
    .replace(/\\\|/g, () => keep("|"));                              // \| = a literal pipe in a table cell
  const inline = (t) => t
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*])\*([^*\s][^*]*)\*/g, "$1<em>$2</em>")
    .replace(/~~([^~]+)~~/g, "<del>$1</del>")
    .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>')
    .replace(/\[([^\]]+)\]\((#\/[^\s)]+)\)/g, '<a href="$2">$1</a>');           // links inside the dashboard
  // GitHub-style tables: a header row, then a separator row like |---|:--:|--:|
  const cells = (l) => { let t = l.trim(); if (t.startsWith("|")) t = t.slice(1); if (t.endsWith("|")) t = t.slice(0, -1); return t.split("|").map((c) => c.trim()); };
  const isSep = (l) => l.includes("|") && /^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$/.test(l);
  const out = []; let list = null;
  const close = () => { if (list) { out.push(`</${list}>`); list = null; } };
  const lines = s.split("\n");
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    let m;
    if (line.includes("|") && i + 1 < lines.length && isSep(lines[i + 1]) && cells(line).length === cells(lines[i + 1]).length) {
      close();
      const head = cells(line), n = head.length;
      const align = cells(lines[i + 1]).map((c) => (c.startsWith(":") && c.endsWith(":") ? "center" : c.endsWith(":") ? "right" : ""));
      const td = (tag, c, k) => `<${tag}${align[k] ? ` style="text-align:${align[k]}"` : ""}>${inline(c)}</${tag}>`;
      const rows = [];
      for (i += 2; i < lines.length && lines[i].includes("|") && lines[i].trim(); i++) {
        const r = cells(lines[i]);
        rows.push(Array.from({ length: n }, (_, k) => r[k] ?? ""));
      }
      i--;
      out.push(`<div class="md-table"><table><thead><tr>${head.map((c, k) => td("th", c, k)).join("")}</tr></thead>`
        + `<tbody>${rows.map((r) => `<tr>${r.map((c, k) => td("td", c, k)).join("")}</tr>`).join("")}</tbody></table></div>`);
    }
    else if (/^\u0000\d+\u0000$/.test(line.trim()) && /^<(pre|div)/.test(blocks[+line.trim().slice(1, -1)])) { close(); out.push(line.trim()); }   // code block / display math on its own line
    else if ((m = line.match(/^(#{1,6})\s+(.*)/))) { const h = Math.min(m[1].length + 2, 6); close(); out.push(`<h${h}>${inline(m[2])}</h${h}>`); }
    else if ((m = line.match(/^\s*[-*]\s+(.*)/))) { if (list !== "ul") { close(); out.push("<ul>"); list = "ul"; } out.push(`<li>${inline(m[1])}</li>`); }
    else if ((m = line.match(/^\s*\d+[.)]\s+(.*)/))) { if (list !== "ol") { close(); out.push("<ol>"); list = "ol"; } out.push(`<li>${inline(m[1])}</li>`); }
    else if (/^\s*(---|\*\*\*)\s*$/.test(line)) { close(); out.push("<hr>"); }
    else if ((m = line.match(/^\s*&gt;\s?(.*)/))) { close(); out.push(`<blockquote>${inline(m[1])}</blockquote>`); }
    else if (!line.trim()) { close(); out.push(""); }
    else { close(); out.push(`<p>${inline(line)}</p>`); }
  }
  close();
  // restore placeholders; a block can contain another one (e.g. math inside a code block)
  let html = out.join("\n");
  for (let k = 0; k < 3 && html.includes("\u0000"); k++) html = html.replace(/\u0000(\d+)\u0000/g, (_, i) => blocks[+i]);
  return html;
}

// ---------- Studio viewers (documents, flashcards, quiz) in a modal ----------
const STUDIO_KINDS = [
  ["guide", "Study guide", "Concepts in order, formulas, examples, a checklist"],
  ["briefing", "Briefing", "One-page overview and key points"],
  ["glossary", "Glossary", "Important terms with definitions"],
  ["flashcards", "Flashcards", "Flip cards for active recall"],
  ["quiz", "Quiz", "Multiple choice with explanations"],
  ["plan", "Study plan", "Tasks with due dates, for your task list"],
];
const OUT_EXT = /\.(flashcards|quiz|plan)\.json$|\.md$/;
const outputKind = (name) => name.endsWith(".flashcards.json") ? "Flashcards" : name.endsWith(".quiz.json") ? "Quiz" : name.endsWith(".plan.json") ? "Study plan"
  : /\.(md|markdown|txt)$/i.test(name) ? "Document" : "File";

function openModal(title, actionsHtml = "") {
  document.querySelector(".modal")?.remove();
  document.body.insertAdjacentHTML("beforeend", `<div class="modal" role="dialog" aria-modal="true" aria-label="${esc(title)}">
    <div class="modal-box"><header><h2>${esc(title)}</h2><span class="spacer"></span>${actionsHtml}
      <button class="icon-btn" data-close aria-label="Close">Close</button></header><div class="modal-body"></div></div></div>`);
  const m = document.querySelector(".modal");
  const close = () => { m.remove(); document.removeEventListener("keydown", onKey); };
  const onKey = (e) => { if (e.key === "Escape") close(); };
  m.querySelector("[data-close]").onclick = close;
  m.onclick = (e) => { if (e.target === m) close(); };
  document.addEventListener("keydown", onKey);
  return { body: m.querySelector(".modal-body"), close, el: m };
}

function flashcardsViewer(body, data) {
  let order = data.cards.map((_, i) => i), pos = 0, flipped = false, onlyAgain = false;
  const again = new Set(), known = new Set();
  const draw = () => {
    const deck = onlyAgain ? order.filter((i) => again.has(i)) : order;
    if (!deck.length) { body.innerHTML = `<div class="empty">Nothing marked "again". <button class="btn small" id="fc-all">Show all cards</button></div>`; body.querySelector("#fc-all").onclick = () => { onlyAgain = false; pos = 0; draw(); }; return; }
    pos = Math.min(pos, deck.length - 1);
    const c = data.cards[deck[pos]];
    body.innerHTML = `<div class="fc-top"><span>${pos + 1} / ${deck.length}</span><span class="meta">${known.size} known · ${again.size} again</span></div>
      <button class="fc-card${flipped ? " flipped" : ""}" id="fc-card" aria-label="Flip card">
        <div class="fc-label">${flipped ? "Answer" : "Question"}</div>
        <div class="fc-text md">${md(flipped ? c.back : c.front)}</div>
        ${flipped && c.source ? `<div class="fc-src">${esc(c.source)}</div>` : `<div class="fc-hint">${flipped ? "" : "Tap to show the answer"}</div>`}
      </button>
      <div class="fc-actions">
        <button class="btn" id="fc-prev" ${pos === 0 ? "disabled" : ""}>← Prev</button>
        ${flipped ? `<button class="btn" id="fc-again">Again</button><button class="btn primary" id="fc-known">Got it</button>` : `<button class="btn primary" id="fc-flip">Show answer</button>`}
        <button class="btn" id="fc-next" ${pos >= deck.length - 1 ? "disabled" : ""}>Next →</button>
      </div>
      <div class="fc-actions secondary"><button class="btn small" id="fc-shuffle">Shuffle</button>
        <label class="check"><input type="checkbox" id="fc-only" ${onlyAgain ? "checked" : ""}> Only cards marked "again"</label></div>`;
    renderMath(body);
    const go = (d) => { pos = Math.max(0, Math.min(deck.length - 1, pos + d)); flipped = false; draw(); };
    const mark = (set, other) => { set.add(deck[pos]); other.delete(deck[pos]); if (pos < deck.length - 1) go(1); else { flipped = false; draw(); } };
    body.querySelector("#fc-card").onclick = () => { flipped = !flipped; draw(); };
    body.querySelector("#fc-flip")?.addEventListener("click", () => { flipped = true; draw(); });
    body.querySelector("#fc-prev").onclick = () => go(-1);
    body.querySelector("#fc-next").onclick = () => go(1);
    body.querySelector("#fc-again")?.addEventListener("click", () => mark(again, known));
    body.querySelector("#fc-known")?.addEventListener("click", () => mark(known, again));
    body.querySelector("#fc-shuffle").onclick = () => { order = [...order].sort(() => Math.random() - 0.5); pos = 0; flipped = false; draw(); };
    body.querySelector("#fc-only").onchange = (e) => { onlyAgain = e.target.checked; pos = 0; flipped = false; draw(); };
  };
  draw();
}

function quizViewer(body, data) {
  const picked = {};
  const draw = () => {
    const answered = Object.keys(picked).length;
    const right = data.questions.filter((q, i) => picked[i] === q.answer).length;
    body.innerHTML = `<div class="quiz-score">${answered === data.questions.length ? `<strong>Score: ${right} / ${data.questions.length}</strong>` : `${answered} of ${data.questions.length} answered · ${right} correct`}
        <button class="btn small" id="qz-reset" ${answered ? "" : "disabled"}>Start over</button></div>
      ${data.questions.map((q, i) => {
        const done = picked[i] !== undefined;
        return `<section class="qz"><div class="qz-q md"><span class="qz-n">${i + 1}.</span> ${md(q.question)}</div>
          <div class="qz-choices">${q.choices.map((c, j) => {
            const cls = !done ? "" : j === q.answer ? " correct" : j === picked[i] ? " wrong" : " dim";
            const mark = !done ? "" : j === q.answer ? " ✓" : j === picked[i] ? " ✗" : "";
            return `<button class="qz-c${cls}" data-q="${i}" data-c="${j}" ${done ? "disabled" : ""}><span class="md">${md(c)}</span>${mark}</button>`;
          }).join("")}</div>
          ${done ? `<div class="qz-exp md">${picked[i] === q.answer ? "<strong>Correct.</strong> " : "<strong>Not quite.</strong> "}${md(q.explanation)}${q.source ? `<div class="fc-src">${esc(q.source)}</div>` : ""}</div>` : ""}</section>`;
      }).join("")}`;
    renderMath(body);
    body.querySelectorAll("[data-q]").forEach((b) => b.onclick = () => { picked[+b.dataset.q] = +b.dataset.c; const y = body.scrollTop; draw(); body.scrollTop = y; });
    body.querySelector("#qz-reset").onclick = () => { for (const k in picked) delete picked[k]; draw(); body.scrollTop = 0; };
  };
  draw();
}

async function openOutput(base, name, onDeleted) {
  const url = `${base}/files/outputs/${encodeURIComponent(name)}`;
  const practice = /\.(flashcards|quiz)\.json$/.test(name);
  const m = openModal(name.replace(OUT_EXT, ""),
    `${practice ? `<button class="icon-btn" data-repeat title="Make a task for this every day">↻ Repeat daily…</button>` : ""}${/\.md$/i.test(name) ? `<button class="icon-btn" data-export title="Copy into your Obsidian vault as a new note">Save to PersonalDocs</button>` : ""}<a class="icon-btn" href="${url}" target="_blank" rel="noopener">Raw file</a><button class="icon-btn danger" data-del>Delete</button>`);
  m.el.querySelector("[data-repeat]")?.addEventListener("click", () => {
    const slug = decodeURIComponent(base.split("/")[2]), cards = name.endsWith(".flashcards.json");
    const label = name.replace(OUT_EXT, "").replace(/^(Flashcards|Quiz) - /, "").replace(/ \(\d{4}-\d{2}-\d{2} \d{4}\)$/, "");
    recurringModal({ title: `${cards ? "Flashcards" : "Quiz"}: ${label}`, workspace: slug, labels: ["review"], priority: 3,
      link: `#/workspaces/${slug}?open=${encodeURIComponent(name).replace(/[()]/g, (c) => `%${c.charCodeAt(0).toString(16).toUpperCase()}`)}`, link_label: cards ? "Open flashcards" : "Open quiz" });
  });
  m.el.querySelector("[data-export]")?.addEventListener("click", async (e) => {
    const slug = decodeURIComponent(base.split("/")[2]), btn = e.currentTarget;
    btn.disabled = true;
    try {
      const r = await send(`/notes-export/${encodeURIComponent(slug)}/${encodeURIComponent(name)}`, "POST", {});
      const el = toast(`Saved to <button class="link" data-go>${esc(r.path)}</button>`, 6000);
      $("[data-go]", el).onclick = () => { m.close(); location.hash = r.href; };
    } catch (err) { alert(err.message); btn.disabled = false; }
  });
  m.el.querySelector("[data-del]").onclick = async () => {
    if (!confirm(`Delete “${name}”?`)) return;
    try { await send(`${base}/outputs/${encodeURIComponent(name)}`, "DELETE"); m.close(); onDeleted?.(); } catch (e) { alert(e.message); }
  };
  m.body.innerHTML = `<div class="empty">Loading…</div>`;
  try {
    const r = await fetch(url, { credentials: "same-origin" });
    if (!r.ok) throw new Error(`${r.status}`);
    const kind = outputKind(name);
    if (kind === "Flashcards") flashcardsViewer(m.body, await r.json());
    else if (kind === "Quiz") quizViewer(m.body, await r.json());
    else if (kind === "Study plan") await planViewer(m.body, await r.json(), decodeURIComponent(base.split("/")[2]), name);
    else if (kind === "Document") { m.body.innerHTML = `<article class="md doc">${md(await r.text())}</article>`; renderMath(m.body); }
    else { m.close(); window.open(url, "_blank", "noopener"); }
  } catch (e) { m.body.innerHTML = `<div class="error">Couldn't open: ${esc(e.message)}</div>`; }
}

const fileSize = (n) => (n == null ? "" : n >= 1048576 ? `${(n / 1048576).toFixed(1)} MB` : n >= 1024 ? `${Math.round(n / 1024)} KB` : `${n} B`);

async function viewWorkspaces(main) {
  main.innerHTML = `<div class="top"><h1>Workspaces</h1><span class="sub">Study spaces with sources and a Claude chat</span></div>
    <div class="grid" id="body"><div class="card span-12 empty">Loading…</div></div>`;
  const list = await api("/workspaces");
  $("#body", main).innerHTML = `
    <section class="card span-12"><h2>New workspace</h2><p class="desc">For a subject, a project, or a set of readings</p>
      <form id="new-ws" class="row-form">
        <input name="name" required maxlength="80" placeholder="Name, e.g. Applied Math II" aria-label="Name">
        <input name="description" maxlength="1000" placeholder="What it's for (optional, Claude reads this)" aria-label="Description">
        <button class="btn primary" type="submit">Create</button>
      </form><div class="form-msg" id="new-msg"></div></section>
    ${list.length ? list.map((w) => `<a class="card span-4 ws-card" href="#/workspaces/${esc(w.slug)}">
        <h2>${esc(w.name)}</h2><p class="desc">${esc(w.description || "No description")}</p>
        <div class="meta">${w.sources} source${w.sources === 1 ? "" : "s"} · ${w.messages} message${w.messages === 1 ? "" : "s"} · ${w.outputs} note${w.outputs === 1 ? "" : "s"}${w.last_chat ? ` · last chat ${ago(w.last_chat)}` : ""}</div></a>`).join("")
      : `<div class="card span-12 empty">No workspaces yet. Create one above.</div>`}`;
  $("#new-ws", main).onsubmit = async (e) => {
    e.preventDefault();
    const f = new FormData(e.target);
    try { const r = await send("/workspaces", "POST", { name: f.get("name"), description: f.get("description") }); location.hash = `#/workspaces/${r.slug}`; }
    catch (err) { $("#new-msg", main).textContent = err.message; }
  };
}

// Claude models for workspace chat + Studio; Sonnet is the default (lighter on the plan's limits)
const WS_MODELS = [["sonnet", "Sonnet", "Good for most questions; uses much less of your plan than Opus"],
  ["opus", "Opus", "Strongest, for hard problems; uses your plan's limit fastest"],
  ["haiku", "Haiku", "Fastest and lightest; fine for quick lookups and simple questions"]];
const modelOptions = (cur) => WS_MODELS.map(([id, label, hint]) => `<option value="${id}" title="${esc(hint)}"${id === cur ? " selected" : ""}>${label}</option>`).join("");

async function viewWorkspace(main, slug) {
  main.innerHTML = `<div class="top"><a class="icon-btn" href="#/workspaces">← Workspaces</a></div><div class="card empty">Loading…</div>`;
  const w = await api(`/workspaces/${encodeURIComponent(slug)}`);
  const base = `/workspaces/${encodeURIComponent(slug)}`;
  let day = w.today, streaming = false;

  main.innerHTML = `<div class="top"><a class="icon-btn" href="#/workspaces">← Workspaces</a><h1>${esc(w.name)}</h1>
      <span class="sub">${esc(w.description || "")}</span><span class="spacer"></span>
      ${focusButton({ workspace: slug, cls: "btn primary" })}
      <button class="icon-btn danger" id="del-ws">Delete workspace</button></div>
    <div class="grid ws-grid">
      <div class="span-4 ws-side">
        <section class="card"><h2>Exams</h2><p class="desc">Countdown, study time before each, results</p><div id="ws-exams"></div></section>
        <section class="card" id="ws-notes-card" hidden><h2>Notes</h2><p class="desc">Recently changed in the linked folders</p><div class="ws-notes-body"></div></section>
        <section class="card"><h2>Tasks <span class="count"></span></h2><p class="desc">Linked to this workspace · Claude sees these</p><div id="ws-tasks"></div></section>
        <section class="card"><h2>Studio</h2><p class="desc">Make study material from your sources</p>
          <div class="studio-kinds">${STUDIO_KINDS.map(([k, label, hint]) => `<button class="studio-btn" data-kind="${k}" title="${esc(hint)}"><strong>${esc(label)}</strong><span>${esc(hint)}</span></button>`).join("")}</div>
          <form id="studio-form" class="stack studio-form" hidden></form>
          <div id="jobs"></div>
        </section>
        <section class="card"><h2>Sources <span class="count" id="src-count"></span></h2>
          <p class="desc">Claude reads these; it never changes them</p>
          <div id="folders"></div>
          <div id="sources"></div>
          <div class="seg tabs" role="tablist">${["File", "Text", "Link", "GitHub", "Folder"].map((t, i) => `<button data-tab="${t}" aria-pressed="${i === 0}">${t}</button>`).join("")}</div>
          <div id="add-panel"></div><div class="form-msg" id="add-msg"></div>
        </section>
        <section class="card"><h2>Notes & study material</h2><p class="desc">From Studio and the chat (<code>outputs/</code>)</p><div id="outputs"></div></section>
      </div>
      <section class="card span-8 chat-card">
        <div class="days" id="days"></div>
        <div class="messages" id="messages"></div>
        <form class="composer" id="composer">
          <textarea name="m" rows="3" placeholder="Ask about your sources, or ask for notes, a study guide, a quiz…" aria-label="Message"></textarea>
          <div class="composer-row"><label class="model-pick" title="Model for this workspace's chat and Studio">Model <select id="ws-model" aria-label="Claude model">${modelOptions(w.model)}</select></label>
            <span class="hint" id="chat-status">Enter to send · Shift+Enter for a new line</span>
            <button class="btn primary" type="submit" id="send-btn">Send</button></div>
        </form>
      </section>
    </div>`;

  const kindLabel = { file: "file", text: "text", url: "web", youtube: "YouTube", github: "GitHub" };
  const drawSources = (list) => {
    $("#src-count", main).textContent = list.length ? `${list.length}` : "";
    $("#sources", main).innerHTML = list.length ? `<ul class="list compact">${list.map((s) => `<li><div class="main">
        <div class="title"><a href="${base}/files/sources/${encodeURIComponent(s.filename)}" target="_blank" rel="noopener">${esc(s.title)}</a></div>
        <div class="meta"><span class="pill">${esc(kindLabel[s.kind] || s.kind)}</span> ${esc(s.filename)} · ${fileSize(s.size)}</div></div>
        <button class="x" data-del="${s.id}" title="Remove source" aria-label="Remove ${esc(s.title)}">×</button></li>`).join("")}</ul>`
      : (w.folders || []).length ? "" : `<div class="empty">No sources yet. Add some below.</div>`;
    main.querySelectorAll("[data-del]").forEach((b) => b.onclick = async () => {
      if (!confirm("Remove this source from the workspace?")) return;
      await send(`${base}/sources/${b.dataset.del}`, "DELETE"); refresh();
    });
  };
  // ---- linked folders (read live from the PC, read-only)
  const drawFolders = () => {
    const list = w.folders || [];
    $("#src-count", main).textContent = list.length + w.sources.length ? `${list.length + w.sources.length}` : "";
    $("#folders", main).innerHTML = list.length ? `<ul class="list compact">${list.map((f) => `<li class="folder-src"><div class="main">
        <div class="title">📂 ${esc(f.title)}</div>
        <div class="meta"><span class="pill live">folder · live</span> ${f.ok ? `${f.files}${f.truncated ? "+" : ""} files · ${fileSize(f.size)} · changed ${esc(f.modified ? ago(f.modified) : "never")}` : `<span class="error">missing or not allowed any more</span>`}</div>
        <div class="crumb" title="${esc(f.path)}">${esc(f.path)}</div>
        ${f.ok ? `<details class="folder-files" data-fid="${f.id}"><summary>What Claude sees</summary><div class="empty">Loading…</div></details>` : ""}</div>
        <button class="x" data-unlink="${f.id}" title="Unlink folder" aria-label="Unlink ${esc(f.title)}">×</button></li>`).join("")}</ul>` : "";
    main.querySelectorAll("[data-unlink]").forEach((b) => b.onclick = async () => {
      if (!confirm("Unlink this folder? The folder and its files on your PC are not touched.")) return;
      await send(`${base}/folders/${b.dataset.unlink}`, "DELETE"); refresh();
    });
    main.querySelectorAll(".folder-files").forEach((d) => d.ontoggle = async () => {
      if (!d.open || d.dataset.loaded) return;
      d.dataset.loaded = "1";
      try {
        const files = await api(`${base}/folders/${d.dataset.fid}/files`);
        d.querySelector(".empty, ul")?.remove();
        d.insertAdjacentHTML("beforeend", files.length ? `<ul class="folder-list">${files.slice(0, 200).map((x) => `<li><span>${esc(x.path)}</span><span class="meta">${fileSize(x.size)} · ${esc(ago(x.modified))}</span></li>`).join("")}</ul>${files.length > 200 ? `<div class="meta">and ${files.length - 200} more</div>` : ""}`
          : `<div class="empty">No files</div>`);
      } catch (e) { d.querySelector(".empty").textContent = e.message; }
    });
  };
  const drawOutputs = (list) => {
    $("#outputs", main).innerHTML = list.length ? `<ul class="list compact">${list.map((o) => `<li><div class="main">
        <div class="title"><button class="link" data-open="${esc(o.name)}">${esc(o.name.replace(OUT_EXT, ""))}</button></div>
        <div class="meta"><span class="pill">${outputKind(o.name)}</span> ${fileSize(o.size)} · ${esc(ago(o.modified))}</div></div></li>`).join("")}</ul>`
      : `<div class="empty">Nothing yet. Use Studio above, or ask Claude for notes in the chat.</div>`;
    main.querySelectorAll("[data-open]").forEach((b) => b.onclick = () => openOutput(base, b.dataset.open, refresh));
  };

  // ---- Studio
  let pollTimer = null;
  const drawJobs = (jobs) => {
    const recent = jobs.filter((j) => j.status === "running" || Date.now() - new Date(j.finished_at || j.started_at) < 30 * 60e3).slice(0, 4);
    $("#jobs", main).innerHTML = recent.length ? `<ul class="list compact jobs">${recent.map((j) => {
      const secs = Math.round(((j.finished_at ? new Date(j.finished_at) : Date.now()) - new Date(j.started_at)) / 1000);
      const t = `${Math.floor(secs / 60)}:${String(secs % 60).padStart(2, "0")}`;
      return `<li><div class="main"><div class="title">${esc(j.label)} · ${esc(j.title || j.options.focus || "all sources")}</div>
        <div class="meta">${j.status === "running" ? `<span class="typing">Making… ${t}</span>`
          : j.status === "done" ? `Done in ${t} · <button class="link" data-open="${esc(j.filename)}">Open</button>`
          : `<span class="error">Failed: ${esc(j.error || "")}</span>`}</div></div></li>`;
    }).join("")}</ul>` : "";
    $("#jobs", main).querySelectorAll("[data-open]").forEach((b) => b.onclick = () => openOutput(base, b.dataset.open, refresh));
  };
  async function pollJobs() {
    clearTimeout(pollTimer);
    if (!main.isConnected || !location.hash.startsWith(`#/workspaces/${slug}`)) return;
    const jobs = await api(`${base}/studio/jobs`).catch(() => []);
    drawJobs(jobs);
    if (jobs.some((j) => j.status === "running")) pollTimer = setTimeout(pollJobs, 3000);
    else refresh();
  }
  main.querySelectorAll("[data-kind]").forEach((b) => b.onclick = () => {
    const [kind, label] = STUDIO_KINDS.find(([k]) => k === b.dataset.kind);
    main.querySelectorAll("[data-kind]").forEach((x) => x.classList.toggle("on", x === b));
    const counted = kind === "flashcards" || kind === "quiz", plan = kind === "plan";
    const form = $("#studio-form", main);
    form.hidden = false;
    form.innerHTML = `<div class="subhead">${esc(label)}</div>
      <input name="focus" maxlength="500" placeholder="${plan ? "Goal (optional), e.g. midterm on chapters 1-4, or finish the lab report" : "Focus (optional), e.g. chapter 3, or momentum"}">
      <div class="row-form">${plan ? `<label class="field">Finish by <input name="deadline" type="date" min="${todayISO()}"></label>` : ""}${counted ? `<label class="field">${kind === "quiz" ? "Questions" : "Cards"} <input name="count" type="number" min="3" max="60" value="${kind === "quiz" ? 10 : 20}"></label>` : ""}
        <label class="field">Model <select name="model">${modelOptions(w.model)}</select></label>
        <label class="field">Language <select name="language"><option value="auto">Same as sources</option><option value="en">English</option><option value="ja">日本語</option><option value="th">ไทย</option></select></label></div>
      ${w.sources.length > 1 ? `<details class="pick"><summary>Sources: all (${w.sources.length})</summary>${w.sources.map((s) => `<label class="check"><input type="checkbox" name="src" value="${esc(s.filename)}"> ${esc(s.title)}</label>`).join("")}</details>` : ""}
      <div class="composer-row"><span class="hint">${plan ? "Then pick the tasks to add" : "Takes 1–3 minutes · uses your Claude plan"}</span>
        <button class="btn" type="button" id="st-cancel">Cancel</button><button class="btn primary" type="submit">Make ${esc(label.toLowerCase())}</button></div>
      <div class="form-msg" id="st-msg"></div>`;
    form.querySelectorAll('input[name="src"]').forEach((c) => c.onchange = () => {
      const n = form.querySelectorAll('input[name="src"]:checked').length;
      form.querySelector(".pick summary").textContent = n ? `Sources: ${n} chosen` : `Sources: all (${w.sources.length})`;
    });
    form.querySelector("#st-cancel").onclick = () => { form.hidden = true; b.classList.remove("on"); };
    form.onsubmit = async (e) => {
      e.preventDefault();
      const f = new FormData(form);
      try {
        await send(`${base}/studio`, "POST", { kind, focus: f.get("focus") || "", count: +(f.get("count") || 20),
          language: f.get("language"), sources: f.getAll("src"), deadline: f.get("deadline") || null, model: f.get("model") });
        form.hidden = true; b.classList.remove("on"); pollJobs();
      } catch (err) { form.querySelector("#st-msg").textContent = err.message; }
    };
  });
  const drawDays = (days) => {
    const all = days.some((d) => d.day === w.today) ? days : [{ day: w.today, messages: 0 }, ...days];
    $("#days", main).innerHTML = all.map((d) => `<button class="day" data-day="${d.day}" aria-pressed="${d.day === day}">${d.day === w.today ? "Today" : esc(dayLabel(d.day))}${d.summarized ? ' <span class="dot" title="Summarized">✓</span>' : ""}</button>`).join("");
    main.querySelectorAll("[data-day]").forEach((b) => b.onclick = () => { if (streaming) return; day = b.dataset.day; drawDays(days); loadMessages(); });
  };
  const sugBag = {};                       // ```tasks blocks in answers, filled in by hydrateSuggestions
  const msgHtml = (m) => m.role === "user" ? `<div class="msg user"><div class="bubble">${esc(m.content).replace(/\n/g, "<br>")}</div></div>`
    : m.role === "error" ? `<div class="msg error"><div class="bubble">⚠ ${esc(m.content)}</div></div>`
    : `<div class="msg assistant">${m.tools?.length ? `<details class="tools"><summary>${m.tools.length} step${m.tools.length > 1 ? "s" : ""}</summary><ul>${m.tools.map((t) => `<li>${esc(t)}</li>`).join("")}</ul></details>` : ""}<div class="bubble md">${m.id ? answerHtml(m.content, m.id, sugBag) : md(m.content)}</div></div>`;
  async function loadMessages() {
    const box = $("#messages", main);
    const msgs = await api(`${base}/messages?day=${day}`);
    const past = day !== w.today;
    box.innerHTML = msgs.length ? msgs.map(msgHtml).join("") : `<div class="empty">${past ? "No messages that day." : "Start by asking about your sources. Claude reads them before answering and cites them."}</div>`;
    if (past) box.insertAdjacentHTML("afterbegin", `<div class="past-note">${esc(dayLong(day))}: read-only. ${w.days.find((d) => d.day === day)?.summarized ? `<a href="${base}/files/history/${day}.md" target="_blank" rel="noopener">Day summary</a>` : "A summary is written when you next chat."}</div>`);
    $("#composer", main).hidden = past;
    renderMath(box);
    box.scrollTop = box.scrollHeight;
    hydrateSuggestions(box, slug, sugBag);
  }
  async function refresh() {
    const d = await api(base);
    Object.assign(w, d);
    drawSources(d.sources); drawFolders(); drawOutputs(d.outputs); drawDays(d.days);
  }

  // ---- add-source panels
  const panels = {
    File: () => `<label class="drop"><input type="file" id="f-in" multiple accept=".pdf,.md,.markdown,.txt,.csv,.json,.png,.jpg,.jpeg,.gif,.webp,.tex,.typ,.bib,.html,.htm,.py,.c,.h,.cpp,.java,.js,.ts,.lua,.ipynb">
        <span>Choose files (PDF, Markdown, text, images, code, up to 50 MB)</span></label>`,
    Text: () => `<form id="t-form" class="stack"><input name="title" required maxlength="120" placeholder="Title"><textarea name="text" required rows="4" placeholder="Paste text…"></textarea><button class="btn" type="submit">Add text</button></form>`,
    Link: () => `<form id="u-form" class="stack"><input name="url" type="url" required placeholder="https://… (web page, PDF or YouTube)"><button class="btn" type="submit">Add link</button></form>`,
    Folder: () => `<div class="stack"><p class="desc" style="margin:0">Link a folder from your PC's document repos. Claude reads its <strong>current</strong> files every time (saved is enough, no commit needed) and can't change them.</p>
        <select id="d-root" aria-label="Repository folder"><option value="">Choose a folder…</option></select><div id="d-browse"></div></div>`,
    GitHub: () => `<div class="stack"><select id="g-repo" aria-label="Repository"><option value="">Choose a repository…</option></select>
        <div id="g-browse"></div><div class="subhead">Release PDFs</div><div id="g-rel"><div class="empty">Loading…</div></div></div>`,
  };
  const busy = (on, text = "") => { $("#add-msg", main).textContent = text; main.querySelectorAll("#add-panel button, #add-panel input").forEach((x) => (x.disabled = on)); };
  const added = async (p) => { try { busy(true, "Adding…"); await p; busy(false, "Added."); refresh(); } catch (e) { busy(false, e.message); } };
  async function showPanel(name) {
    main.querySelectorAll("[data-tab]").forEach((b) => b.setAttribute("aria-pressed", b.dataset.tab === name));
    $("#add-panel", main).innerHTML = panels[name](); $("#add-msg", main).textContent = "";
    if (name === "File") $("#f-in", main).onchange = (e) => added((async () => { for (const f of e.target.files) { const fd = new FormData(); fd.append("file", f); await send(`${base}/sources/upload`, "POST", fd); } })());
    if (name === "Text") $("#t-form", main).onsubmit = (e) => { e.preventDefault(); const f = new FormData(e.target); added(send(`${base}/sources/text`, "POST", { title: f.get("title"), text: f.get("text") }).then(() => e.target.reset())); };
    if (name === "Link") $("#u-form", main).onsubmit = (e) => { e.preventDefault(); added(send(`${base}/sources/url`, "POST", { url: new FormData(e.target).get("url") }).then(() => e.target.reset())); };
    if (name === "Folder") {
      const roots = await api(`${base}/folders/roots`);
      $("#d-root", main).insertAdjacentHTML("beforeend", roots.map((r) => `<option value="${r.id}">${esc(r.name)}</option>`).join(""));
      const browse = async (root, path = "") => {
        const box = $("#d-browse", main); box.innerHTML = `<div class="empty">Loading…</div>`;
        try {
          const d = await api(`${base}/folders/browse?root=${root}&path=${encodeURIComponent(path)}`);
          const linked = (w.folders || []).some((f) => f.path === d.abs);
          const up = d.path ? `<li><button class="link" data-dir="${esc(d.path.split("/").slice(0, -1).join("/"))}">↑ ..</button></li>` : "";
          box.innerHTML = `<div class="crumb">${esc(d.root)}/${esc(d.path)}</div>
            <ul class="list compact tree">${up}${d.dirs.map((x) => `<li><button class="link" data-dir="${esc(x.path)}">📁 ${esc(x.name)}</button></li>`).join("") || (up ? "" : `<li class="meta">No sub-folders</li>`)}</ul>
            <div class="composer-row"><span class="hint">${d.files} file${d.files === 1 ? "" : "s"} here${d.dirs.length ? ` + ${d.dirs.length} folder${d.dirs.length === 1 ? "" : "s"}` : ""}</span>
              <button class="btn small primary" id="d-link"${linked ? " disabled" : ""}>${linked ? "Linked" : `Link “${esc(d.path ? d.path.split("/").pop() : d.root)}”`}</button></div>`;
          box.querySelectorAll("[data-dir]").forEach((b) => b.onclick = () => browse(root, b.dataset.dir));
          $("#d-link", box).onclick = async () => { await added(send(`${base}/folders`, "POST", { root: +root, path: d.path })); await refresh(); browse(root, d.path); };
        } catch (e) { box.innerHTML = `<div class="error">${esc(e.message)}</div>`; }
      };
      $("#d-root", main).onchange = (e) => (e.target.value !== "" ? browse(e.target.value) : ($("#d-browse", main).innerHTML = ""));
    }
    if (name === "GitHub") {
      const [gh, rels] = await Promise.all([api("/summary/github"), api(`${base}/github/releases`)]);
      $("#g-repo", main).insertAdjacentHTML("beforeend", gh.repos.map((r) => `<option value="${esc(r.repo)}">${esc(r.repo)}</option>`).join(""));
      const browse = async (repo, path = "") => {
        const box = $("#g-browse", main); box.innerHTML = `<div class="empty">Loading…</div>`;
        try {
          const items = await api(`${base}/github/browse?repo=${encodeURIComponent(repo)}&path=${encodeURIComponent(path)}`);
          const up = path ? `<li><button class="link" data-dir="${esc(path.split("/").slice(0, -1).join("/"))}">↑ ..</button></li>` : "";
          box.innerHTML = `<div class="crumb">${esc(repo)}/${esc(path)}</div><ul class="list compact tree">${up}${items.map((i) => i.type === "dir"
            ? `<li><button class="link" data-dir="${esc(i.path)}">📁 ${esc(i.name)}</button></li>`
            : `<li><span class="main">${esc(i.name)} <span class="meta">${fileSize(i.size)}</span></span><button class="btn small" data-file="${esc(i.path)}">Add</button></li>`).join("")}</ul>`;
          box.querySelectorAll("[data-dir]").forEach((b) => b.onclick = () => browse(repo, b.dataset.dir));
          box.querySelectorAll("[data-file]").forEach((b) => b.onclick = () => added(send(`${base}/sources/github`, "POST", { repo, path: b.dataset.file })));
        } catch (e) { box.innerHTML = `<div class="error">${esc(e.message)}</div>`; }
      };
      $("#g-repo", main).onchange = (e) => e.target.value && browse(e.target.value);
      $("#g-rel", main).innerHTML = rels.length ? `<ul class="list compact">${rels.flatMap((r) => r.assets.map((a) => `<li><span class="main">${esc(r.name || r.tag)} <span class="meta">${esc(a.name)}</span></span>
          <button class="btn small" data-rel='${esc(JSON.stringify({ repo: r.repo, tag: r.tag, asset: a.name }))}'>Add</button></li>`)).join("")}</ul>` : `<div class="empty">No releases</div>`;
      main.querySelectorAll("[data-rel]").forEach((b) => b.onclick = () => added(send(`${base}/sources/github`, "POST", JSON.parse(b.dataset.rel))));
    }
  }
  main.querySelectorAll("[data-tab]").forEach((b) => b.onclick = () => showPanel(b.dataset.tab));

  // ---- chat
  const ta = $("#composer textarea", main);
  // Phones/tablets (touch, no hover) have no Shift+Enter: Return adds a new line there and the Send
  // button sends. With a mouse/keyboard, Enter sends and Shift+Enter adds a new line.
  const touch = matchMedia("(pointer: coarse) and (hover: none)").matches;
  ta.setAttribute("enterkeyhint", touch ? "enter" : "send");
  if (touch) $("#chat-status", main).textContent = "Return for a new line · tap Send to send";
  ta.onkeydown = (e) => {
    if (e.key !== "Enter" || e.isComposing) return;
    const send = touch ? e.metaKey || e.ctrlKey : !e.shiftKey;      // iPad + keyboard: Cmd+Enter sends
    if (send) { e.preventDefault(); $("#composer", main).requestSubmit(); }
  };
  $("#composer", main).onsubmit = async (e) => {
    e.preventDefault();
    const text = ta.value.trim();
    if (!text || streaming) return;
    streaming = true; ta.value = ""; $("#send-btn", main).disabled = true;
    const box = $("#messages", main);
    box.querySelector(".empty")?.remove();
    box.insertAdjacentHTML("beforeend", msgHtml({ role: "user", content: text }) + `<div class="msg assistant live"><ul class="live-steps"></ul><div class="bubble md"><span class="typing">Claude is thinking…</span></div></div>`);
    const live = box.lastElementChild, bubble = live.querySelector(".bubble"), steps = live.querySelector(".live-steps");
    box.scrollTop = box.scrollHeight;
    let answer = "", pending = false;
    // a ```tasks block becomes a checklist once the answer is saved; while streaming, just say so
    const paint = () => { pending = false; bubble.innerHTML = md(answer.replace(/`{3}tasks[\s\S]*?(`{3}|$)/g, "\n*Preparing task suggestions…*\n")); renderMath(bubble); box.scrollTop = box.scrollHeight; };
    try {
      const r = await fetch(`${base}/chat`, { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ message: text }) });
      if (!r.ok) { const d = await r.json().catch(() => ({})); throw new Error(d.detail || `chat → ${r.status}`); }
      const reader = r.body.getReader(), dec = new TextDecoder();
      let buf = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        let i;
        while ((i = buf.indexOf("\n\n")) >= 0) {
          const chunk = buf.slice(0, i); buf = buf.slice(i + 2);
          if (!chunk.startsWith("data: ")) continue;
          const ev = JSON.parse(chunk.slice(6));
          if (ev.type === "text") { answer += ev.text; if (!pending) { pending = true; requestAnimationFrame(paint); } }
          else if (ev.type === "tool") { steps.insertAdjacentHTML("beforeend", `<li>${esc(ev.text)}</li>`); box.scrollTop = box.scrollHeight; }
          else if (ev.type === "error") { live.outerHTML = msgHtml({ role: "error", content: ev.text }); }
          else if (ev.type === "done") { $("#chat-status", main).textContent = ev.cost_usd != null ? `Today's chat so far ≈ $${ev.cost_usd.toFixed(2)} at API prices: not billed, counts toward your plan's limit` : ""; }
        }
      }
    } catch (err) {
      live.outerHTML = msgHtml({ role: "error", content: err.message });
    } finally {
      streaming = false; $("#send-btn", main).disabled = false;
      await refresh(); await loadMessages();
    }
  };

  $("#ws-model", main).onchange = async (e) => {
    const sel = e.target, prev = w.model;
    try { w.model = (await send(base, "PATCH", { model: sel.value })).model; $("#chat-status", main).textContent = `Using ${WS_MODELS.find(([id]) => id === w.model)[1]} from the next message`; }
    catch (err) { sel.value = prev; alert(err.message); }
  };

  $("#del-ws", main).onclick = async () => {
    if (!confirm(`Delete “${w.name}” with all its sources, notes and chat history? This can't be undone.`)) return;
    try { await send(base, "DELETE"); location.hash = "#/workspaces"; } catch (e) { alert(e.message); }
  };

  workspaceTasksPanel($("#ws-tasks", main), slug);
  workspaceNotesCard($("#ws-notes-card", main), slug);
  wireFocusButtons(main);
  // "Plan for this exam": open Studio → Study plan, finishing the day before, focused on the scope
  const planFor = (e) => {
    const b = main.querySelector('[data-kind="plan"]');
    b.click();
    const f = $("#studio-form", main);
    f.elements.focus.value = `Prepare for ${e.title}${e.scope ? `: ${e.scope}` : ""}`;
    if (f.elements.deadline) f.elements.deadline.value = dayBefore(e.date);
    f.scrollIntoView({ behavior: "smooth", block: "center" });
  };
  examsPanel($("#ws-exams", main), { workspace: slug, onPlan: planFor, compact: true }).then(() => {
    const planId = new URLSearchParams(location.hash.split("?")[1] || "").get("plan");
    if (planId) api(`/exams?workspace=${encodeURIComponent(slug)}`).then((l) => { const e = l.find((x) => x.id === +planId); if (e) planFor(e); });
  });
  drawSources(w.sources); drawFolders(); drawOutputs(w.outputs); drawDays(w.days); showPanel("File"); pollJobs(); await loadMessages();
  // deep link: #/workspaces/<slug>?open=<file in outputs/> opens it in the viewer
  const toOpen = new URLSearchParams(location.hash.split("?")[1] || "").get("open");
  if (toOpen) openOutput(base, toOpen, refresh);
}

function viewSoon(main, title, intro, items) {
  main.innerHTML = `<div class="top"><h1>${esc(title)}</h1><span class="pill">coming soon</span></div>
    <div class="grid">${card("What this will be", intro, `<ul>${items.map((i) => `<li>${esc(i)}</li>`).join("")}</ul>`, 12)}</div>`;
}

const VIEWS = {
  overview: viewOverview,
  docs: viewDocs,
  projects: viewProjects,
  workspaces: (m, arg) => (arg ? viewWorkspace(m, arg) : viewWorkspaces(m)),
  tasks: viewTasks,
  focus: viewFocus,
  money: viewMoney,
  notes: viewNotes,
  search: viewSearch,
};

async function render() {
  const [, view = "overview", arg] = location.hash.match(/^#\/(\w+)(?:\/([\w-]+))?/) || [];
  const fn = VIEWS[view] || VIEWS.overview;
  document.querySelectorAll(".modal, .pop, .sheet").forEach((el) => el.remove());   // navigating closes dialogs and menus
  document.querySelectorAll(".nav a").forEach((a) =>
    a.dataset.view === (view === "docs" ? "notes" : view) ? a.setAttribute("aria-current", "page") : a.removeAttribute("aria-current"));
  tabbarSync(view);
  const main = $("#main");
  try { await fn(main, arg); }
  catch (e) { main.innerHTML = `<div class="top"><h1>Something went wrong</h1></div><div class="card error">${esc(e.message)}</div>`; }
  window._last = Date.now();
}

window.addEventListener("hashchange", render);
document.addEventListener("visibilitychange", () => { if (!document.hidden && Date.now() - (window._last || 0) > 5 * 60e3) render(); });
focusRefresh();
api("/whoami").then((w) => { if (w.name) $("#who").textContent = `Signed in as ${w.name}`; }).catch(() => {});
render();
