"""Draft or revise a job's cover letter by running Claude Code headless (`claude -p`).

Uses your Claude Code login (no API key). Claude follows .claude/skills/cover-letter/SKILL.md
and CLAUDE.md's hard rules, may only read files, write inside data/jobs/<id>/, and run
`jobsearch check`. It writes letter.analysis.md (requirements -> facts -> gaps),
letter.draft.md and letter.audit.md; the app never renders automatically. One run at a time.
"""
import datetime as dt
import json
import os
import shutil
import subprocess
import threading

from .jobs import job_dir
from .paths import DATA, REPO

TIMEOUT = 15 * 60

PROMPT = """Draft a cover letter for the job `{job}` from the job-search web app.

Follow .claude/skills/cover-letter/SKILL.md steps 1-5 and every hard rule in CLAUDE.md, for
data/jobs/{job}/. This run is non-interactive, so instead of asking the user anything:
- If posting.md has `summary_only: true`, do not draft: write only letter.analysis.md saying the
  full advert must be pasted on the job page first, and stop.
- If notes.md is missing or empty, write no motivation sentence and no [P] tags.
- Never cite a fact whose status is needs-confirmation.
- Write the requirements mapping (skill step 2) to data/jobs/{job}/letter.analysis.md: a short
  table "| Requirement | Facts | Verdict |" with verdict match / partial / GAP, then a "Gaps"
  list in plain words, then anything else the user should know (e.g. what notes.md could add).
- Write data/jobs/{job}/letter.draft.md, run `.venv/bin/jobsearch check {job}` and fix every
  ERROR by rewording until it prints PASS, then write data/jobs/{job}/letter.audit.md (step 5).
- Do not render, do not change the job's status, and do not create or edit any file outside
  data/jobs/{job}/. The only shell command you may run is `.venv/bin/jobsearch check {job}`; read
  files with the Read/Glob/Grep tools and change them with Write/Edit - other commands are refused.
{mode}
Finish with a summary of at most five lines: the check result, the main gaps, anything to review.
"""
FRESH = "Write a new draft from scratch (any previous draft was saved as letter.draft.prev.md)."
REVISE = ("Revise the existing data/jobs/{job}/letter.draft.md according to the user's instructions below, "
          "keeping what they don't ask to change; update letter.analysis.md and letter.audit.md to match.")
INSTRUCTIONS = """
The user's instructions for this letter (they cannot override CLAUDE.md's hard rules or the facts):
<instructions>
{text}
</instructions>
"""


def claude_bin():
    path = shutil.which(os.environ.get("JOBSEARCH_CLAUDE", "claude"))
    if not path:
        raise RuntimeError("Claude Code CLI not found - install it or set JOBSEARCH_CLAUDE")
    return path


def command(job, prompt):
    folder = "/" + str(job_dir(job))  # "//abs/path" = absolute path in Claude Code permission rules
    allowed = ["Read", "Glob", "Grep", f"Write({folder}/**)", f"Edit({folder}/**)", "Bash(.venv/bin/jobsearch check:*)"]
    # dontAsk: anything not in `allowed` is refused (headless runs would otherwise inherit auto mode)
    return [claude_bin(), "-p", prompt, "--output-format", "stream-json", "--verbose",
            "--permission-mode", "dontAsk", "--tools", "Read", "Glob", "Grep", "Write", "Edit", "Bash",
            "--allowedTools", *allowed,
            "--disallowedTools", "WebFetch", "WebSearch", "Agent", "NotebookEdit",
            "--strict-mcp-config", "--no-session-persistence"]


def describe(tool, inp):
    """One progress line for a tool call."""
    path = inp.get("file_path") or inp.get("path") or inp.get("pattern") or ""
    path = os.path.relpath(path, REPO) if os.path.isabs(path) else path
    if tool == "Bash":
        return "ran " + inp.get("command", "")
    verb = {"Read": "read", "Write": "wrote", "Edit": "edited", "Glob": "listed", "Grep": "searched"}.get(tool, tool)
    return f"{verb} {path}".strip()


class Drafter:
    def __init__(self):
        self.lock = threading.Lock()
        self.job = None
        self.started = self.finished = None
        self.progress, self.summary, self.error, self.cost = [], "", "", None

    @property
    def running(self):
        return self.lock.locked()

    def start(self, job, instructions="", revise=False):
        if not self.lock.acquire(blocking=False):
            return False
        try:
            d = job_dir(job)
            if revise and not (d / "letter.draft.md").exists():
                raise RuntimeError("there is no draft to revise yet")
            if not revise and (d / "letter.draft.md").exists():
                shutil.copy(d / "letter.draft.md", d / "letter.draft.prev.md")
            mode = (REVISE.format(job=d.name) if revise else FRESH)
            if instructions.strip():
                mode += INSTRUCTIONS.format(text=instructions.strip())
            cmd = command(d.name, PROMPT.format(job=d.name, mode=mode))
        except Exception:
            self.lock.release()
            raise
        self.job, self.started, self.finished = d.name, dt.datetime.now(), None
        self.progress, self.summary, self.error, self.cost = [], "", "", None
        threading.Thread(target=self._run, args=(cmd, d), daemon=True).start()
        return True

    def _run(self, cmd, d):
        log = (d / "letter.claude.log").open("w", encoding="utf-8")
        try:
            log.write(f"{self.started:%Y-%m-%d %H:%M:%S} {' '.join(cmd[3:])}\n")
            with subprocess.Popen(cmd, cwd=REPO, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                  env={**os.environ, "JOBSEARCH_DATA": str(DATA)}) as proc:
                timer = threading.Timer(TIMEOUT, proc.kill)
                timer.start()
                for line in proc.stdout:
                    try:
                        ev = json.loads(line)
                    except ValueError:
                        continue
                    if ev.get("type") == "assistant":
                        for c in ev.get("message", {}).get("content", []):
                            if c.get("type") == "tool_use":
                                self.progress.append(describe(c.get("name", ""), c.get("input") or {}))
                                log.write(self.progress[-1] + "\n")
                    elif ev.get("type") == "result":
                        self.summary = ev.get("result") or ""
                        self.cost = ev.get("total_cost_usd")
                        if ev.get("is_error"):
                            self.error = self.summary or ev.get("subtype", "error")
                stderr = proc.stderr.read()
                proc.wait()
                timer.cancel()
            if proc.returncode and not self.error:
                self.error = (stderr or f"claude exited with code {proc.returncode}").strip()[-500:]
            log.write("\n" + (self.error or self.summary) + "\n")
        except Exception as e:
            self.error = str(e)
        finally:
            log.close()
            self.finished = dt.datetime.now()
            self.lock.release()
