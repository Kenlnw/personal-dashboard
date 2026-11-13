"""Studio: one-click study material for a workspace (study guide, briefing, glossary,
flashcards, quiz, study plan).

Each generation is a background headless Claude Code run in the workspace folder that may
only READ (Read/Glob/Grep). Claude returns its result through `--json-schema` structured
output, and this server writes the file into outputs/:
    *.md               study guide / briefing / glossary
    *.flashcards.json  {"title", "cards": [{"front", "back", "source"}]}
    *.quiz.json        {"title", "questions": [{"question", "choices", "answer", "explanation", "source"}]}
    *.plan.json        {"title", "overview", "tasks": [{"title", "description", "priority", "due", "labels",
                        "source", "subtasks": [...]}]}  suggested tasks; the user approves them into Tasks
The dashboard renders these with its own viewers (flip cards, quiz with scoring).
"""
import json
import re
import subprocess
import threading
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

import db
import tasks
from workspaces import (CLAUDE, MODELS, NO_WINDOW, ensure_layout, folders_context, get_conn, get_ws, linked_folders, now_iso,
                        ws_dir, ws_model)

READ_ONLY_ARGS = ["--permission-mode", "dontAsk", "--allowedTools", "Read,Glob,Grep",
                  "--disallowedTools", "Bash,PowerShell,Write,Edit,NotebookEdit,WebFetch,WebSearch",
                  "--permission-prompts", "none"]
TIMEOUT = 20 * 60
RUNNING: set[int] = set()     # job ids this server process is running right now

DOC_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["title", "markdown"],
    "properties": {"title": {"type": "string"}, "markdown": {"type": "string"}},
}
CARDS_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["title", "cards"],
    "properties": {
        "title": {"type": "string"},
        "cards": {"type": "array", "minItems": 1, "items": {
            "type": "object", "additionalProperties": False, "required": ["front", "back", "source"],
            "properties": {"front": {"type": "string"}, "back": {"type": "string"}, "source": {"type": "string"}}}},
    },
}
QUIZ_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["title", "questions"],
    "properties": {
        "title": {"type": "string"},
        "questions": {"type": "array", "minItems": 1, "items": {
            "type": "object", "additionalProperties": False,
            "required": ["question", "choices", "answer", "explanation", "source"],
            "properties": {
                "question": {"type": "string"},
                "choices": {"type": "array", "minItems": 2, "maxItems": 6, "items": {"type": "string"}},
                "answer": {"type": "integer", "minimum": 0},
                "explanation": {"type": "string"},
                "source": {"type": "string"}}}},
    },
}

SUBTASK = {
    "type": "object", "additionalProperties": False, "required": ["title", "description", "due"],
    "properties": {"title": {"type": "string"}, "description": {"type": "string"},
                   "due": {"type": "string", "description": "YYYY-MM-DD, or empty for none"}},
}
PLAN_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["title", "overview", "tasks"],
    "properties": {
        "title": {"type": "string"},
        "overview": {"type": "string", "description": "Markdown: the approach and schedule in a few lines"},
        "tasks": {"type": "array", "minItems": 1, "maxItems": 20, "items": {
            "type": "object", "additionalProperties": False,
            "required": ["title", "description", "priority", "due", "labels", "source", "subtasks"],
            "properties": {
                "title": {"type": "string"},
                "description": {"type": "string"},
                "priority": {"type": "integer", "minimum": 0, "maximum": 4},
                "due": {"type": "string", "description": "YYYY-MM-DD, or empty for none"},
                "labels": {"type": "array", "maxItems": 3, "items": {"type": "string"}},
                "source": {"type": "string"},
                "subtasks": {"type": "array", "maxItems": 8, "items": SUBTASK}}}},
    },
}

COMMON = (
    "Read the workspace's sources in sources/ first (list the folder, then read what's relevant). "
    "Base everything on the sources and cite them as (file name, page or section). "
    "Use Markdown, with $...$ and $$...$$ for math. Don't write any files: return the result "
    "in the structured output."
)
KINDS = {
    "guide": dict(label="Study guide", ext=".md", schema=DOC_SCHEMA, prompt=(
        "Write a study guide: the main concepts in a logical order, each explained clearly with "
        "the key formulas/definitions and a short worked example where it helps, common mistakes, "
        "and a short checklist of what to be able to do at the end.")),
    "briefing": dict(label="Briefing", ext=".md", schema=DOC_SCHEMA, prompt=(
        "Write a briefing document: a one-paragraph overview, then the most important points as a "
        "short bulleted list, key numbers/formulas, and open questions. Keep it to about one page.")),
    "glossary": dict(label="Glossary", ext=".md", schema=DOC_SCHEMA, prompt=(
        "Write a glossary: the important terms from the sources in alphabetical order, each with a "
        "precise one-to-three sentence definition, a formula if relevant, and the source.")),
    "flashcards": dict(label="Flashcards", ext=".flashcards.json", schema=CARDS_SCHEMA, prompt=(
        "Create {count} flashcards for active recall. Front: one focused question or prompt. Back: a "
        "short, complete answer (a formula is fine). Cover the most important ideas, not trivia. "
        "Put the citation in `source`.")),
    "quiz": dict(label="Quiz", ext=".quiz.json", schema=QUIZ_SCHEMA, prompt=(
        "Create a {count}-question multiple-choice quiz that checks understanding (not just "
        "recall). Each question has 4 plausible choices, exactly one correct; `answer` is the "
        "0-based index of the correct choice. `explanation` says why it's right and why the "
        "most tempting wrong choice is wrong. Put the citation in `source`. Vary which position "
        "holds the correct answer.")),
    "plan": dict(label="Study plan", ext=".plan.json", schema=PLAN_SCHEMA, prompt=(
        "Make a study/work plan as a list of tasks for the user's task tracker. Work out from the sources "
        "what has to be learned or done (chapters, problem sets, report sections, exam topics), then split it "
        "into concrete tasks that each take about 30 minutes to 3 hours, in a sensible order, with due dates "
        "spread realistically from today{deadline}. Leave the last day or two before a deadline for review. "
        "Bigger tasks may have subtasks. Titles are actions starting with a verb (e.g. \"Solve problems "
        "3.1-3.8\"). `priority`: 1 urgent, 2 high, 3 medium, 4 low (0 none). `due`: YYYY-MM-DD or empty. "
        "`labels`: up to 3 short lowercase tags such as reading, practice, review, writing. `description`: one "
        "or two sentences on what exactly to do. `source`: the citation. Don't repeat tasks that already exist "
        "(listed below). `overview`: a few lines of Markdown explaining the plan.")),
}
LANGS = {"auto": "Write in the main language of the sources.", "en": "Write in English.",
         "ja": "Write in Japanese (日本語).", "th": "Write in Thai (ภาษาไทย)."}


def build_prompt(kind: str, opts: dict) -> str:
    k = KINDS[kind]
    deadline = f", finishing by {opts['deadline']}" if opts.get("deadline") else ""
    parts = [k["prompt"].format(count=opts.get("count") or 20, deadline=deadline), COMMON, LANGS[opts.get("language") or "auto"]]
    if kind == "plan":
        parts.append("The user's existing tasks for this workspace:\n\n" + opts["_tasks"])
    if opts.get("focus"):
        parts.append(f"Focus on: {opts['focus']}")
    if opts.get("sources"):
        parts.append("Use only these sources: " + ", ".join(f"sources/{s}" for s in opts["sources"]))
    if opts.get("_folders"):
        parts.append(opts["_folders"])
    parts.append("Give the result a short, specific title.")
    return "\n\n".join(parts)


def out_name(kind: str, title: str) -> str:
    label = KINDS[kind]["label"]
    clean = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "", title).strip(" .")
    clean = re.sub(rf"^{re.escape(label)}\s*[-:–]?\s*", "", clean, flags=re.I)[:60] or label   # no "Study guide - Study guide …"
    return f"{label} - {clean} ({datetime.now():%Y-%m-%d %H%M}){KINDS[kind]['ext']}"


def run_job(job_id: int, slug: str, kind: str, opts: dict) -> None:
    conn = db.connect(check_same_thread=False)

    def finish(status: str, **cols):
        cols = {"status": status, "finished_at": now_iso(), **cols}
        conn.execute(f"UPDATE ws_jobs SET {', '.join(f'{k} = ?' for k in cols)} WHERE id = ?", (*cols.values(), job_id))
        conn.commit()

    try:
        cmd = [CLAUDE, "-p", "--output-format", "json", "--json-schema", json.dumps(KINDS[kind]["schema"]), *READ_ONLY_ARGS]
        for d in opts.get("_dirs", []):
            cmd += ["--add-dir", d]
        if opts.get("model"):
            cmd += ["--model", opts["model"]]
        proc = subprocess.run(cmd, input=build_prompt(kind, opts), cwd=ws_dir(slug), capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=TIMEOUT, creationflags=NO_WINDOW)
        try:
            res = json.loads(proc.stdout)
        except ValueError:
            return finish("error", error=(proc.stderr or proc.stdout or "no output").strip()[:500])
        data = res.get("structured_output")
        if res.get("is_error") or not data:
            return finish("error", error=str(res.get("result") or "Claude didn't return a result")[:500],
                          cost_usd=res.get("total_cost_usd"))
        title = data.get("title") or KINDS[kind]["label"]
        name = out_name(kind, title)
        path = ws_dir(slug) / "outputs" / name
        sources = ", ".join(opts.get("sources") or []) or "all sources"
        if kind == "plan":            # keep only well-formed dates; the dashboard turns these into tasks
            ok = lambda d: d if re.fullmatch(r"\d{4}-\d{2}-\d{2}", d or "") else None  # noqa: E731
            for t in data["tasks"]:
                t["due"] = ok(t["due"])
                t["priority"] = min(max(int(t.get("priority") or 0), 0), 4)
                for st in t["subtasks"]:
                    st["due"] = ok(st["due"])
        if kind in ("guide", "briefing", "glossary"):
            body = data["markdown"].strip()
            first, _, rest = body.partition("\n")
            if first.lstrip().startswith("#") and first.lstrip("# ").strip().lower() == title.strip().lower():
                body = rest.strip()           # Claude repeated the title as a heading
            path.write_text(f"# {title}\n\n*{KINDS[kind]['label']} · generated {datetime.now():%Y-%m-%d %H:%M} "
                            f"from {sources}*\n\n{body}\n", encoding="utf-8")
        else:
            if kind == "quiz":   # drop malformed questions instead of failing the whole quiz
                data["questions"] = [q for q in data["questions"] if 0 <= q["answer"] < len(q["choices"])]
            data.update(kind=kind, generated=now_iso(), sources=sources)
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        finish("done", title=title, filename=name, cost_usd=res.get("total_cost_usd"))
    except subprocess.TimeoutExpired:
        finish("error", error=f"Took longer than {TIMEOUT // 60} minutes")
    except Exception as e:
        finish("error", error=f"{type(e).__name__}: {e}"[:500])
    finally:
        RUNNING.discard(job_id)
        conn.close()


def build_router(check_token) -> APIRouter:
    r = APIRouter(prefix="/workspaces", dependencies=[Depends(check_token)])

    def mark_orphans(conn) -> None:
        """A job marked running that this server process isn't running was cut off by a
        restart (code change, reboot); it can't finish any more."""
        for j in conn.execute("SELECT id FROM ws_jobs WHERE status = 'running'").fetchall():
            if j["id"] not in RUNNING:
                conn.execute("UPDATE ws_jobs SET status = 'error', error = 'Interrupted (the server restarted)', "
                             "finished_at = ? WHERE id = ?", (now_iso(), j["id"]))
        conn.commit()

    class StudioIn(BaseModel):
        kind: str
        focus: str = Field("", max_length=500)
        count: int = Field(20, ge=3, le=60)
        language: str = "auto"
        sources: list[str] = []
        deadline: date | None = None          # study plan: finish by
        model: str | None = None              # default: the workspace's model

    @r.get("/studio/kinds")
    def kinds():
        return [{"kind": k, "label": v["label"]} for k, v in KINDS.items()]

    @r.post("/{slug}/studio")
    def start(slug: str, body: StudioIn, conn=Depends(get_conn)):
        w = get_ws(conn, slug)
        ensure_layout(slug, w["name"], w["description"])
        if body.kind not in KINDS or body.language not in LANGS:
            raise HTTPException(400, "Unknown kind or language")
        if body.model is not None and body.model not in MODELS:
            raise HTTPException(400, "Unknown model")
        body.model = body.model or ws_model(conn, slug)
        known = {s["filename"] for s in conn.execute("SELECT filename FROM ws_sources WHERE ws = ?", (slug,))}
        folders = [f for f in linked_folders(conn, slug) if f["ok"]]
        if not known and not folders:
            raise HTTPException(400, "Add some sources or link a folder first")
        if any(s not in known for s in body.sources):
            raise HTTPException(400, "Unknown source")
        mark_orphans(conn)
        if conn.execute("SELECT 1 FROM ws_jobs WHERE ws = ? AND status = 'running'", (slug,)).fetchone():
            raise HTTPException(409, "Studio is already making something in this workspace")
        opts = body.model_dump(exclude={"kind"}, mode="json")
        cur = conn.execute("INSERT INTO ws_jobs (ws, kind, options, status, started_at) VALUES (?, ?, ?, 'running', ?)",
                           (slug, body.kind, json.dumps(opts, ensure_ascii=False), now_iso()))
        conn.commit()
        RUNNING.add(cur.lastrowid)
        if folders and not body.sources:   # linked folders count as sources (unless specific files were picked)
            opts = {**opts, "_dirs": [f["path"] for f in folders], "_folders": folders_context(folders)}
        if body.kind == "plan":          # Claude sees the tasks that already exist (not saved with the job)
            import focus
            opts = {**opts, "_tasks": tasks.task_lines(conn, slug) + "\n\n" + focus.workspace_context(conn, slug)}
        threading.Thread(target=run_job, args=(cur.lastrowid, slug, body.kind, opts), daemon=True).start()
        return {"job_id": cur.lastrowid}

    @r.get("/{slug}/studio/jobs")
    def jobs(slug: str, conn=Depends(get_conn)):
        get_ws(conn, slug)
        mark_orphans(conn)
        return [{**dict(j), "options": json.loads(j["options"]), "label": KINDS.get(j["kind"], {}).get("label", j["kind"])}
                for j in conn.execute("SELECT * FROM ws_jobs WHERE ws = ? ORDER BY id DESC LIMIT 10", (slug,))]

    @r.delete("/{slug}/outputs/{name}")
    def delete_output(slug: str, name: str, conn=Depends(get_conn)):
        get_ws(conn, slug)
        folder = (ws_dir(slug) / "outputs").resolve()
        p = (folder / name).resolve()
        if p.parent != folder or not p.is_file():
            raise HTTPException(404, "Not found")
        p.unlink()
        return {"deleted": name}

    return r
