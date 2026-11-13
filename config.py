"""Personal settings, read from config.json next to this file (git-ignored; see
config.example.json). Missing keys fall back to the generic defaults below, so the code
itself holds nothing personal."""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULTS = {
    "allowed_logins": [],          # Tailscale logins that may use the dashboard without the token
    "docs_repos": {},              # "owner/repo": "reports" | "notes", counted on the Docs page
    "link_roots": [],              # local clones the workspaces / Notes may read (in this order)
    "backup_repo": "",             # private GitHub repo for the encrypted daily DB backups
    "money_accounts": [["Wallet", "📱", "wallet"], ["Bank", "🏦", "bank"],
                       ["Cash", "💴", "cash"], ["Savings", "🪙", "savings"]],
}


def _load() -> dict:
    try:
        data = json.loads((HERE / "config.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    return {k: v for k, v in data.items() if not k.startswith("_")}


_CONFIG = _load()


def get(key: str):
    return _CONFIG.get(key, DEFAULTS[key])


def paths(key: str) -> list[Path]:
    return [Path(p).expanduser() for p in get(key)]
