"""Money: accounts, categories and transactions (yen, whole numbers).

Transaction types:
  expense   money out of `account_id` (with a category)
  income    money into `account_id` (with a category)
  transfer  `account_id` -> `to_account_id` (e.g. PayPay charge, moving money to Savings)
  adjust    sets a starting balance / corrects a balance to what the bank app shows; `amount`
            is the signed difference. Adjustments never count as spending or income.

"Saved" in a month = transfers into accounts of kind "savings" minus transfers out of them.
Entries can be posted from an iPhone Shortcut: account and category may be given by name.
"""
import re
from datetime import date, datetime, timedelta
from datetime import date as Day

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

import config
from tasks import get_conn, now_iso

TYPES = ("expense", "income", "transfer", "adjust")
ACCOUNT_KINDS = ("wallet", "bank", "cash", "savings", "other")
DEFAULT_ACCOUNTS = [tuple(a) for a in config.get("money_accounts")]   # first-run accounts (config.json)
DEFAULT_CATEGORIES = [
    ("Food", "🍙", "expense"), ("Eating out", "🍜", "expense"), ("Transport", "🚃", "expense"),
    ("School", "📚", "expense"), ("Daily goods", "🧴", "expense"), ("Hobby", "🎮", "expense"),
    ("Clothes", "👕", "expense"), ("Subscriptions", "📱", "expense"), ("Health", "🏥", "expense"),
    ("Gifts", "🎁", "expense"), ("Other", "📦", "expense"),
    ("Part-time job", "💼", "income"), ("Allowance", "👪", "income"), ("Scholarship", "🎓", "income"),
    ("Other income", "➕", "income"),
]


def seed(conn) -> None:
    """Default accounts and categories the first time Money is opened."""
    if not conn.execute("SELECT 1 FROM money_accounts LIMIT 1").fetchone():
        conn.executemany("INSERT INTO money_accounts (name, icon, kind, sort, created_at) VALUES (?, ?, ?, ?, ?)",
                         [(n, i, k, s, now_iso()) for s, (n, i, k) in enumerate(DEFAULT_ACCOUNTS)])
    if not conn.execute("SELECT 1 FROM money_categories LIMIT 1").fetchone():
        conn.executemany("INSERT INTO money_categories (name, icon, kind, sort) VALUES (?, ?, ?, ?)",
                         [(n, i, k, s) for s, (n, i, k) in enumerate(DEFAULT_CATEGORIES)])
    conn.commit()


def parse_amount(v) -> int:
    """1200, 1200.0, "1,200", "¥1,200", "１２００" -> 1200."""
    if isinstance(v, bool):
        raise ValueError("amount must be a number")
    if isinstance(v, (int, float)):
        n = v
    else:
        s = str(v).translate(str.maketrans("０１２３４５６７８９．－", "0123456789.-"))
        s = re.sub(r"[¥￥,\s円]", "", s)
        if not re.fullmatch(r"-?\d+(\.\d+)?", s):
            raise ValueError("amount must be a number like 1200")
        n = float(s)
    return int(round(n))


def balances(conn) -> dict[int, int]:
    b = {r["id"]: 0 for r in conn.execute("SELECT id FROM money_accounts")}
    for r in conn.execute("SELECT type, amount, account_id, to_account_id FROM money_tx"):
        if r["type"] == "expense":
            b[r["account_id"]] = b.get(r["account_id"], 0) - r["amount"]
        elif r["type"] in ("income", "adjust"):
            b[r["account_id"]] = b.get(r["account_id"], 0) + r["amount"]
        elif r["type"] == "transfer":
            b[r["account_id"]] = b.get(r["account_id"], 0) - r["amount"]
            b[r["to_account_id"]] = b.get(r["to_account_id"], 0) + r["amount"]
    return b


def accounts(conn, include_archived=True) -> list[dict]:
    b = balances(conn)
    rows = conn.execute("SELECT * FROM money_accounts ORDER BY archived, sort, id").fetchall()
    return [{**dict(r), "balance": b.get(r["id"], 0)} for r in rows if include_archived or not r["archived"]]


def categories(conn) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM money_categories ORDER BY kind DESC, archived, sort, id")]


def tx_rows(conn, where="1", args=(), limit=2000) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT t.*, a.name AS account, a.icon AS account_icon, b.name AS to_account, b.icon AS to_account_icon, "
        "c.name AS category, c.icon AS category_icon FROM money_tx t "
        "JOIN money_accounts a ON a.id = t.account_id LEFT JOIN money_accounts b ON b.id = t.to_account_id "
        f"LEFT JOIN money_categories c ON c.id = t.category_id WHERE {where} ORDER BY t.date DESC, t.id DESC LIMIT ?",
        (*args, limit))]


def month_bounds(month: str) -> tuple[str, str]:
    y, m = map(int, month.split("-"))
    start = date(y, m, 1)
    end = date(y + (m == 12), m % 12 + 1, 1)
    return start.isoformat(), end.isoformat()


def month_stats(conn, month: str) -> dict:
    lo, hi = month_bounds(month)
    rows = conn.execute(
        "SELECT t.type, t.amount, t.date, t.category_id, a.kind AS from_kind, b.kind AS to_kind "
        "FROM money_tx t JOIN money_accounts a ON a.id = t.account_id LEFT JOIN money_accounts b ON b.id = t.to_account_id "
        "WHERE t.date >= ? AND t.date < ?", (lo, hi)).fetchall()
    spent = sum(r["amount"] for r in rows if r["type"] == "expense")
    earned = sum(r["amount"] for r in rows if r["type"] == "income")
    saved = sum(r["amount"] * ((r["to_kind"] == "savings") - (r["from_kind"] == "savings"))
                for r in rows if r["type"] == "transfer")
    cats = {c["id"]: c for c in categories(conn)}
    by_cat: dict = {}
    for r in rows:
        if r["type"] in ("expense", "income"):
            key = (r["type"], r["category_id"])
            by_cat[key] = by_cat.get(key, 0) + r["amount"]
    def cat_list(kind):
        out = [{"id": cid, "name": cats[cid]["name"] if cid in cats else "Uncategorized",
                "icon": cats[cid]["icon"] if cid in cats else "·", "amount": v}
               for (k, cid), v in by_cat.items() if k == kind]
        return sorted(out, key=lambda x: -x["amount"])
    per_day: dict = {}
    for r in rows:
        if r["type"] == "expense":
            per_day[r["date"]] = per_day.get(r["date"], 0) + r["amount"]
    d, end = date.fromisoformat(lo), date.fromisoformat(hi)
    days = []
    while d < end:
        days.append({"date": d.isoformat(), "spent": per_day.get(d.isoformat(), 0)})
        d += timedelta(days=1)
    return {"month": month, "spent": spent, "earned": earned, "saved": saved, "net": earned - spent,
            "by_category": cat_list("expense"), "income_by_category": cat_list("income"), "per_day": days,
            "count": sum(1 for r in rows if r["type"] != "adjust")}


def shift_month(month: str, n: int) -> str:
    y, m = map(int, month.split("-"))
    i = y * 12 + (m - 1) + n
    return f"{i // 12:04d}-{i % 12 + 1:02d}"


def get_budget(conn) -> int | None:
    r = conn.execute("SELECT value FROM state WHERE key = 'money_budget'").fetchone()
    return int(r["value"]) if r and r["value"] else None


def summary(conn, months: int = 3) -> dict:
    """For Claude (MCP, read-only connection): balances, month totals with categories, and the transactions."""
    this = date.today().strftime("%Y-%m")
    ms = [shift_month(this, -i) for i in range(months)]
    lo = month_bounds(ms[-1])[0]
    stats = []
    for m in ms:
        s = month_stats(conn, m)
        s.pop("per_day")
        stats.append(s)
    return {"currency": "JPY", "today": date.today().isoformat(), "monthly_budget": get_budget(conn),
            "accounts": [{k: a[k] for k in ("name", "kind", "balance", "archived")} for a in accounts(conn)],
            "net_worth": sum(a["balance"] for a in accounts(conn) if not a["archived"]),
            "months": stats,
            "transactions": [{k: t[k] for k in ("date", "type", "amount", "account", "to_account", "category", "note")}
                             for t in tx_rows(conn, "t.date >= ?", (lo,), limit=1000)],
            "notes": "Amounts in yen. 'adjust' rows set starting balances / corrections and are not spending. "
                     "'saved' = transfers into savings accounts minus transfers out of them."}


# ---------------------------------------------------------------- API

def find_account(conn, v) -> int:
    if v is None or v == "":
        raise HTTPException(400, "Pick an account")
    if isinstance(v, int) or (isinstance(v, str) and v.isdigit()):
        r = conn.execute("SELECT id FROM money_accounts WHERE id = ?", (int(v),)).fetchone()
    else:
        r = conn.execute("SELECT id FROM money_accounts WHERE lower(name) = lower(?) OR lower(icon || ' ' || name) = lower(?)",
                         (str(v).strip(), str(v).strip())).fetchone()
    if not r:
        raise HTTPException(400, f"Unknown account: {v}")
    return r["id"]


def find_category(conn, v, kind: str) -> int | None:
    if v is None or v == "":
        return None
    if isinstance(v, int) or (isinstance(v, str) and v.isdigit()):
        r = conn.execute("SELECT id, kind FROM money_categories WHERE id = ?", (int(v),)).fetchone()
    else:
        r = conn.execute("SELECT id, kind FROM money_categories WHERE lower(name) = lower(?) ORDER BY kind = ? DESC",
                         (str(v).strip(), kind)).fetchone()
    if not r:
        raise HTTPException(400, f"Unknown category: {v}")
    if r["kind"] != kind:
        raise HTTPException(400, f"“{v}” is an {r['kind']} category")
    return r["id"]


class TxIn(BaseModel):
    type: str = "expense"
    amount: int
    account: int | str
    to_account: int | str | None = None
    category: int | str | None = None
    note: str = Field("", max_length=500)
    date: Day | None = None

    @field_validator("amount", mode="before")
    @classmethod
    def _amount(cls, v):
        return parse_amount(v)


class TxPatch(BaseModel):
    type: str | None = None
    amount: int | None = None
    account: int | str | None = None
    to_account: int | str | None = None
    category: int | str | None = None
    note: str | None = Field(None, max_length=500)
    date: Day | None = None

    @field_validator("amount", mode="before")
    @classmethod
    def _amount(cls, v):
        return None if v is None else parse_amount(v)


class AccountIn(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    icon: str = Field("💳", max_length=8)
    kind: str = "bank"


class AccountPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=40)
    icon: str | None = Field(None, max_length=8)
    kind: str | None = None
    archived: bool | None = None
    sort: int | None = None


class BalanceIn(BaseModel):
    balance: int
    date: Day | None = None

    @field_validator("balance", mode="before")
    @classmethod
    def _amount(cls, v):
        return parse_amount(v)


class CategoryIn(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    icon: str = Field("🏷", max_length=8)
    kind: str = "expense"


class CategoryPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=40)
    icon: str | None = Field(None, max_length=8)
    archived: bool | None = None


class BudgetIn(BaseModel):
    amount: int | None = None

    @field_validator("amount", mode="before")
    @classmethod
    def _amount(cls, v):
        return None if v in (None, "") else parse_amount(v)


def check_tx(conn, t: dict) -> dict:
    """Validate a full transaction dict (type, amount, account, to_account, category) -> row values."""
    if t["type"] not in TYPES:
        raise HTTPException(400, f"type must be one of {', '.join(TYPES)}")
    if t["type"] != "adjust" and t["amount"] <= 0:
        raise HTTPException(400, "Amount must be more than 0")
    if abs(t["amount"]) > 100_000_000:
        raise HTTPException(400, "Amount is too large")
    acc = find_account(conn, t["account"])
    to = None
    if t["type"] == "transfer":
        to = find_account(conn, t["to_account"])
        if to == acc:
            raise HTTPException(400, "Pick two different accounts for a transfer")
    cat = find_category(conn, t["category"], t["type"]) if t["type"] in ("expense", "income") else None
    return {"type": t["type"], "amount": t["amount"], "account_id": acc, "to_account_id": to, "category_id": cat}


def build_router(check_token) -> APIRouter:
    r = APIRouter(prefix="/money", dependencies=[Depends(check_token)])

    @r.get("")
    def overview(month: str | None = None, conn=Depends(get_conn)):
        seed(conn)
        month = month or date.today().strftime("%Y-%m")
        if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
            raise HTTPException(400, "month must be YYYY-MM")
        lo, hi = month_bounds(month)
        accs = accounts(conn)
        stats = month_stats(conn, month)
        trend = []
        for i in range(5, -1, -1):
            s = month_stats(conn, shift_month(month, -i))
            trend.append({k: s[k] for k in ("month", "spent", "earned", "saved")})
        budget = get_budget(conn)
        today = date.today()
        days_left = (date.fromisoformat(hi) - today).days if lo <= today.isoformat() < hi else None
        return {"month": month, "today": today.isoformat(), "accounts": accs, "categories": categories(conn),
                "net_worth": sum(a["balance"] for a in accs if not a["archived"]),
                "stats": stats, "trend": trend, "budget": budget,
                "budget_left": budget - stats["spent"] if budget else None, "days_left": days_left,
                "tx": tx_rows(conn, "t.date >= ? AND t.date < ?", (lo, hi)),
                "first_month": (conn.execute("SELECT MIN(date) d FROM money_tx").fetchone()["d"] or today.isoformat())[:7]}

    @r.post("/tx")
    def add_tx(body: TxIn, conn=Depends(get_conn)):
        seed(conn)
        row = check_tx(conn, body.model_dump())
        d = (body.date or date.today()).isoformat()
        cur = conn.execute("INSERT INTO money_tx (date, type, amount, account_id, to_account_id, category_id, note, created_at) "
                           "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                           (d, row["type"], row["amount"], row["account_id"], row["to_account_id"], row["category_id"],
                            body.note.strip(), now_iso()))
        conn.commit()
        return tx_rows(conn, "t.id = ?", (cur.lastrowid,))[0]

    @r.patch("/tx/{tx_id}")
    def edit_tx(tx_id: int, body: TxPatch, conn=Depends(get_conn)):
        old = conn.execute("SELECT t.*, c.id AS cat FROM money_tx t LEFT JOIN money_categories c ON c.id = t.category_id "
                           "WHERE t.id = ?", (tx_id,)).fetchone()
        if not old:
            raise HTTPException(404, "No such entry")
        ch = body.model_dump(exclude_unset=True)
        t = {"type": ch.get("type", old["type"]), "amount": ch.get("amount", old["amount"]),
             "account": ch.get("account", old["account_id"]), "to_account": ch.get("to_account", old["to_account_id"]),
             "category": ch.get("category", old["category_id"] if ch.get("type", old["type"]) == old["type"] else None)}
        row = check_tx(conn, t)
        conn.execute("UPDATE money_tx SET date = ?, type = ?, amount = ?, account_id = ?, to_account_id = ?, category_id = ?, "
                     "note = ? WHERE id = ?",
                     ((ch.get("date") or date.fromisoformat(old["date"])).isoformat(), row["type"], row["amount"],
                      row["account_id"], row["to_account_id"], row["category_id"],
                      (ch["note"] if ch.get("note") is not None else old["note"]).strip(), tx_id))
        conn.commit()
        return tx_rows(conn, "t.id = ?", (tx_id,))[0]

    @r.delete("/tx/{tx_id}")
    def delete_tx(tx_id: int, conn=Depends(get_conn)):
        if not conn.execute("DELETE FROM money_tx WHERE id = ?", (tx_id,)).rowcount:
            raise HTTPException(404, "No such entry")
        conn.commit()
        return {"deleted": tx_id}

    @r.get("/tx")
    def search_tx(q: str = "", account: int | None = None, category: int | None = None, limit: int = 200,
                  conn=Depends(get_conn)):
        where, args = ["1"], []
        if q:
            where.append("(t.note LIKE ? OR c.name LIKE ?)"); args += [f"%{q}%", f"%{q}%"]
        if account:
            where.append("(t.account_id = ? OR t.to_account_id = ?)"); args += [account, account]
        if category:
            where.append("t.category_id = ?"); args.append(category)
        return tx_rows(conn, " AND ".join(where), args, limit=max(1, min(limit, 2000)))

    # ---- accounts
    @r.post("/accounts")
    def add_account(body: AccountIn, conn=Depends(get_conn)):
        if body.kind not in ACCOUNT_KINDS:
            raise HTTPException(400, "Unknown account kind")
        if conn.execute("SELECT 1 FROM money_accounts WHERE lower(name) = lower(?)", (body.name.strip(),)).fetchone():
            raise HTTPException(409, "An account with that name exists")
        n = conn.execute("SELECT COALESCE(MAX(sort), -1) + 1 FROM money_accounts").fetchone()[0]
        cur = conn.execute("INSERT INTO money_accounts (name, icon, kind, sort, created_at) VALUES (?, ?, ?, ?, ?)",
                           (body.name.strip(), body.icon, body.kind, n, now_iso()))
        conn.commit()
        return {"id": cur.lastrowid}

    @r.patch("/accounts/{acc_id}")
    def edit_account(acc_id: int, body: AccountPatch, conn=Depends(get_conn)):
        if not conn.execute("SELECT 1 FROM money_accounts WHERE id = ?", (acc_id,)).fetchone():
            raise HTTPException(404, "No such account")
        ch = body.model_dump(exclude_unset=True)
        if "kind" in ch and ch["kind"] not in ACCOUNT_KINDS:
            raise HTTPException(400, "Unknown account kind")
        for k, v in ch.items():
            if v is not None:
                conn.execute(f"UPDATE money_accounts SET {k} = ? WHERE id = ?", (int(v) if k == "archived" else v, acc_id))
        conn.commit()
        return {"id": acc_id}

    @r.post("/accounts/{acc_id}/balance")
    def set_balance(acc_id: int, body: BalanceIn, conn=Depends(get_conn)):
        """Make the balance match what the bank / app shows, by adding an adjustment."""
        if not conn.execute("SELECT 1 FROM money_accounts WHERE id = ?", (acc_id,)).fetchone():
            raise HTTPException(404, "No such account")
        diff = body.balance - balances(conn).get(acc_id, 0)
        if diff:
            first = not conn.execute("SELECT 1 FROM money_tx WHERE account_id = ? OR to_account_id = ?", (acc_id, acc_id)).fetchone()
            conn.execute("INSERT INTO money_tx (date, type, amount, account_id, note, created_at) VALUES (?, 'adjust', ?, ?, ?, ?)",
                         ((body.date or date.today()).isoformat(), diff, acc_id,
                          "Starting balance" if first else "Balance corrected", now_iso()))
            conn.commit()
        return {"id": acc_id, "balance": body.balance, "adjusted_by": diff}

    # ---- categories
    @r.post("/categories")
    def add_category(body: CategoryIn, conn=Depends(get_conn)):
        if body.kind not in ("expense", "income"):
            raise HTTPException(400, "kind must be expense or income")
        if conn.execute("SELECT 1 FROM money_categories WHERE lower(name) = lower(?) AND kind = ?",
                        (body.name.strip(), body.kind)).fetchone():
            raise HTTPException(409, "That category exists")
        n = conn.execute("SELECT COALESCE(MAX(sort), -1) + 1 FROM money_categories").fetchone()[0]
        cur = conn.execute("INSERT INTO money_categories (name, icon, kind, sort) VALUES (?, ?, ?, ?)",
                           (body.name.strip(), body.icon, body.kind, n))
        conn.commit()
        return {"id": cur.lastrowid}

    @r.patch("/categories/{cat_id}")
    def edit_category(cat_id: int, body: CategoryPatch, conn=Depends(get_conn)):
        if not conn.execute("SELECT 1 FROM money_categories WHERE id = ?", (cat_id,)).fetchone():
            raise HTTPException(404, "No such category")
        for k, v in body.model_dump(exclude_unset=True).items():
            if v is not None:
                conn.execute(f"UPDATE money_categories SET {k} = ? WHERE id = ?", (int(v) if k == "archived" else v, cat_id))
        conn.commit()
        return {"id": cat_id}

    @r.put("/budget")
    def set_budget(body: BudgetIn, conn=Depends(get_conn)):
        conn.execute("INSERT INTO state (key, value) VALUES ('money_budget', ?) "
                     "ON CONFLICT (key) DO UPDATE SET value = excluded.value", (str(body.amount or ""),))
        conn.commit()
        return {"budget": body.amount}

    return r
