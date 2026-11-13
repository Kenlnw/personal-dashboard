"""Git sync for the notes repos (PersonalDocs, LaTeX_docs, Typst_docs) from the dashboard.

The PC's clones are kept in step with GitHub, where the iPhone / iPad (Git Sync app) push:
- Pull: fetch, then fast-forward only. Local uncommitted edits are fine as long as GitHub's
  new commits don't touch the same files (then they are stashed and put back automatically).
- Commit & push: commit everything changed on the PC ("Last Sync: <time> (PC)", like the iPad),
  pull with rebase if GitHub is ahead, then push.
- Auto-pull: every AUTO_EVERY seconds for roots in AUTO_ROOTS, when it is safe (same rules).
Nothing is ever merged by hand: if both sides changed the same file, it stops and says so.
Pushing uses the GitHub CLI's login (no Git Credential Manager pop-ups on an unattended PC).
"""
import json
import os
import shutil
import subprocess
import threading
import time
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

import db
import notes

GIT = shutil.which("git") or r"C:\Program Files\Git\cmd\git.exe"
GH = shutil.which("gh") or r"C:\Program Files\GitHub CLI\gh.exe"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
AUTO_ROOTS = {"personaldocs"}
AUTO_EVERY = 600
_locks: dict[str, threading.Lock] = {}


class GitError(Exception):
    pass


def lock_for(root: str) -> threading.Lock:
    return _locks.setdefault(root, threading.Lock())


def git_run(root: str, *args: str, timeout: int = 90) -> subprocess.CompletedProcess:
    """git in the root's folder, logging in through the GitHub CLI and never prompting."""
    gh = GH.replace("\\", "/")
    cmd = [GIT, "-c", "credential.helper=", "-c", f'credential.helper=!"{gh}" auth git-credential',
           "-c", "core.quotepath=false", *args]
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"}
    return subprocess.run(cmd, cwd=notes.root_path(root), capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout, creationflags=NO_WINDOW, env=env)


def git(root: str, *args: str, timeout: int = 90, check: bool = True) -> str:
    r = git_run(root, *args, timeout=timeout)
    if check and r.returncode != 0:
        raise GitError((r.stderr or r.stdout).strip()[:400] or f"git {args[0]} failed")
    return r.stdout


def is_repo(root: str) -> bool:
    try:
        return git(root, "rev-parse", "--is-inside-work-tree", check=False).strip() == "true"
    except (OSError, subprocess.TimeoutExpired):
        return False


def changed_files(root: str) -> list[dict]:
    out = []
    for line in git(root, "status", "--porcelain=v1", "-uall").splitlines():
        code, path = line[:2], line[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        kind = "new" if code == "??" or "A" in code else "deleted" if "D" in code else "modified"
        out.append({"path": path.strip('"'), "kind": kind})
    return out


def state(root: str, fetch: bool = False) -> dict:
    if fetch:
        git(root, "fetch", "--quiet", "origin", timeout=60)
    branch = git(root, "rev-parse", "--abbrev-ref", "HEAD").strip()
    ahead = behind = 0
    upstream = git(root, "rev-parse", "--abbrev-ref", "@{u}", check=False).strip()
    if upstream:
        counts = git(root, "rev-list", "--left-right", "--count", "HEAD...@{u}").split()
        ahead, behind = int(counts[0]), int(counts[1])
    last = git(root, "log", "-1", "--format=%h%x1f%s%x1f%cI").strip().split("\x1f")
    incoming = git(root, "diff", "--name-only", "HEAD...@{u}").splitlines() if behind else []
    return {"root": root, "name": notes.root_path(root).name, "branch": branch, "upstream": upstream,
            "ahead": ahead, "behind": behind, "changes": changed_files(root), "incoming": incoming,
            "last_commit": {"hash": last[0], "subject": last[1], "date": last[2]} if len(last) == 3 else None}


def remember(root: str, what: str, result: str) -> None:
    conn = db.connect()
    try:
        db.set_state(conn, f"git_{what}_{root}", f"{datetime.now().astimezone().isoformat(timespec='seconds')} {result}")
    finally:
        conn.close()


def pull(root: str) -> dict:
    """Fast-forward to GitHub. Refuses (without changing anything) when it isn't clearly safe."""
    s = state(root, fetch=True)
    if not s["behind"]:
        return {"ok": True, "message": "Already up to date", **s}
    if s["ahead"]:
        raise GitError(f"The PC has {s['ahead']} commit(s) GitHub doesn't, and GitHub has {s['behind']} new one(s). "
                       "Use Commit & push, which puts the PC's commits on top.")
    mine = {c["path"] for c in s["changes"]}
    both = sorted(mine & set(s["incoming"]))
    if both:
        raise GitError("Changed on the PC and on another device: " + ", ".join(both[:5])
                       + (" …" if len(both) > 5 else "") + ". Nothing was changed. Decide on the PC which version to keep.")
    git(root, "pull", "--ff-only", "--autostash", "--quiet", "origin", s["branch"], timeout=120)
    after = state(root)
    notes.forget(root)
    msg = f"Pulled {s['behind']} commit(s): {len(s['incoming'])} file(s) updated"
    remember(root, "pull", msg)
    return {"ok": True, "message": msg, "updated": s["incoming"], **after}


def commit_push(root: str, message: str) -> dict:
    s = state(root, fetch=True)
    committed = False
    if s["changes"]:
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", message)
        committed = True
    s = state(root)
    if s["behind"]:
        r = git_run(root, "pull", "--rebase", "--quiet", "origin", s["branch"], timeout=120)
        if r.returncode != 0:
            conflicts = git(root, "diff", "--name-only", "--diff-filter=U", check=False).split()
            git(root, "rebase", "--abort", check=False)
            raise GitError("GitHub has newer versions of the same file(s): " + ", ".join(conflicts[:5] or ["(see git)"])
                           + ". Your commit is saved on the PC but not pushed. Merge it on the PC (or in Obsidian), then push.")
    if not committed and not state(root)["ahead"]:
        return {"ok": True, "message": "Nothing to commit or push", **state(root)}
    git(root, "push", "--quiet", "origin", s["branch"], timeout=120)
    notes.forget(root)
    after = state(root)
    msg = f"Pushed: {after['last_commit']['subject']}" if after["last_commit"] else "Pushed"
    remember(root, "push", msg)
    return {"ok": True, "message": msg, **after}


def auto_loop() -> None:
    time.sleep(60)
    while True:
        for root in AUTO_ROOTS:
            lk = lock_for(root)
            if root in notes.roots() and is_repo(root) and lk.acquire(blocking=False):
                try:
                    pull(root)
                except (GitError, OSError, subprocess.TimeoutExpired) as e:
                    remember(root, "auto", f"skipped: {str(e)[:200]}")
                finally:
                    lk.release()
        time.sleep(AUTO_EVERY)


# ---------------------------------------------------------------- "Do task" after a pull
# PersonalDocs has a Claude Code skill (.claude/skills/transcribe-exports, triggered by the prompt
# "Do task") that transcribes handwritten notebook pages. After a Pull from the dashboard, if its
# scan finds pages waiting, run it headless. It may only read, run the skill's own script and a few
# read-only shell commands, and write scratch files in .papi-tmp/ (git-excluded). No commits, no
# deletions: the changed notebooks show up as "changed on the PC" for Commit & push.

TASK_ROOT = "personaldocs"
TASK_MODEL = "sonnet"          # lighter on the plan; "opus" reads hard handwriting a bit more reliably
TASK_TIMEOUT = 40 * 60
SKILL_JS = ".claude/skills/transcribe-exports/notebook-ink.js"
TASK: dict = {"state": "idle"}


def task_pending(root: str) -> dict:
    """The skill's own scan (free, no Claude): notebooks with new / changed pages, leftover export PDFs."""
    base = notes.root_path(root)
    if not (base / SKILL_JS).exists():
        return {"available": False, "notebooks": [], "pages": 0, "exports": []}
    r = subprocess.run(["node", "-e", f"process.stdout.write(JSON.stringify(require('./{SKILL_JS}').scan()))"],
                       cwd=base, capture_output=True, text=True, encoding="utf-8", timeout=120, creationflags=NO_WINDOW)
    if r.returncode != 0:
        raise GitError("The notebook scan failed: " + (r.stderr or r.stdout).strip()[:300])
    work = json.loads(r.stdout or "[]")
    exports = [p.relative_to(base).as_posix() for p in base.rglob("Exports/*.pdf")
               if not any(part in (".git", ".obsidian", ".trash", ".claude") for part in p.parts)]
    return {"available": True, "exports": exports, "pages": sum(len(w.get("pages") or []) for w in work),
            "notebooks": [{"md": w["md"], "pages": [p["n"] for p in w.get("pages") or []], "error": w.get("error")} for w in work]}


def _task_save() -> None:
    conn = db.connect()
    try:
        db.set_state(conn, f"task_{TASK_ROOT}", json.dumps(TASK, ensure_ascii=False))
    finally:
        conn.close()


def task_status() -> dict:
    if TASK.get("state") == "idle":                     # after a restart: the last saved run
        conn = db.connect()
        try:
            saved = db.get_state(conn, f"task_{TASK_ROOT}")
        finally:
            conn.close()
        if saved:
            last = json.loads(saved)
            if last.get("state") == "running":           # the server restarted mid-run
                last.update(state="error", error="Interrupted (the server restarted)")
            return last
    return dict(TASK)


def run_task(root: str, trigger: str) -> None:
    """Background: run the skill with headless Claude Code, holding the repo lock (no pull/push meanwhile)."""
    import workspaces as W
    base = notes.root_path(root)
    lk = lock_for(root)
    if not lk.acquire(timeout=300):
        TASK.update(state="error", error="Another sync was running", finished=_now()); _task_save(); return
    # scratch folder at the repo root: Claude Code won't let a run write under .claude/ (settings, hooks)
    run_dir = base / ".papi-tmp" / datetime.now().strftime("run-%Y%m%d-%H%M%S")
    try:
        excl = base / ".git" / "info" / "exclude"         # keep scratch files out of git, locally only
        excl.parent.mkdir(parents=True, exist_ok=True)
        if ".papi-tmp/" not in (excl.read_text(encoding="utf-8") if excl.exists() else ""):
            with open(excl, "a", encoding="utf-8") as fh:
                fh.write("\n# personal-api dashboard: Do task scratch files\n.papi-tmp/\n")
        run_dir.mkdir(parents=True, exist_ok=True)
        rel_tmp = run_dir.relative_to(base).as_posix()
        # file writes only inside the scratch folder (Edit rules cover Write too); everything else
        # is denied by dontAsk. Don't put Edit in --disallowedTools: a deny there would block these.
        allowed = ",".join(["Read", "Glob", "Grep", "Write(./.papi-tmp/**)", "Edit(./.papi-tmp/**)",
                            f"Bash(node {SKILL_JS}:*)", "Bash(sed:*)", "Bash(grep:*)", "Bash(cut:*)", "Bash(head:*)",
                            "Bash(tail:*)", "Bash(wc:*)", "Bash(ls:*)", "Bash(pdftoppm:*)", "Bash(mkdir:*)"])
        exports = task_pending(root)["exports"]
        system = (f"This run was started automatically from the personal dashboard after a git pull ({trigger}); "
                  "nobody is watching, so don't ask questions. "
                  f"Your scratchpad for this run is ./{rel_tmp}/ (it already exists): render pages there and write every draft, "
                  "old/new snippet and helper file there with the Write tool. "
                  "Permissions are narrow: run ONE command per Bash call (no ;, && or | chains except `| cut`/`| head` after sed/grep), "
                  f"only `node {SKILL_JS} ...`, sed, grep, cut, head, tail, wc, ls, pdftoppm, mkdir. Use the Glob and Grep tools "
                  "to find files (no find). "
                  + (f"Leftover export PDFs: {', '.join(exports)}. " if exports else "There are no leftover export PDFs. ")
                  + "Skip step 6 (deleting export PDFs): list any in the report instead. Don't commit. "
                  "End with the step-7 report as short Markdown.")
        cmd = [W.CLAUDE, "-p", "--output-format", "json", "--model", TASK_MODEL, "--permission-mode", "dontAsk",
               "--allowedTools", allowed, "--disallowedTools", "NotebookEdit,WebFetch,WebSearch,PowerShell",
               "--permission-prompts", "none", "--append-system-prompt", system]
        r = subprocess.run(cmd, input="Do task", cwd=base, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=TASK_TIMEOUT, creationflags=NO_WINDOW)
        try:
            res = json.loads(r.stdout)
        except ValueError:
            raise GitError((r.stderr or r.stdout or "Claude Code gave no output").strip()[:400])
        after = task_pending(root)
        denied = [f'{d.get("tool_name")}: ' + str((d.get("tool_input") or {}).get("command") or (d.get("tool_input") or {}).get("file_path") or "")[:200]
                  for d in res.get("permission_denials") or []]
        TASK.update(state="error" if res.get("is_error") else "done", finished=_now(), report=res.get("result") or "", denied=denied,
                    cost_usd=res.get("total_cost_usd"), left=after["pages"],
                    error=(res.get("result") or "")[:400] if res.get("is_error") else None)
    except subprocess.TimeoutExpired:
        TASK.update(state="error", finished=_now(), error=f"Took longer than {TASK_TIMEOUT // 60} minutes")
    except Exception as e:
        TASK.update(state="error", finished=_now(), error=f"{type(e).__name__}: {e}"[:400])
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)
        try:
            run_dir.parent.rmdir()                     # .papi-tmp itself, when empty
        except OSError:
            pass
        lk.release()
        notes.forget(root)
        _task_save()


def start_task(root: str, trigger: str) -> dict:
    """Start the skill if its scan finds work; returns what happened (never blocks)."""
    if root != TASK_ROOT:
        return {"started": False, "reason": "only PersonalDocs has the Do task skill"}
    if TASK.get("state") == "running":
        return {"started": False, "reason": "already running", **TASK}
    work = task_pending(root)
    if not work["available"]:
        return {"started": False, "reason": "the transcribe-exports skill isn't in this repo"}
    if not work["pages"] and not any(n.get("error") for n in work["notebooks"]):
        return {"started": False, "reason": "nothing to transcribe", "exports": work["exports"]}
    TASK.clear()
    TASK.update(state="running", started_at=_now(), trigger=trigger, pages=work["pages"], notebooks=work["notebooks"])
    _task_save()
    threading.Thread(target=run_task, args=(root, trigger), daemon=True, name="do-task").start()
    return {"started": True, **TASK}


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def start_auto() -> None:
    threading.Thread(target=auto_loop, daemon=True, name="git-auto-pull").start()


# ---------------------------------------------------------------- API

class CommitIn(BaseModel):
    message: str = Field("", max_length=500)


def build_router(check_token) -> APIRouter:
    r = APIRouter(prefix="/git", dependencies=[Depends(check_token)])

    def checked(root: str) -> str:
        notes.root_path(root)
        if not is_repo(root):
            raise HTTPException(400, "Not a git repository")
        return root

    def run(root: str, fn, *args):
        lk = lock_for(root)
        if not lk.acquire(timeout=30):
            raise HTTPException(409, "A sync is already running for this folder")
        try:
            return fn(root, *args)
        except GitError as e:
            raise HTTPException(409, str(e))
        except subprocess.TimeoutExpired:
            raise HTTPException(504, "Git took too long (no internet?)")
        finally:
            lk.release()

    @r.get("")
    def all_status():
        out = []
        conn = db.connect()
        try:
            for rid in notes.roots():
                if is_repo(rid):
                    s = state(rid)
                    s["last_pull"] = db.get_state(conn, f"git_pull_{rid}")
                    s["last_push"] = db.get_state(conn, f"git_push_{rid}")
                    s["last_auto"] = db.get_state(conn, f"git_auto_{rid}")
                    s["auto"] = rid in AUTO_ROOTS
                    out.append(s)
        finally:
            conn.close()
        return out

    @r.get("/{root}")
    def status(root: str, fetch: bool = False):
        return run(checked(root), lambda rt: state(rt, fetch=fetch))

    @r.post("/{root}/pull")
    def do_pull(root: str, then_task: bool = False):
        """Pull; with then_task (the dashboard's Pull button), start "Do task" if pages are waiting."""
        if root == TASK_ROOT and TASK.get("state") == "running":
            raise HTTPException(409, "Do task is still transcribing; pull again when it's done")
        out = run(checked(root), pull)
        if then_task and root == TASK_ROOT:
            try:
                out["task"] = start_task(root, "Pull button")
            except GitError as e:
                out["task"] = {"started": False, "reason": str(e)}
        return out

    @r.get("/{root}/task")
    def task_get(root: str, scan: bool = False):
        out = task_status()
        if scan and root == TASK_ROOT:
            out["pending"] = task_pending(root)
        return out

    @r.post("/{root}/task")
    def task_start(root: str):
        try:
            return start_task(checked(root), "Do task button")
        except GitError as e:
            raise HTTPException(409, str(e))

    @r.post("/{root}/push")
    def do_push(root: str, body: CommitIn):
        if root == TASK_ROOT and TASK.get("state") == "running":
            raise HTTPException(409, "Do task is still transcribing; push when it's done")
        msg = body.message.strip() or f"Last Sync: {datetime.now():%Y-%m-%d %H:%M} (PC)"
        return run(checked(root), commit_push, msg)

    return r
