// Phone navigation: a bottom tab bar with 4 pinned sections + "More", which opens a sheet with
// every section (and lets you choose the 4). Built from the sidebar's links, so a new section
// only needs adding to index.html. Loaded before app.js; app.js calls tabbarSync(view) on render.
"use strict";

const TABBAR_DEFAULT = ["overview", "tasks", "search", "money"];
const TABBAR_MAX = 4;
const navItems = () => [...document.querySelectorAll(".nav a[data-view]")].map((a) => ({
  view: a.dataset.view, href: a.getAttribute("href"), label: a.textContent.trim(), short: a.dataset.short || "",
  icon: a.querySelector("svg")?.outerHTML || "",
}));
function tabbarPinned() {
  let v;
  try { v = JSON.parse(localStorage.getItem("nav.pinned")); } catch { v = null; }
  const known = new Set(navItems().map((i) => i.view));
  v = Array.isArray(v) ? v.filter((x) => known.has(x)).slice(0, TABBAR_MAX) : null;
  return v && v.length ? v : TABBAR_DEFAULT.filter((x) => known.has(x));
}
function tabbarSave(list) { try { localStorage.setItem("nav.pinned", JSON.stringify(list)); } catch { /* private mode */ } }

const MORE_ICON = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="5" cy="12" r="1.6"/><circle cx="12" cy="12" r="1.6"/><circle cx="19" cy="12" r="1.6"/></svg>`;
let tabbarView = "overview";

function tabbarDraw() {
  let bar = document.querySelector(".tabbar");
  if (!bar) {
    document.body.insertAdjacentHTML("beforeend", `<nav class="tabbar" aria-label="Sections"></nav>`);
    bar = document.querySelector(".tabbar");
  }
  const items = navItems(), pinned = tabbarPinned();
  const view = tabbarView === "docs" ? "notes" : tabbarView;
  const cur = items.find((i) => i.view === view);
  const inMore = cur && !pinned.includes(view);
  bar.innerHTML = pinned.map((v) => items.find((i) => i.view === v)).filter(Boolean).map((i) =>
    `<a href="${i.href}" ${i.view === view ? 'aria-current="page"' : ""}>${i.icon}<span>${i.short || i.label}</span></a>`).join("")
    + `<button type="button" class="tb-more" ${inMore ? 'aria-current="page"' : ""} aria-haspopup="dialog" aria-label="More sections${inMore ? `, now on ${cur.label}` : ""}">
        ${inMore ? cur.icon : MORE_ICON}<span>${inMore ? cur.short || cur.label : "More"}</span></button>`;
  bar.querySelector(".tb-more").onclick = () => tabbarSheet();
}

function tabbarSheet() {
  document.querySelector(".sheet")?.remove();
  let editing = false;
  document.body.insertAdjacentHTML("beforeend", `<div class="sheet" role="dialog" aria-modal="true" aria-label="All sections">
    <div class="sheet-box"><div class="sheet-grab" aria-hidden="true"></div>
      <div class="sheet-head"><strong>All sections</strong><span class="spacer"></span><button class="btn small" data-edit>Edit bar</button></div>
      <p class="desc sheet-hint" hidden>Tap to choose up to ${TABBAR_MAX} sections for the bar. The rest stay here.</p>
      <div class="sheet-grid"></div></div></div>`);
  const sheet = document.querySelector(".sheet"), grid = $(".sheet-grid", sheet);
  const close = () => { sheet.classList.add("closing"); setTimeout(() => sheet.remove(), 160); document.removeEventListener("keydown", onKey); };
  const onKey = (e) => { if (e.key === "Escape") close(); };
  document.addEventListener("keydown", onKey);
  sheet.onclick = (e) => { if (e.target === sheet) close(); };
  const draw = () => {
    const pinned = tabbarPinned(), view = tabbarView === "docs" ? "notes" : tabbarView;
    grid.innerHTML = navItems().map((i) => {
      const on = pinned.includes(i.view);
      return editing
        ? `<button type="button" class="sheet-tile${on ? " pinned" : ""}" data-pin="${i.view}" aria-pressed="${on}">${i.icon}<span>${i.label}</span><i class="pin" aria-hidden="true">${on ? pinned.indexOf(i.view) + 1 : "+"}</i></button>`
        : `<a class="sheet-tile${i.view === view ? " on" : ""}" href="${i.href}">${i.icon}<span>${i.label}</span>${on ? '<i class="pin dot" aria-hidden="true"></i>' : ""}</a>`;
    }).join("");
    $(".sheet-hint", sheet).hidden = !editing;
    $("[data-edit]", sheet).textContent = editing ? "Done" : "Edit bar";
    grid.querySelectorAll("a").forEach((a) => a.onclick = () => close());
    grid.querySelectorAll("[data-pin]").forEach((b) => b.onclick = () => {
      let list = tabbarPinned();
      const v = b.dataset.pin;
      if (list.includes(v)) { if (list.length > 1) list = list.filter((x) => x !== v); }
      else if (list.length < TABBAR_MAX) list.push(v);
      else list = [...list.slice(0, TABBAR_MAX - 1), v];        // full: replace the last one
      tabbarSave(list); draw(); tabbarDraw();
    });
  };
  $("[data-edit]", sheet).onclick = () => { editing = !editing; draw(); };
  draw();
  requestAnimationFrame(() => sheet.classList.add("open"));
}

/** Called by app.js on every navigation. */
function tabbarSync(view) {
  tabbarView = view || "overview";
  document.querySelector(".sheet")?.remove();
  tabbarDraw();
}
