"""Start the Personal API server in the background, with no window (used at login).

Run with pythonw.exe. This process is a small supervisor:
  * it starts the server (uvicorn, no window) as a child process,
  * restarts it when a .py file in this folder changes (like `--reload`), and
  * restarts it if it crashes.
uvicorn's own --reload can't be used here: it stops its child with a Ctrl+C signal, which
needs a console window, and this runs without one.

Output of both goes to server.log. If something already listens on the port (e.g. a
server you started by hand), it exits instead of starting a second one.

Port: 8000, or the PAPI_PORT environment variable (for testing).
Stop it: Task Manager → end the pythonw.exe processes, or
    Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" |
      Where-Object CommandLine -like '*start_server.pyw*' | ForEach-Object { Stop-Process -Id $_.ProcessId }
"""
import os
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOG = HERE / "server.log"
PORT = int(os.environ.get("PAPI_PORT", "8000"))
SERVE = "--serve" in sys.argv

# pythonw has no console: send all output to the log, and have no stdin at all.
os.chdir(HERE)
sys.path.insert(0, str(HERE))
if not SERVE and LOG.exists() and LOG.stat().st_size > 5_000_000:
    LOG.replace(HERE / "server.log.1")            # keep the log from growing forever
_log = open(LOG, "a", encoding="utf-8", buffering=1)
sys.stdout = sys.stderr = _log
sys.stdin = None


def say(msg: str) -> None:
    print(f"--- {datetime.now().isoformat(timespec='seconds')} supervisor: {msg}")


def port_in_use(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", port)) == 0


def code_stamp() -> float:
    """Newest modification time of the project's own .py files."""
    return max((p.stat().st_mtime for p in HERE.glob("*.py")), default=0.0)


def start_child() -> subprocess.Popen:
    return subprocess.Popen([sys.executable, str(Path(__file__)), "--serve"], cwd=HERE,
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def supervise() -> None:
    say(f"starting, port {PORT}")
    if port_in_use(PORT):
        say(f"port {PORT} is already in use; not starting a second server")
        return
    child, stamp, crashes = start_child(), code_stamp(), 0
    while True:
        time.sleep(1)
        new = code_stamp()
        if new != stamp:
            time.sleep(0.5)                       # let a multi-file save finish
            stamp = code_stamp()
            say("code changed, restarting the server")
            child.terminate()
            child.wait(timeout=15)
            child, crashes = start_child(), 0
        elif child.poll() is not None:
            crashes += 1
            delay = min(60, 2 ** crashes)
            say(f"server exited with code {child.returncode}; restarting in {delay}s")
            time.sleep(delay)
            stamp, child = code_stamp(), start_child()


if __name__ == "__main__":
    if SERVE:
        import uvicorn
        uvicorn.run("main:app", host="127.0.0.1", port=PORT)
    else:
        supervise()
