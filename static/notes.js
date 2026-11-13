// Notes (read-only browser over PersonalDocs / LaTeX_docs / Typst_docs) and search across everything.
// Loaded before app.js; uses its helpers ($, esc, api, md, renderMath, card, ago, dayLabel) at call time.
"use strict";

const notesIndex = {};          // root -> {paths, byName: lowercased name -> path}
const hashParams = () => new URLSearchParams(location.hash.split("?")[1] || "");
const noteHref = (root, path) => `#/notes/${root}${path ? `?p=${encodeURIComponent(path)}` : ""}`;
const rawHref = (root, path) => `/notes/${root}/raw?path=${encodeURIComponent(path)}`;
const NOTE_ICONS = { md: "📝", pdf: "📕", image: "🖼", text: "📄", other: "📦" };
const fileSizeN = (n) => (n < 1024 ? `${n} B` : n < 1048576 ? `${Math.round(n / 1024)} KB` : `${(n / 1048576).toFixed(1)} MB`);

async function loadIndex(root) {
  if (notesIndex[root] && Date.now() - notesIndex[root].t < 60000) return notesIndex[root];
  const paths = await api(`/notes/${root}/index`);
  const byName = {};
  for (const p of paths) {                    // Obsidian resolves by file name; the shortest path wins ties
    const name = p.split("/").pop().toLowerCase();
    const stem = name.replace(/\.md$/, "");
    for (const k of [name, stem, p.toLowerCase(), p.toLowerCase().replace(/\.md$/, "")])
      if (!byName[k] || byName[k].length > p.length) byName[k] = p;
  }
  return (notesIndex[root] = { paths, byName, t: Date.now() });
}

/** Resolve an Obsidian link target ("Note", "folder/Note", "img.png") from the note at `from`. */
function resolveLink(idx, target, from) {
  const t = target.trim().replace(/\\/g, "/");
  const dir = from.includes("/") ? from.slice(0, from.lastIndexOf("/") + 1) : "";
  const rel = (dir + t).split("/").reduce((a, part) => (part === ".." ? a.slice(0, -1) : part === "." || !part ? a : [...a, part]), []).join("/");
  return idx.byName[rel.toLowerCase()] || idx.byName[`${rel.toLowerCase()}.md`] || idx.byName[t.toLowerCase()] || idx.byName[`${t.toLowerCase()}.md`]
    || idx.byName[t.split("/").pop().toLowerCase()] || null;
}

/** Obsidian Markdown → HTML: properties, [[links]], ![[embeds]], images, tasks, callouts, ==highlights==, nesting. */
function noteHtml(src, root, path, idx) {
  let props = null;
  if (src.startsWith("---")) {
    const end = src.indexOf("\n---", 3);
    if (end > 0) { props = src.slice(3, end).trim(); src = src.slice(end + 4).replace(/^\r?\n/, ""); }
  }
  src = src.replace(/\r\n/g, "\n").replace(/%%[\s\S]*?%%/g, "").replace(/<!--[\s\S]*?-->/g, "");   // hidden comments
  const toks = [];
  const tok = (html) => { toks.push(html); return `\u0001${toks.length - 1}\u0001`; };
  const media = (target, alt, size) => {
    const p = /^https?:\/\//.test(target) ? null : resolveLink(idx, decodeURIComponent(target), path);
    if (!p && !/^https?:\/\//.test(target)) return tok(`<span class="n-missing" title="Not found in ${esc(root)}">${esc(target)}</span>`);
    const url = p ? rawHref(root, p) : target, ext = (p || target).split(".").pop().toLowerCase();
    if (["png", "jpg", "jpeg", "gif", "webp", "svg", "bmp", "heic"].includes(ext))
      return tok(`<img class="n-img" src="${esc(url)}" alt="${esc(alt || "")}" loading="lazy"${/^\d+$/.test(size || "") ? ` style="width:${+size}px"` : ""}>`);
    if (ext === "pdf") return tok(`<a class="n-embed" href="${esc(p ? noteHref(root, p) : url)}">📕 ${esc(alt || target.split("/").pop())}</a>`);
    if (p && /\.md$/i.test(p)) return tok(`<a class="n-embed" href="${esc(noteHref(root, p))}">↪ ${esc(alt || p.split("/").pop().replace(/\.md$/i, ""))}</a>`);
    return tok(`<a href="${esc(url)}" target="_blank" rel="noopener">${esc(alt || target)}</a>`);
  };
  // protect code first so links inside code stay literal
  src = src.replace(/```[\s\S]*?```|`[^`\n]+`/g, (m) => m.replace(/\[|\]|!|=/g, (c) => `\u0002${c.charCodeAt(0)}\u0002`));
  src = src
    .replace(/!\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|([^\]]*))?\]\]/g, (_, t, a) => media(t, /^\d+(x\d+)?$/.test(a || "") ? "" : a, (a || "").split("x")[0]))
    .replace(/!\[([^\]]*)\]\(<?([^)>\s]+)>?(?:\s+"[^"]*")?\)/g, (_, alt, t) => { const [a, sz] = alt.split("|"); return media(t, a, sz); })
    .replace(/\[\[([^\]|#]+)(#[^\]|]*)?(?:\|([^\]]*))?\]\]/g, (_, t, h, a) => {
      const p = resolveLink(idx, t, path), label = a || (t.split("/").pop() + (h ? ` › ${h.slice(1)}` : ""));
      return tok(p ? `<a class="n-link" href="${esc(noteHref(root, p))}">${esc(label)}</a>` : `<span class="n-missing" title="No such note">${esc(label)}</span>`);
    })
    .replace(/\[([^\]]+)\]\((?!https?:|#|mailto:)<?([^)>]+?)>?\)/g, (m, label, t) => {     // relative [x](other.md)
      const p = resolveLink(idx, decodeURIComponent(t.split("#")[0]), path);
      return p ? tok(`<a class="n-link" href="${esc(noteHref(root, p))}">${esc(label)}</a>`) : m;
    })
    .replace(/^(\s*)[-*+] \[( |x|X)\] /gm, (_, ind, x) => `${ind}- ${tok(`<input type="checkbox" disabled${x.trim() ? " checked" : ""}>`)} `)
    .replace(/^( {2,}|\t+)([-*+]|\d+[.)]) /gm, (_, ind, b) => `${b === "*" || b === "+" ? "-" : b} ${tok(`<i class="n-ind" data-l="${Math.min(4, Math.round(ind.replace(/\t/g, "    ").length / 3))}"></i>`)}`)
    .replace(/^> ?\[!(\w+)\][+-]? ?(.*)$/gm, (_, type, title) => `> ${tok(`<strong class="n-callout n-${esc(type.toLowerCase())}">${esc(title || type)}</strong>`)}`)
    .replace(/==([^=\n]+)==/g, (_, t) => `${tok("<mark>")}${t}${tok("</mark>")}`);
  src = src.replace(/\u0002(\d+)\u0002/g, (_, c) => String.fromCharCode(+c));
  let html = md(src).replace(/\u0001(\d+)\u0001/g, (_, i) => toks[+i]);
  html = html.replace(/<li>(<i class="n-ind" data-l="(\d)"><\/i>)/g, '<li class="n-l$2">');
  html = html.replace(/<blockquote>(<strong class="n-callout n-(\w+)">)/g, '<blockquote class="n-co n-co-$2">$1');
  const propHtml = props ? `<details class="n-props"><summary>Properties</summary><pre>${esc(props)}</pre></details>` : "";
  return propHtml + html;
}

function crumbs(root, rootName, path, isFile) {
  const parts = path ? path.split("/") : [];
  let acc = "";
  const links = parts.map((p, i) => {
    acc = acc ? `${acc}/${p}` : p;
    return i === parts.length - 1 && isFile ? `<span>${esc(p)}</span>` : `<a href="${esc(noteHref(root, acc))}">${esc(p)}</a>`;
  });
  return `<nav class="n-crumbs" aria-label="Folder"><a href="#/notes">Notes</a><a href="${esc(noteHref(root, ""))}">${esc(rootName)}</a>${links.join("")}</nav>`;
}

function folderList(t, current = "") {
  const dirs = t.dirs.map((d) => `<li><a href="${esc(noteHref(t.root, d.path))}"><span class="n-ico">📁</span><span class="main"><span class="title">${esc(d.name)}</span><span class="meta">${d.files} file${d.files === 1 ? "" : "s"} · ${esc(ago(d.modified))}</span></span></a></li>`);
  const files = t.files.map((f) => `<li${f.path === current ? ' class="on"' : ""}><a href="${esc(noteHref(t.root, f.path))}"><span class="n-ico">${NOTE_ICONS[f.kind]}</span><span class="main"><span class="title">${esc(f.kind === "md" ? f.name.replace(/\.md$/i, "") : f.name)}</span><span class="meta">${esc(ago(f.modified))} · ${fileSizeN(f.size)}</span></span></a></li>`);
  return dirs.length + files.length ? `<ul class="n-list">${dirs.join("")}${files.join("")}</ul>` : `<div class="empty">Empty folder</div>`;
}

async function viewNotes(main, root) {
  const path = hashParams().get("p") || "";
  if (!root) return notesHome(main);
  main.innerHTML = `<div class="card empty">Loading…</div>`;
  const parent = (p) => (p.includes("/") ? p.slice(0, p.lastIndexOf("/")) : "");
  // a file, or a folder?
  let file = null;
  if (path && /\.[^/]+$/.test(path)) file = await api(`/notes/${root}/file?path=${encodeURIComponent(path)}`).catch(() => null);
  const dir = file ? parent(file.path) : path;
  const [t, idx] = await Promise.all([api(`/notes/${root}/tree?path=${encodeURIComponent(dir)}`), file?.kind === "md" ? loadIndex(root) : null]);
  if (!file) {
    main.innerHTML = `<div class="top n-top">${crumbs(root, t.root_name, t.path, false)}<span class="spacer"></span><a class="icon-btn" href="#/search">Search</a></div>
      <section class="card n-folder">${folderList(t)}</section>`;
    return;
  }
  let body;
  if (file.kind === "md") body = `<article class="n-doc md">${noteHtml(file.content, root, file.path, idx)}</article>`;
  else if (file.kind === "pdf") body = `<div class="n-pdf" id="n-pdf"><div class="empty">Loading the PDF…</div></div>
      <p class="desc"><a href="${esc(rawHref(root, file.path))}" target="_blank" rel="noopener">Open the PDF in a new tab ↗</a></p>`;
  else if (file.kind === "image") body = `<img class="n-img n-solo" src="${esc(rawHref(root, file.path))}" alt="${esc(file.name)}">`;
  else if (file.kind === "text") body = `<pre class="n-code"><code>${esc(file.content)}</code></pre>`;
  else body = `<div class="empty">No preview for this kind of file. <a href="${esc(rawHref(root, file.path))}" target="_blank" rel="noopener">Open it ↗</a></div>`;
  const title = file.kind === "md" ? file.name.replace(/\.md$/i, "") : file.name;
  const wsChips = (file.workspaces || []).map((w) => `<a class="pill n-ws" href="#/workspaces/${esc(w.slug)}" title="Open the workspace">📘 ${esc(w.name)}</a>`).join("");
  main.innerHTML = `<div class="top n-top">${crumbs(root, t.root_name, file.path, true)}<span class="spacer"></span>${wsChips}
      <button class="btn small" id="n-task" title="A task that links back to this note">+ Task</button></div>
    <div class="grid n-grid">
      <section class="card span-8 n-main"><h1 class="n-title">${esc(title)}</h1>
        <div class="meta n-meta">Changed ${esc(ago(file.modified))} · ${fileSizeN(file.size)}${file.truncated ? " · only the first 2 MB shown" : ""}
          · <a href="${esc(rawHref(root, file.path))}" target="_blank" rel="noopener">Raw</a></div>
        ${body}
        ${file.backlinks?.length ? `<div class="n-back"><div class="subhead">Linked from</div><ul>${file.backlinks.map((b) => `<li><a href="${esc(noteHref(root, b.path))}">${esc(b.name)}</a> <span class="meta">${esc(parent(b.path))}</span></li>`).join("")}</ul></div>` : ""}
      </section>
      <section class="card span-4 n-side"><div class="list-head"><h2>${esc(t.path ? t.path.split("/").pop() : t.root_name)}</h2></div>${folderList(t, file.path)}</section>
    </div>`;
  const doc = $(".n-doc", main);
  if (doc) {                                     // T-123 in a note → the task
    doc.querySelectorAll("p, li, td, blockquote").forEach((el) => {
      if (/\bT-\d+\b/.test(el.textContent) && !el.querySelector("code, a"))
        el.innerHTML = el.innerHTML.replace(/\bT-(\d+)\b/g, '<a href="#/tasks/$1">T-$1</a>');
    });
    renderMath(doc);
  }
  $("#n-task", main).onclick = () => openTaskDialog({ workspace: file.workspaces?.[0]?.slug || null,
    title: `Review ${title.replace(/\.notebook$/, "")}`, description: `Note: [${title}](${noteHref(root, file.path)})` });
  if (file.kind === "pdf") pdfView($("#n-pdf", main), rawHref(root, file.path), file.name);
  window.scrollTo(0, 0);
}

// ---------- PDF viewer (PDF.js, vendored): every page, on iPad / iPhone too ----------
const PDFJS = "/static/vendor/pdfjs/";
let pdfjsLib = null;

/** Render a PDF into host: all pages (drawn as they scroll into view), page counter, zoom. */
async function pdfView(host, url, name) {
  try {
    if (!pdfjsLib) {
      pdfjsLib = await import(`${PDFJS}pdf.min.mjs`);
      pdfjsLib.GlobalWorkerOptions.workerSrc = `${PDFJS}pdf.worker.min.mjs`;
    }
    const doc = await pdfjsLib.getDocument({ url, cMapUrl: `${PDFJS}cmaps/`, cMapPacked: true, isEvalSupported: false }).promise;
    if (!host.isConnected) { doc.destroy(); return; }
    let zoom = 1;
    host.innerHTML = `<div class="pdf-bar"><span class="pdf-page">1 / ${doc.numPages}</span><span class="spacer"></span>
        <button class="icon-btn" data-z="-1" aria-label="Zoom out">−</button><span class="pdf-zoom">100%</span>
        <button class="icon-btn" data-z="1" aria-label="Zoom in">+</button><button class="icon-btn" data-z="0">Fit</button></div>
      <div class="pdf-scroll"><div class="pdf-pages" aria-label="${esc(name)}"></div></div>`;
    const pagesEl = $(".pdf-pages", host), first = await doc.getPage(1);
    const ratio = first.getViewport({ scale: 1 }).height / first.getViewport({ scale: 1 }).width;
    const slots = [];
    for (let i = 1; i <= doc.numPages; i++) {
      const s = document.createElement("div");
      s.className = "pdf-slot"; s.dataset.page = i; s.style.aspectRatio = `1 / ${ratio}`;
      s.innerHTML = `<span class="pdf-wait">Page ${i}</span>`;          // until the page is drawn
      pagesEl.appendChild(s); slots.push(s);
    }
    const drawn = new Map();                         // page -> width it was drawn at
    const draw = async (slot) => {
      const n = +slot.dataset.page, cssW = slot.clientWidth;
      if (!cssW || drawn.get(n) === cssW) return;
      drawn.set(n, cssW);
      const page = await doc.getPage(n);
      const base = page.getViewport({ scale: 1 });
      const dpr = Math.min(window.devicePixelRatio || 1, 3);
      const vp = page.getViewport({ scale: (cssW / base.width) * dpr });
      const canvas = document.createElement("canvas");
      canvas.width = Math.floor(vp.width); canvas.height = Math.floor(vp.height);
      slot.style.aspectRatio = `${base.width} / ${base.height}`;
      await page.render({ canvasContext: canvas.getContext("2d"), viewport: vp }).promise;
      slot.replaceChildren(canvas);
    };
    const io = new IntersectionObserver((entries) => entries.forEach((e) => {
      if (e.isIntersecting) draw(e.target);
    }), { rootMargin: "600px 0px" });
    slots.forEach((s) => io.observe(s));
    // page counter follows the page nearest the middle of the screen
    const counter = $(".pdf-page", host);
    const onScroll = () => {
      if (!host.isConnected) { window.removeEventListener("scroll", onScroll); io.disconnect(); doc.destroy(); return; }
      const mid = innerHeight / 2;
      const cur = slots.find((s) => { const r = s.getBoundingClientRect(); return r.top <= mid && r.bottom >= mid; });
      if (cur) counter.textContent = `${cur.dataset.page} / ${doc.numPages}`;
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    const setZoom = (z) => {
      zoom = Math.min(3, Math.max(0.5, z));
      pagesEl.style.width = `${zoom * 100}%`;
      $(".pdf-zoom", host).textContent = `${Math.round(zoom * 100)}%`;
      slots.forEach((s) => { const r = s.getBoundingClientRect(); if (r.bottom > -600 && r.top < innerHeight + 600) draw(s); });
    };
    host.querySelectorAll("[data-z]").forEach((b) => b.onclick = () => setZoom(+b.dataset.z === 0 ? 1 : zoom + 0.25 * +b.dataset.z));
  } catch (e) {                                       // fall back to the browser's own viewer
    host.innerHTML = `<iframe src="${esc(url)}" title="${esc(name)}"></iframe><p class="desc">Couldn't render with PDF.js (${esc(e.message || e)}); showing the browser's viewer.</p>`;
  }
}

async function notesHome(main) {
  main.innerHTML = `<div class="top"><h1>Notes</h1><span class="sub">Your notes and reports (read-only; edit them in Obsidian / your editor)</span></div>
    <div class="grid"><div class="card span-12 empty">Loading…</div></div>`;
  const h = await api("/notes");
  $(".grid", main).innerHTML = `
    <section class="card span-12 n-searchcard"><form id="n-q" role="search"><input name="q" type="search" placeholder="Search notes, tasks, chats, exams, money…" aria-label="Search everything"></form></section>
    <section class="card span-12" id="git-sync"><div class="list-head"><h2>Sync with GitHub</h2><span class="count" id="git-when"></span></div>
      <p class="desc">The PC's copies. Pull gets what your iPhone / iPad pushed; Commit &amp; push sends the PC's edits. PersonalDocs also pulls by itself every 10 minutes.</p>
      <div id="git-rows"><div class="empty">Checking…</div></div></section>
    ${h.roots.map((r) => `<a class="card tile span-4 n-root" href="${esc(noteHref(r.id, ""))}"><div class="label">${esc(r.name)}</div>
      <div class="value">${r.notes > 5 ? `${r.notes} notes` : r.pdfs ? `${r.pdfs} PDF${r.pdfs === 1 ? "" : "s"}` : `${r.files} files`}</div>
      <div class="context">${r.notes > 5 && r.pdfs ? `${r.pdfs} PDFs · ` : ""}${r.files} files · changed ${esc(ago(r.modified))}</div></a>`).join("")}
    ${card("Recently changed", "Notes and PDFs, newest first", `<ul class="n-list">${h.recent.map((f) => `<li><a href="${esc(noteHref(f.root, f.path))}"><span class="n-ico">${NOTE_ICONS[f.path.toLowerCase().endsWith(".pdf") ? "pdf" : "md"]}</span><span class="main"><span class="title">${esc(f.path.split("/").pop().replace(/\.md$/i, ""))}</span>
      <span class="meta">${esc(f.root_name)} / ${esc(f.path.includes("/") ? f.path.slice(0, f.path.lastIndexOf("/")) : "")} · ${esc(ago(f.modified))}</span></span></a></li>`).join("")}</ul>`, 8)}
    ${card("Reports & repos", "Counts, releases and sync status from GitHub", `<a class="btn" href="#/docs">Open docs stats →</a>`, 4)}`;
  gitSyncCard($("#git-sync", main));
  $("#n-q", main).onsubmit = (e) => { e.preventDefault(); location.hash = `#/search?q=${encodeURIComponent(e.target.elements.q.value)}`; };
}

// ---------- search ----------
const SEARCH_ICONS = { note: "📝", task: "☑", workspace: "📘", chat: "💬", output: "📄", exam: "📝", money: "¥" };
function highlight(text, terms) {
  let h = esc(text || "");
  for (const t of terms || []) {
    const re = new RegExp(esc(t).replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "gi");
    h = h.replace(re, (m) => `\u0003${m}\u0004`);
  }
  return h.replace(/\u0003/g, "<mark>").replace(/\u0004/g, "</mark>");
}

async function viewSearch(main) {
  const q0 = hashParams().get("q") || "";
  main.innerHTML = `<div class="top"><h1>Search</h1><span class="sub">Notes, tasks, workspace chats and study material, exams, money</span></div>
    <form class="s-form" role="search"><input name="q" type="search" value="${esc(q0)}" placeholder="Search, or type something to capture" aria-label="Search everything" autocomplete="off"></form>
    <div class="cap-row" id="s-cap" hidden><span class="meta">Save as</span>
      <button class="btn small" data-cap="task">☑ Task</button><button class="btn small" data-cap="money">¥ Money</button><button class="btn small" data-cap="note">📝 Note</button></div>
    <div id="s-res"></div>`;
  const input = $(".s-form input", main), res = $("#s-res", main), cap = $("#s-cap", main);
  const syncCap = () => { cap.hidden = !input.value.trim(); };
  cap.querySelectorAll("[data-cap]").forEach((b) => b.onclick = () => capture(b.dataset.cap, input.value.trim(), () => { input.value = ""; syncCap(); run(""); }));
  syncCap();
  let seq = 0, timer;
  const run = async (q) => {
    const my = ++seq;
    history.replaceState(null, "", `#/search${q ? `?q=${encodeURIComponent(q)}` : ""}`);
    if (!q.trim()) { res.innerHTML = `<p class="desc">Type to search; every word must match. Or type a thought and save it as a task, a money entry (e.g. <code>680 food lunch</code>) or a note in PersonalDocs/Inbox. Press <kbd>/</kbd> anywhere to come here.</p>`; return; }
    res.innerHTML = `<p class="desc">Searching…</p>`;
    const r = await api(`/search?q=${encodeURIComponent(q)}`).catch((e) => ({ error: e.message }));
    if (my !== seq) return;
    if (r.error) { res.innerHTML = `<div class="card error">${esc(r.error)}</div>`; return; }
    res.innerHTML = r.groups.length ? `<p class="desc">${r.total} result${r.total === 1 ? "" : "s"}</p>` + r.groups.map((g) => `<section class="card s-group"><div class="list-head"><h2>${esc(g.label)}</h2><span class="count">${g.total}</span></div>
      <ul class="s-list">${g.items.map((it) => `<li><a href="${esc(it.href)}"><span class="n-ico">${SEARCH_ICONS[it.kind] || "·"}</span><span class="main">
        <span class="title">${highlight(it.title, r.terms)}</span><span class="meta">${esc(it.sub || "")}</span>
        ${it.snippet ? `<span class="s-snip">${highlight(it.snippet, r.terms)}</span>` : ""}</span></a></li>`).join("")}</ul>
      ${g.total > g.items.length ? `<p class="desc">Showing ${g.items.length} of ${g.total}. Add a word to narrow it down.</p>` : ""}</section>`).join("")
      : `<div class="card empty">Nothing matches “${esc(q)}”.</div>`;
  };
  input.oninput = () => { syncCap(); clearTimeout(timer); timer = setTimeout(() => run(input.value), 250); };
  $(".s-form", main).onsubmit = (e) => { e.preventDefault(); clearTimeout(timer); run(input.value); };
  input.focus();
  run(q0);
}

// "/" or Ctrl+K anywhere: go to search
document.addEventListener("keydown", (e) => {
  const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName) || document.activeElement?.isContentEditable;
  if (((e.key === "/" && !typing) || (e.key.toLowerCase() === "k" && (e.ctrlKey || e.metaKey))) && !document.querySelector(".modal")) {
    e.preventDefault();
    if (location.hash.startsWith("#/search")) $(".s-form input")?.focus(); else location.hash = "#/search";
  }
});

// ---------- quick capture ----------
/** New task dialog with title / description filled in (the dialog itself only takes properties). */
async function openTaskDialog(preset) {
  if (typeof T === "undefined" || !T) await loadTasks();
  const { title = "", description = "", ...props } = preset;
  newTaskModal(props);
  const f = $(".modal form");
  if (f) { f.elements.title.value = title; f.elements.description.value = description; setTimeout(() => f.elements.title.select()); }
}

/** "680 food lunch", "lunch 680", "¥1,200 eating out ramen" → amount, category, note. */
function parseMoney(text, categories) {
  const m = text.match(/[¥￥]?\s*([\d０-９][\d０-９,，]*)\s*(円|yen)?/i);
  const amount = m ? m[1].replace(/[０-９]/g, (c) => String.fromCharCode(c.charCodeAt(0) - 0xFEE0)).replace(/[,，]/g, "") : "";
  let rest = (m ? text.replace(m[0], " ") : text).replace(/\s+/g, " ").trim();
  const income = /^(income|got|paid me|salary|給料|バイト代)\b/i.test(rest);
  const type = income ? "income" : "expense";
  let category_id = null;
  const cands = categories.filter((c) => c.kind === type && !c.archived).sort((a, b) => b.name.length - a.name.length);
  let best = null;                     // the category word that comes first ("food lunch at school" → Food)
  for (const c of cands) {
    const re = new RegExp(`(^|\\s)${c.name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}(\\s|$)`, "i");
    const hit = re.exec(rest);
    if (hit && (!best || hit.index < best.index)) best = { c, re, index: hit.index };
  }
  if (best) { category_id = best.c.id; rest = rest.replace(best.re, " ").replace(/\s+/g, " ").trim(); }
  return { type, amount, category_id, note: rest };
}

async function capture(kind, text, done) {
  if (!text) return;
  if (kind === "task") { await openTaskDialog({ title: text }); done?.(); return; }
  if (kind === "money") {
    const d = await api("/money");
    const m = openModal("New money entry");
    moneyForm(m.body, d, { initial: parseMoney(text, d.categories), onSaved: (t) => {
      m.close(); done?.();
      const el = toast(`Saved ${yen(t.amount)}${t.category ? ` · ${esc(t.category)}` : ""} <button class="link" data-go>Open Money</button>`);
      $("[data-go]", el).onclick = () => { location.hash = "#/money"; };
    } });
    setTimeout(() => m.body.querySelector("[name=amount]")?.focus());
    return;
  }
  captureNote({ body: text }, done);
}

/** Write a new note into PersonalDocs (Inbox by default). Never overwrites. */
async function captureNote({ title = "", body = "", folder = "" } = {}, done) {
  const lines = body.split("\n");
  if (!title && lines.length > 1 && lines[0].length <= 80) { title = lines[0]; body = lines.slice(1).join("\n").trim(); }
  const fl = await api("/notes-folders").catch(() => ({ inbox: "Inbox", folders: [] }));
  const m = openModal("New note in PersonalDocs");
  m.body.innerHTML = `<form class="m-form" id="nc-f">
      <input name="title" maxlength="200" placeholder="Title (file name)" value="${esc(title || (body.length <= 80 ? body : ""))}" required>
      <textarea name="body" rows="8" placeholder="Markdown…">${esc(title || body.length > 80 ? body : "")}</textarea>
      <label class="field">Folder <input name="folder" list="nc-folders" value="${esc(folder || fl.inbox)}"></label>
      <datalist id="nc-folders"><option value="${esc(fl.inbox)}">${fl.folders.slice(0, 200).map((d) => `<option value="${esc(d)}">`).join("")}</datalist>
      <p class="desc">Saved as a new Markdown file; Obsidian picks it up when the vault syncs. Commit it with the rest of your notes.</p>
      <div class="composer-row"><span class="form-msg" style="flex:1"></span><button class="btn primary">Save note</button></div></form>`;
  const f = $("#nc-f", m.body);
  setTimeout(() => (f.elements.title.value ? f.elements.body : f.elements.title).focus());
  f.onsubmit = async (e) => {
    e.preventDefault();
    try {
      const r = await send("/notes/capture", "POST", { title: f.elements.title.value, body: f.elements.body.value, folder: f.elements.folder.value });
      m.close(); done?.();
      const el = toast(`Saved <button class="link" data-go>${esc(r.path)}</button>`);
      $("[data-go]", el).onclick = () => { location.hash = r.href; };
    } catch (err) { $(".form-msg", f).textContent = err.message; }
  };
}

/** Workspace page: the linked folders in Notes and their recently changed notes. */
async function workspaceNotesCard(card, slug) {
  const d = await api(`/notes-ws/${encodeURIComponent(slug)}?limit=6`).catch(() => null);
  if (!d || !d.folders.length) { card.hidden = true; return; }
  card.hidden = false;
  $(".ws-notes-body", card).innerHTML = `<ul class="n-list">${d.recent.map((n) => `<li><a href="${esc(n.href)}"><span class="n-ico">${NOTE_ICONS[n.kind]}</span><span class="main"><span class="title">${esc(n.name.replace(/\.notebook$/, ""))}</span><span class="meta">${esc(ago(n.modified))}</span></span></a></li>`).join("") || `<li class="empty">No notes yet</li>`}</ul>
    <div class="n-ws-actions">${d.folders.map((f) => `<a class="btn small" href="${esc(f.href)}">📂 Browse ${esc(f.title)}</a>`).join("")}
      <button class="btn small" data-new-note>+ Note</button></div>`;
  $("[data-new-note]", card).onclick = () => captureNote({ folder: d.folders.find((f) => f.root === "personaldocs")?.path || "" });
}

// ---------- git sync (gitsync.py): pull / commit & push the PC's clones from any device ----------
function gitLine(s) {
  const parts = [];
  if (s.behind) parts.push(`<strong>${s.behind} new</strong> on GitHub`);
  if (s.changes.length) parts.push(`<strong>${s.changes.length} changed</strong> on the PC`);
  if (s.ahead) parts.push(`${s.ahead} commit${s.ahead === 1 ? "" : "s"} not pushed`);
  return parts.length ? parts.join(" · ") : "Up to date";
}
const gitWhen = (v) => (v ? `${ago(v.split(" ")[0])}: ${v.split(" ").slice(1).join(" ")}` : "");

async function gitSyncCard(card) {
  const rows = $("#git-rows", card);
  const draw = (list) => {
    rows.innerHTML = list.length ? list.map((s) => `<div class="git-row" data-root="${esc(s.root)}">
      <div class="git-main"><div class="title">${esc(s.name)} <span class="git-state${s.behind || s.changes.length || s.ahead ? " todo" : ""}">${gitLine(s)}</span></div>
        <div class="meta">Last commit: ${esc(s.last_commit?.subject || "—")} · ${esc(s.last_commit ? ago(s.last_commit.date) : "")}</div>
        ${s.last_auto && s.last_auto.includes("skipped") ? `<div class="meta git-warn">Auto-pull ${esc(gitWhen(s.last_auto))}</div>` : ""}
        ${s.root === "personaldocs" ? `<div class="git-task" data-task-box></div>` : ""}
        ${s.changes.length ? `<details class="git-files"><summary>${s.changes.length} file${s.changes.length === 1 ? "" : "s"} changed on the PC</summary><ul>${s.changes.slice(0, 50).map((c) => `<li><span class="pill">${esc(c.kind)}</span> ${esc(c.path)}</li>`).join("")}${s.changes.length > 50 ? `<li>…</li>` : ""}</ul></details>` : ""}
      </div>
      <div class="git-actions"><button class="btn small" data-pull title="${s.root === "personaldocs" ? "Pull, then run Do task if notebook pages are waiting" : "Pull"}">↓ Pull</button>${s.root === "personaldocs" ? `<button class="btn small" data-task title="Run the transcribe-exports skill now">✍ Do task</button>` : ""}<button class="btn small primary" data-push${s.changes.length || s.ahead ? "" : " disabled"}>↑ Commit &amp; push</button></div>
    </div>`).join("") : `<div class="empty">No git repositories found.</div>`;
    const tb = rows.querySelector("[data-task-box]");
    if (tb) doTaskBox(tb, () => load(false));
    rows.querySelectorAll(".git-row").forEach((row) => {
      const root = row.dataset.root, s = list.find((x) => x.root === root);
      row.querySelector("[data-task]")?.addEventListener("click", async (e) => {
        const b = e.currentTarget; b.disabled = true;
        try {
          const r = await send(`/git/${root}/task`, "POST");
          toast(r.started === true ? `✍ Do task started: ${r.pages} page${r.pages === 1 ? "" : "s"}` : `Do task: ${esc(r.reason || "not started")}`, 5000);
          if (tb) doTaskBox(tb, () => load(false));
        } catch (err) { toast(`⚠ ${esc(err.message)}`, 8000); }
        b.disabled = false;
      });
      const busy = (b, on, label) => { b.disabled = on; if (label) b.textContent = label; };
      $("[data-pull]", row).onclick = async (e) => {
        const b = e.currentTarget; busy(b, true, "Pulling…");
        try {
          const r = await send(`/git/${root}/pull${root === "personaldocs" ? "?then_task=true" : ""}`, "POST");
          const t = r.task;
          toast(esc(r.message) + (t ? (t.started === true ? ` · ✍ Do task started: ${t.pages} page${t.pages === 1 ? "" : "s"}` : t.reason === "nothing to transcribe" ? " · no notebook pages to transcribe" : ` · Do task: ${esc(t.reason || "")}`) : ""), 6000);
          load(false);
        }
        catch (err) { toast(`⚠ ${esc(err.message)}`, 9000); busy(b, false, "↓ Pull"); }
      };
      $("[data-push]", row).onclick = () => {
        const m = openModal(`Commit & push ${s.name}`);
        m.body.innerHTML = `<form class="m-form" id="gp-f">
          <p class="desc">${s.changes.length ? `${s.changes.length} changed file${s.changes.length === 1 ? "" : "s"} on the PC will be committed` : "Pushes the PC's unpushed commits"}${s.behind ? `, after taking the ${s.behind} new commit${s.behind === 1 ? "" : "s"} from GitHub` : ""}.</p>
          ${s.changes.length ? `<ul class="git-list">${s.changes.slice(0, 30).map((c) => `<li><span class="pill">${esc(c.kind)}</span> ${esc(c.path)}</li>`).join("")}${s.changes.length > 30 ? `<li>… and ${s.changes.length - 30} more</li>` : ""}</ul>` : ""}
          <label class="field">Message <input name="msg" maxlength="500" placeholder="Last Sync: ${esc(new Date().toISOString().slice(0, 16).replace("T", " "))} (PC)"></label>
          <div class="composer-row"><span class="form-msg" style="flex:1"></span><button class="btn primary">Commit &amp; push</button></div></form>`;
        const f = $("#gp-f", m.body);
        f.onsubmit = async (ev) => {
          ev.preventDefault();
          const b = f.querySelector("button"); busy(b, true, "Pushing…");
          try { const r = await send(`/git/${root}/push`, "POST", { message: f.elements.msg.value }); m.close(); toast(esc(r.message), 5000); load(false); }
          catch (err) { $(".form-msg", f).textContent = err.message; busy(b, false, "Commit & push"); }
        };
      };
    });
  };
  const load = async (fetchRemote) => {
    let list = await api("/git").catch(() => []);
    draw(list);
    if (!fetchRemote) return;
    $("#git-when", card).textContent = "checking GitHub…";
    list = await Promise.all(list.map((s) => api(`/git/${s.root}?fetch=true`).then((x) => ({ ...s, ...x })).catch(() => s)));
    if (card.isConnected) { draw(list); $("#git-when", card).textContent = "checked just now"; }
  };
  load(true);
}

/** The "Do task" line on the PersonalDocs row: running / last report / error; polls while running. */
let doTaskTimer = null;
async function doTaskBox(box, onFinished) {
  clearTimeout(doTaskTimer);
  const t = await api("/git/personaldocs/task").catch(() => null);
  if (!box.isConnected || !t) return;
  const prev = box.dataset.now;
  box.dataset.now = t.state;
  if (prev === "running" && t.state !== "running") { onFinished?.(); return; }   // re-draws the card (changed files)
  const when = (v) => (v ? ago(v) : "");
  if (t.state === "running") {
    box.innerHTML = `<div class="git-task-line running"><span class="fb-dot" aria-hidden="true"></span>✍ Do task: transcribing ${t.pages} page${t.pages === 1 ? "" : "s"}… <span class="meta">started ${esc(when(t.started_at))} · pull and push wait until it's done</span></div>`;
    doTaskTimer = setTimeout(() => doTaskBox(box, onFinished), 10000);
  } else if (t.state === "done") {
    // first real sentence of the report (skip headings like "## Report")
    const first = (t.report || "").split("\n").filter((l) => !/^\s*#/.test(l))
      .map((l) => l.replace(/^[*\-\s]+/, "").replace(/\*\*/g, "").trim()).find((l) => l.length > 12) || "Finished";
    box.innerHTML = `<div class="git-task-line">✍ Do task ${esc(when(t.finished))}: ${esc(first.slice(0, 140))}${t.left ? ` <span class="git-warn">· ${t.left} page${t.left === 1 ? "" : "s"} still waiting</span>` : ""}</div>
      ${t.denied?.length ? `<div class="meta git-warn">Blocked: ${esc(t.denied.slice(0, 3).join("; "))}</div>` : ""}
      ${t.report ? `<details class="git-files"><summary>Report</summary><div class="md git-report">${md(t.report)}</div></details>` : ""}`;
    renderMath(box);
  } else if (t.state === "error") {
    box.innerHTML = `<div class="git-task-line git-warn">✍ Do task failed ${esc(when(t.finished))}: ${esc((t.error || "").slice(0, 200))}</div>`;
  } else box.innerHTML = "";
}
