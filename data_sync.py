"""Keep the dashboard's data files current (Nov 4, 2026).

Streamlit Cloud only redeploys when GitHub tells it about a push, and it was skipping the bot
commits that the scheduled workflows make every few minutes ("[skip ci]" rounds). The app kept
reading the CSVs from its old checkout, so settled bets sat on "Live · waiting to settle" for hours.

sync() moves the app's checkout to origin/main at most once every SYNC_EVERY seconds, so every
page sees what the workflows last committed.

Two ways in, tried in order:
  1. git: the checkout Streamlit Cloud runs from is a git clone, so a shallow fetch is cheap.
  2. tarball: if there's no usable git, download the repo snapshot from codeload and copy just the
     data files (csv / json / gz) out of it (slower, so it runs less often).
Any failure just leaves the current files in place; the page still renders.
"""
from __future__ import annotations

import io
import os
import shutil
import subprocess
import tarfile
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO = os.environ.get("SHARPIQ_REPO", "colebeasley10-glitch/pm-edge")
BRANCH = os.environ.get("SHARPIQ_BRANCH", "main")
SYNC_EVERY = 120            # seconds between git syncs
TARBALL_EVERY = 600         # seconds between tarball syncs (only when git isn't available)
DATA_EXT = (".csv", ".json", ".gz")

_lock = threading.Lock()
_state = {"last": 0.0, "ok": None, "how": "", "error": ""}


def is_data(path: str) -> bool:
    return path.endswith(DATA_EXT) and not path.startswith((".git/", ".github/"))


def _git(*args: str, timeout: int = 60) -> str:
    out = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True, timeout=timeout)
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip() or f"git {args[0]} failed")
    return out.stdout


def _sync_git() -> int:
    """Move the checkout to origin's newest commit. Returns how many data files changed.

    A plain reset (not a partial checkout) keeps git's state clean, so Streamlit's own redeploy
    pull still fast-forwards afterwards instead of tripping over half-updated files.
    """
    if not (ROOT / ".git").exists() or not shutil.which("git"):
        raise RuntimeError("no git checkout")
    _git("fetch", "--depth", "1", "origin", BRANCH, timeout=90)
    old, new = _git("rev-parse", "HEAD").strip(), _git("rev-parse", "FETCH_HEAD").strip()
    if old == new:
        return 0
    try:
        changed = [f for f in _git("diff", "--name-only", old, new).split() if is_data(f)]
    except RuntimeError:
        changed = []
    _git("reset", "--hard", new)
    return len(changed) or 1


def _sync_tarball() -> int:
    url = f"https://codeload.github.com/{REPO}/tar.gz/refs/heads/{BRANCH}"
    with urllib.request.urlopen(url, timeout=120) as r:
        blob = r.read()
    n = 0
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
        for m in tar.getmembers():
            if not m.isfile():
                continue
            rel = m.name.split("/", 1)[1] if "/" in m.name else ""
            if not rel or not is_data(rel) or ".." in Path(rel).parts:
                continue
            dest = ROOT / rel
            data = tar.extractfile(m).read()
            if dest.exists() and dest.read_bytes() == data:
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=dest.parent, prefix=".sync-")
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            os.replace(tmp, dest)
            n += 1
    return n


def sync(force: bool = False) -> dict:
    """Refresh data files if it's been long enough. Safe to call on every page load."""
    if os.environ.get("SHARPIQ_NO_SYNC"):
        return dict(_state)
    wait = TARBALL_EVERY if _state["how"] == "tarball" else SYNC_EVERY
    if not force and time.time() - _state["last"] < wait:
        return dict(_state)
    if not _lock.acquire(blocking=False):       # another session is already syncing
        return dict(_state)
    try:
        _state["last"] = time.time()
        try:
            n, how = _sync_git(), "git"
        except Exception as git_err:  # noqa: BLE001
            try:
                n, how = _sync_tarball(), "tarball"
            except Exception as tar_err:  # noqa: BLE001
                _state.update(ok=False, error=f"git: {git_err}; tarball: {tar_err}")
                return dict(_state)
        _state.update(ok=True, how=how, error="", updated=n, at=time.time())
        return dict(_state)
    finally:
        _lock.release()


def status() -> dict:
    return dict(_state)
