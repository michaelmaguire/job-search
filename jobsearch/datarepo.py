"""Commit and push the data repo (data/) from the web app: git add -A, commit, push.

Never force-pushes, never prompts (BatchMode ssh, no terminal prompts): if the push can't
happen - offline, remote has newer commits, locked ssh key - the commit stays local and the
error is reported. Refuses to commit on a detached HEAD.
"""
import os
import subprocess

from .paths import DATA

ENV = {**os.environ, "GIT_TERMINAL_PROMPT": "0",
       "GIT_SSH_COMMAND": os.environ.get("GIT_SSH_COMMAND", "ssh -o BatchMode=yes -o ConnectTimeout=15")}


def git(*args, timeout=60):
    r = subprocess.run(["git", "-C", str(DATA), *args], capture_output=True, text=True, timeout=timeout, env=ENV)
    return r.returncode, (r.stdout + r.stderr).rstrip()  # keep leading spaces: porcelain status columns


def status():
    """{'changes': [porcelain lines], 'branch': name or None, 'ahead': commits not pushed, 'ok': is a repo}."""
    code, out = git("status", "--porcelain", "--untracked-files=all", timeout=15)
    if code:
        return {"ok": False, "changes": [], "branch": None, "ahead": 0, "error": out}
    changes = [line for line in out.splitlines() if line.strip()]
    code, branch = git("symbolic-ref", "--short", "-q", "HEAD", timeout=15)
    branch = branch if code == 0 else None
    code, ahead = git("rev-list", "--count", "@{u}..HEAD", timeout=15)
    return {"ok": True, "changes": changes, "branch": branch, "ahead": int(ahead) if code == 0 and ahead.isdigit() else 0}


def default_message(changes):
    """e.g. 'mathrix-senior-c-c-low-latency-engineer, efforts (3 files)'."""
    parts = []
    for line in changes:
        path = line[3:].strip().strip('"').split(" -> ")[-1]
        segs = path.split("/")
        name = segs[1][11:] if segs[0] == "jobs" and len(segs) > 1 and segs[1][:4].isdigit() else \
            segs[1] if segs[0] == "jobs" and len(segs) > 1 else segs[0].rsplit(".", 1)[0]
        if name not in parts:
            parts.append(name)
    shown = ", ".join(parts[:4]) + (f" +{len(parts) - 4} more" if len(parts) > 4 else "")
    return f"{shown} ({len(changes)} file{'s' if len(changes) != 1 else ''})"


def commit_and_push(message=""):
    """Returns (ok, summary). Commits all changes (if any), then pushes the branch."""
    st = status()
    if not st["ok"]:
        return False, f"data/ is not a git repository: {st.get('error', '')}"
    if not st["branch"]:
        return False, "data/ is on a detached HEAD - check out a branch (e.g. git -C data checkout main) first"
    done = []
    if st["changes"]:
        message = (message or "").strip() or default_message(st["changes"])
        code, out = git("add", "-A")
        if code:
            return False, f"git add failed: {out}"
        code, out = git("commit", "-q", "-m", message)
        if code:
            return False, f"git commit failed: {out}"
        done.append(f"committed {len(st['changes'])} file(s): {message}")
    elif not st["ahead"]:
        return True, "nothing to commit or push - data/ is up to date"
    code, out = git("push", "-q", timeout=90)
    if code:
        hint = " (GitHub has newer commits - pull them first: git -C data pull --rebase)" if "rejected" in out or "fetch first" in out else ""
        return False, "; ".join(done + [f"push failed, commit kept locally{hint}: {out[-300:]}"])
    done.append(f"pushed to {st['branch']}")
    return True, "; ".join(done)
