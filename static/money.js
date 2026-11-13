// Money: quick entry, balances per account, spending per month / category / day.
// Loaded before app.js; uses its helpers ($, esc, api, send, fmt, card, tile, columnChart,
// barList, tableView, openModal, dayLabel, dayLong, todayISO, store) and toast (tasks.js) at call time.
"use strict";

const yen = (n) => (n < 0 ? "−¥" : "¥") + Math.abs(Math.round(n || 0)).toLocaleString();
const M_TYPES = [["expense", "Expense"], ["income", "Income"], ["transfer", "Transfer"]];
const M_KINDS = { wallet: "Wallet", bank: "Bank", cash: "Cash", savings: "Savings", other: "Other" };
const monthLabel = (m) => { const [y, mm] = m.split("-").map(Number); return `${MON[mm - 1]} ${y}`; };
const monthShift = (m, n) => { const [y, mm] = m.split("-").map(Number); const i = y * 12 + mm - 1 + n; return `${Math.floor(i / 12)}-${String(i % 12 + 1).padStart(2, "0")}`; };

/** The entry form (new or edit). preset = an existing tx for editing. */
function moneyForm(host, data, { preset = null, initial = null, onSaved } = {}) {
  const last = store.get("money_last", {});
  let type = preset?.type || initial?.type || last.type || "expense";
  if (type === "adjust") type = "expense";
  const accs = data.accounts.filter((a) => !a.archived || a.id === preset?.account_id || a.id === preset?.to_account_id);
  const draw = () => {
    const cats = data.categories.filter((c) => c.kind === type && (!c.archived || c.id === preset?.category_id));
    const cat = preset && preset.type === type ? preset.category_id : initial?.category_id && initial.type === type ? initial.category_id : last[`cat_${type}`];
    const acc = preset?.account_id || last.account || accs[0]?.id;
    const to = preset?.to_account_id || last.to || accs.find((a) => a.kind === "savings")?.id;
    host.innerHTML = `<form class="m-form" autocomplete="off">
      <div class="seg m-type" role="group" aria-label="Type">${M_TYPES.map(([k, l]) => `<button type="button" data-type="${k}" aria-pressed="${k === type}">${l}</button>`).join("")}</div>
      <label class="m-amount-wrap"><span>¥</span><input name="amount" class="m-amount" inputmode="numeric" pattern="[0-9,]*" placeholder="0" required aria-label="Amount in yen" value="${preset ? preset.amount : initial?.amount || ""}"></label>
      ${type === "transfer" ? "" : `<div class="m-cats" role="radiogroup" aria-label="Category">${cats.map((c) => `<label class="m-chip"><input type="radio" name="category" value="${c.id}"${c.id === cat ? " checked" : ""}><span>${esc(c.icon)} ${esc(c.name)}</span></label>`).join("")}</div>`}
      <div class="row-form">
        <label class="field">${type === "transfer" ? "From" : type === "income" ? "Into" : "Paid with"} <select name="account">${accs.map((a) => `<option value="${a.id}"${a.id === acc ? " selected" : ""}>${esc(a.icon)} ${esc(a.name)}</option>`).join("")}</select></label>
        ${type === "transfer" ? `<label class="field">To <select name="to">${accs.map((a) => `<option value="${a.id}"${a.id === to ? " selected" : ""}>${esc(a.icon)} ${esc(a.name)}</option>`).join("")}</select></label>` : ""}
        <label class="field">Date <input type="date" name="date" value="${preset?.date || todayISO()}" max="${todayISO()}"></label>
      </div>
      <input name="note" maxlength="500" placeholder="Note (optional), e.g. lunch at school, train to Toyama" value="${esc(preset?.note || initial?.note || "")}">
      <div class="composer-row">${preset ? `<button type="button" class="btn danger-link" data-del>Delete</button>` : ""}<span class="form-msg" style="flex:1"></span>
        <button class="btn primary">${preset ? "Save" : "Add"}</button></div>
    </form>`;
    const f = $("form", host);
    f.querySelectorAll("[data-type]").forEach((b) => b.onclick = () => { type = b.dataset.type; const a = f.elements.amount.value, n = f.elements.note.value; draw(); $("form", host).elements.amount.value = a; $("form", host).elements.note.value = n; });
    f.onsubmit = async (e) => {
      e.preventDefault();
      const body = { type, amount: f.elements.amount.value, account: +f.elements.account.value, date: f.elements.date.value || null,
        note: f.elements.note.value, category: type === "transfer" ? null : +(f.querySelector('[name="category"]:checked')?.value || 0) || null,
        to_account: type === "transfer" ? +f.elements.to.value : null };
      try {
        const t = await send(preset ? `/money/tx/${preset.id}` : "/money/tx", preset ? "PATCH" : "POST", body);
        store.set("money_last", { ...store.get("money_last", {}), type, account: body.account, ...(type === "transfer" ? { to: body.to_account } : { [`cat_${type}`]: body.category }) });
        onSaved?.(t);
      } catch (err) { $(".form-msg", f).textContent = err.message; }
    };
    f.querySelector("[data-del]")?.addEventListener("click", async () => {
      if (!confirm("Delete this entry?")) return;
      await send(`/money/tx/${preset.id}`, "DELETE"); onSaved?.(null);
    });
  };
  draw();
}

function txLine(t) {
  const sign = t.type === "expense" ? "−" : t.type === "income" ? "+" : t.type === "adjust" ? (t.amount < 0 ? "−" : "+") : "";
  const what = t.type === "transfer" ? `${esc(t.account)} → ${esc(t.to_account)}`
    : t.type === "adjust" ? esc(t.note || "Balance set") : `${esc(t.category || "Uncategorized")}`;
  const icon = t.type === "transfer" ? "⇄" : t.type === "adjust" ? "⚖" : esc(t.category_icon || "·");
  return `<li class="m-tx m-${t.type}" data-tx="${t.id}" tabindex="0" role="button" aria-label="Edit entry">
    <span class="m-ico" aria-hidden="true">${icon}</span>
    <div class="main"><div class="title">${what}</div>
      <div class="meta">${t.type === "transfer" || t.type === "adjust" ? esc(t.type === "adjust" ? t.account : "Transfer") : `${esc(t.account_icon)} ${esc(t.account)}`}${t.note && t.type !== "adjust" ? ` · ${esc(t.note)}` : ""}</div></div>
    <span class="m-amt">${sign}${yen(Math.abs(t.amount)).replace("−", "")}</span></li>`;
}

async function viewMoney(main, arg) {
  const month = /^\d{4}-\d{2}$/.test(arg || "") ? arg : todayISO().slice(0, 7);
  const d = await api(`/money?month=${month}`);
  const s = d.stats, isNow = month === d.today.slice(0, 7);
  const go = (m) => { location.hash = m === d.today.slice(0, 7) ? "#/money" : `#/money/${m}`; };

  const budgetCtx = d.budget ? (d.budget_left >= 0 ? `${yen(d.budget_left)} left of ${yen(d.budget)}${isNow && d.days_left ? ` · ${yen(d.budget_left / d.days_left)}/day` : ""}` : `${yen(-d.budget_left)} over the ${yen(d.budget)} budget`) : "no budget set";
  main.innerHTML = `<div class="top"><h1>Money</h1>
      <div class="m-month"><button class="icon-btn" data-m="-1" aria-label="Previous month">‹</button><strong>${esc(monthLabel(month))}</strong>
        <button class="icon-btn" data-m="1" aria-label="Next month"${isNow ? " disabled" : ""}>›</button></div>
      <span class="spacer"></span><button class="icon-btn" id="m-cats">Categories</button><button class="icon-btn" id="m-budget">Budget</button></div>
    <div class="grid">
      ${tile("Spent", yen(s.spent), budgetCtx)}
      ${tile("Earned", yen(s.earned), `net ${yen(s.net)}`)}
      ${tile("Saved", yen(s.saved), "moved into savings")}
      ${tile("All accounts", yen(d.net_worth), `${d.accounts.filter((a) => !a.archived).length} accounts`)}
      <section class="card span-4 m-add"><h2>Add</h2><div id="m-form"></div></section>
      <section class="card span-8"><h2>Spending per day</h2><p class="desc">${esc(monthLabel(month))}</p><div id="m-days"></div>
        ${tableView(s.per_day.filter((x) => x.spent).map((x) => [x.date, yen(x.spent)]), ["Day", "Spent"])}</section>
      <section class="card span-4"><div class="list-head"><h2>Accounts</h2><button class="btn small" id="m-add-acc">+ Account</button></div>
        <ul class="list compact m-accs">${d.accounts.filter((a) => !a.archived).map((a) => `<li data-acc="${a.id}" tabindex="0" role="button"><span class="m-ico">${esc(a.icon)}</span>
          <div class="main"><div class="title">${esc(a.name)}</div><div class="meta">${esc(M_KINDS[a.kind] || a.kind)}</div></div>
          <span class="m-bal${a.balance < 0 ? " neg" : ""}">${yen(a.balance)}</span></li>`).join("")}</ul>
        ${d.accounts.some((a) => a.archived) ? `<details class="m-arch"><summary>Archived</summary><ul class="list compact m-accs">${d.accounts.filter((a) => a.archived).map((a) => `<li data-acc="${a.id}" tabindex="0" role="button"><span class="m-ico">${esc(a.icon)}</span><div class="main"><div class="title">${esc(a.name)}</div></div><span class="m-bal">${yen(a.balance)}</span></li>`).join("")}</ul></details>` : ""}
        <p class="desc">Tap an account to set its real balance or rename it.</p></section>
      <section class="card span-4"><h2>Where it went</h2><p class="desc">Spending by category</p>
        ${barList(s.by_category.map((c) => ({ name: `${c.icon} ${c.name}`, value: c.amount })), yen)}
        ${s.income_by_category.length ? `<div class="subhead">Income</div>${barList(s.income_by_category.map((c) => ({ name: `${c.icon} ${c.name}`, value: c.amount })), yen)}` : ""}</section>
      <section class="card span-4"><h2>Last 6 months</h2><p class="desc">Spent per month</p><div id="m-trend"></div>
        ${tableView(d.trend.map((t) => [monthLabel(t.month), yen(t.spent), yen(t.earned), yen(t.saved)]).reverse(), ["Month", "Spent", "Earned", "Saved"])}</section>
      <section class="card span-12"><div class="list-head"><h2>Entries</h2><span class="count">${s.count}</span></div><div id="m-list"></div></section>
    </div>`;

  main.querySelectorAll("[data-m]").forEach((b) => b.onclick = () => go(monthShift(month, +b.dataset.m)));
  const reload = () => viewMoney(main, arg);
  moneyForm($("#m-form", main), d, { onSaved: (t) => { toast(`Added ${yen(t.amount)}${t.category ? ` · ${esc(t.category)}` : ""}`); if (t.date.slice(0, 7) !== month) go(t.date.slice(0, 7)); else reload(); } });
  columnChart($("#m-days", main), s.per_day.map((x) => ({ key: x.date, label: String(+x.date.slice(8)), tip: dayLong(x.date), y: x.spent || null })),
    { valueFmt: yen, axisFmt: (v) => `¥${fmt.compact(v)}`, emptyText: "No spending this month yet" });
  columnChart($("#m-trend", main), d.trend.map((t) => ({ key: t.month, label: MON[+t.month.slice(5) - 1], tip: monthLabel(t.month), y: t.spent || null })),
    { valueFmt: yen, axisFmt: (v) => `¥${fmt.compact(v)}`, emptyText: "No spending yet" });

  // entries grouped by day
  const byDay = {};
  d.tx.forEach((t) => (byDay[t.date] ||= []).push(t));
  $("#m-list", main).innerHTML = d.tx.length ? Object.entries(byDay).map(([day, list]) => {
    const out = list.filter((t) => t.type === "expense").reduce((a, t) => a + t.amount, 0);
    return `<div class="m-day"><div class="m-day-head"><span>${esc(relDay(day) === "today" || relDay(day) === "yesterday" ? `${relDay(day)[0].toUpperCase()}${relDay(day).slice(1)}` : dayLong(day))}</span>${out ? `<span>−${yen(out).slice(0)}</span>` : ""}</div>
      <ul class="list compact">${list.map(txLine).join("")}</ul></div>`;
  }).join("") : `<div class="empty">No entries in ${esc(monthLabel(month))}.</div>`;
  const editTx = (id) => {
    const t = d.tx.find((x) => x.id === id);
    const m = openModal(t.type === "adjust" ? "Balance adjustment" : "Edit entry");
    if (t.type === "adjust") {
      m.body.innerHTML = `<p>${esc(t.account)}: ${t.amount < 0 ? "−" : "+"}${yen(Math.abs(t.amount))} on ${esc(dayLong(t.date))} (${esc(t.note)}). Adjustments make a balance match the real one; they don't count as spending.</p>
        <div class="composer-row"><span style="flex:1"></span><button class="btn danger-link" data-del-adj>Delete adjustment</button></div>`;
      m.body.querySelector("[data-del-adj]").onclick = async () => { await send(`/money/tx/${t.id}`, "DELETE"); m.close(); reload(); };
      return;
    }
    moneyForm(m.body, d, { preset: t, onSaved: () => { m.close(); reload(); } });
  };
  main.querySelectorAll("[data-tx]").forEach((li) => {
    li.onclick = () => editTx(+li.dataset.tx);
    li.onkeydown = (e) => { if (e.key === "Enter") editTx(+li.dataset.tx); };
  });

  // accounts
  const editAcc = (id) => {
    const a = d.accounts.find((x) => x.id === id);
    const m = openModal(a ? a.name : "New account");
    m.body.innerHTML = `<form class="m-form" id="acc-f">
      <div class="row-form"><label class="field">Icon <input name="icon" maxlength="8" value="${esc(a?.icon || "💳")}" style="width:60px"></label>
        <label class="field" style="flex:1">Name <input name="name" required maxlength="40" value="${esc(a?.name || "")}"></label>
        <label class="field">Kind <select name="kind">${Object.entries(M_KINDS).map(([k, v]) => `<option value="${k}"${k === (a?.kind || "bank") ? " selected" : ""}>${v}</option>`).join("")}</select></label></div>
      <label class="field">Balance right now (¥) <input name="balance" inputmode="numeric" value="${a ? a.balance : ""}" placeholder="what the bank / app shows"></label>
      <p class="desc">If it differs from the dashboard, an adjustment is added so the numbers match. Adjustments don't count as spending.</p>
      ${a ? `<label class="check"><input type="checkbox" name="archived"${a.archived ? " checked" : ""}> Archive (hide; keeps its history)</label>` : ""}
      <div class="composer-row"><span class="form-msg" style="flex:1"></span><button class="btn primary">Save</button></div></form>`;
    const f = $("#acc-f", m.body);
    setTimeout(() => (a ? f.elements.balance : f.elements.name).focus());
    f.onsubmit = async (e) => {
      e.preventDefault();
      try {
        let accId = a?.id;
        const body = { name: f.elements.name.value, icon: f.elements.icon.value, kind: f.elements.kind.value };
        if (a) await send(`/money/accounts/${a.id}`, "PATCH", { ...body, archived: f.elements.archived.checked });
        else accId = (await send("/money/accounts", "POST", body)).id;
        const bal = f.elements.balance.value.trim();
        if (bal !== "" && (!a || bal.replace(/[,¥]/g, "") !== String(a.balance))) await send(`/money/accounts/${accId}/balance`, "POST", { balance: bal });
        m.close(); reload();
      } catch (err) { $(".form-msg", f).textContent = err.message; }
    };
  };
  main.querySelectorAll("[data-acc]").forEach((li) => {
    li.onclick = () => editAcc(+li.dataset.acc);
    li.onkeydown = (e) => { if (e.key === "Enter") editAcc(+li.dataset.acc); };
  });
  $("#m-add-acc", main).onclick = () => editAcc(null);

  $("#m-budget", main).onclick = async () => {
    const v = prompt("Monthly spending budget in yen (empty = none):", d.budget ?? "");
    if (v == null) return;
    try { await send("/money/budget", "PUT", { amount: v.trim() || null }); reload(); } catch (e) { alert(e.message); }
  };
  $("#m-cats", main).onclick = () => {
    const m = openModal("Categories");
    const draw = () => {
      m.body.innerHTML = ["expense", "income"].map((k) => `<div class="subhead">${k === "expense" ? "Spending" : "Income"}</div>
        <ul class="list compact">${d.categories.filter((c) => c.kind === k).map((c) => `<li><span class="m-ico">${esc(c.icon)}</span><div class="main"><div class="title">${esc(c.name)}${c.archived ? ` <span class="pill">hidden</span>` : ""}</div></div>
          <button class="btn small" data-hide="${c.id}">${c.archived ? "Show" : "Hide"}</button></li>`).join("")}</ul>`).join("")
        + `<form class="m-form" id="cat-f"><div class="subhead">New category</div><div class="row-form">
          <label class="field">Icon <input name="icon" maxlength="8" value="🏷" style="width:60px"></label>
          <label class="field" style="flex:1">Name <input name="name" required maxlength="40"></label>
          <label class="field">For <select name="kind"><option value="expense">Spending</option><option value="income">Income</option></select></label></div>
          <div class="composer-row"><span class="form-msg" style="flex:1"></span><button class="btn primary">Add</button></div></form>`;
      m.body.querySelectorAll("[data-hide]").forEach((b) => b.onclick = async () => {
        const c = d.categories.find((x) => x.id === +b.dataset.hide);
        await send(`/money/categories/${c.id}`, "PATCH", { archived: !c.archived }); c.archived = c.archived ? 0 : 1; draw();
      });
      const f = $("#cat-f", m.body);
      f.onsubmit = async (e) => {
        e.preventDefault();
        try {
          const body = { name: f.elements.name.value, icon: f.elements.icon.value, kind: f.elements.kind.value };
          const r = await send("/money/categories", "POST", body);
          d.categories.push({ id: r.id, ...body, archived: 0 }); draw();
        } catch (err) { $(".form-msg", f).textContent = err.message; }
      };
    };
    draw();
    const obs = new MutationObserver(() => { if (!m.el.isConnected) { obs.disconnect(); reload(); } });
    obs.observe(document.body, { childList: true });
  };
}
