# job-search

**For job hunters who want to run their search like a project:** find adverts before they disappear, apply
with cover letters that are honest about what you've done, and keep the records an unemployment office asks
for. It's built around a search in Switzerland - the RAV/URC effort form, Swiss job sites, CHF salaries,
commute areas - but most of it works for a job search anywhere.

## Quick start for Local mode

Run it on your own computer (you need a fork and a private records repository - see [Setup](#setup)
the first time):

```sh
git clone git@github.com:<you>/job-search.git     # your fork
cd job-search
./run.sh                      # sets up, starts the app and opens http://127.0.0.1:8765/
```

`./run.sh` runs `./setup.sh` each time, then starts the app (Ctrl-C to stop). To set up without starting it,
run `./setup.sh` on its own (add `--dev` for ruff and the test extras).

`setup.sh` is safe to re-run, and handles:
- **Your records:** clones `job-search-data` from the same place as the code (`<you>/job-search-data`) into
  `data/`. If that repository is empty, it fills it from `examples/data-template` and pushes, after asking.
  If it doesn't exist yet, it tells you how to create it (keep it **private**). To use a different
  repository: `./setup.sh --data-url <url>`.
- **The app:** creates `.venv` and installs it, or rebuilds `.venv` if it's broken (for example after you
  move or rename the folder).
- **Optional tools:** warns if LibreOffice, Chrome or Claude Code is missing.

If something stops working after a `git pull`, run `./setup.sh` again. (Use a plain `git clone`, not
`--recurse-submodules`: in a fork, that tries to fetch the upstream author's records and fails.)

## Two modes

**It works in two modes** - the same app and pages either way:

| | [Browser mode](#try-it-in-your-browser) | [Local mode](#run-it-locally-for-the-full-app) |
|---|---|---|
| **What it is** | Runs entirely in your browser from a web page | A small web server you run on your own computer |
| **To start** | Open the page - nothing to install | Clone this repository and install it (Python, plus LibreOffice and Chrome for PDFs) |
| **Your records** | An encrypted copy kept in that browser, moved in and out as git bundles | Your clone of your records repository, committed and pushed from the app |
| **Good for** | Trying it out, or using it on a computer where you can't install anything | Day-to-day use, with [extra features](#what-you-get-locally): drafting letters with Claude, Gmail drafts, PDFs of advert pages, fetching from every job board |

The two don't sync automatically: use one as your main place, and move records between them as a git bundle.

## What it offers

- **One inbox for adverts.** It fetches new adverts from company job boards (Greenhouse, Lever, Ashby,
  SmartRecruiters, Workday, SuccessFactors, Recruitee, Personio) and swissdevjobs.ch, and you add the ones you
  find elsewhere (LinkedIn, jobs.ch, recruiters) - by pasting, or with one click using the "Save this job" browser
  extension. The full advert text is saved, so it's still there when the advert is gone.
- **Filters you set once.** Title and location filters, a minimum salary, your commute area, and
  companies or sources you never want to see again.
- **Cover letters you can stand behind.** Every claim in a letter must cite a fact from your own evidence base
  (built from your CV and what you tell it), the advert, or your notes. A checker catches untagged sentences,
  numbers that don't match their source, inflated verbs ("led" when you "supported") and stock puffery, before
  the letter is rendered to PDF and Word. Claude can draft the letter for you, under the same rules.
- **Applications tracked to the end.** Status from new to offer or rejection, acknowledgement and rejection emails
  saved with each job, recruiter conversations, and every effort logged.
- **A record of every advert you respond to.** Each application keeps the advert's full text and, in the local
  app, a PDF of the advert page saved automatically when you shortlist, draft or apply - together with your
  letter, the emails and the efforts logged for it. If the RAV/URC asks what you applied for, you can show them,
  even after the advert has been taken down.
- **The paperwork done for you.** A monthly report of your job-search efforts in the RAV/URC layout, in German or
  Italian, including the official form 716.007 filled in as a PDF.
- **Configurable to your search and your rules.** Which companies and sites to fetch from, title and location
  filters, the terms that score an advert higher, minimum salary and exchange rates, commute area; and for
  letters, your sender profiles (name, address, email per profile), CV, word limit, minimum number of cited
  facts, the phrases and verbs the checker refuses, and a closing in your own words. It's all in a few YAML files
  in your records; sources, blocks and the minimum salary can also be changed on the web app's Sources and
  Companies tabs.
- **Your data stays yours.** Your records are plain files (Markdown, YAML, CSV) in a private git repository of
  your own - host it where you like, or nowhere - and everything works without Claude.

This code repository holds no records: yours live in a separate, private repository.

## Try it in your browser

Open **https://michaelmaguire.github.io/job-search/web/#/** (or `https://<you>.github.io/job-search/web/#/` for your
fork). The first time, choose a passphrase, then either:

- **Start with empty records** - one click, to try it out. They're the starter records from `examples/data-template`.
- **Import your records,** if you already have them, as a git bundle: `git -C data bundle create ~/jobsearch.bundle --all`.

How it works:

- **Your data stays in your browser.** It's kept as one blob encrypted with your passphrase (AES-GCM, PBKDF2) and
  decrypted only in the open tab. Nothing is sent to a server. There's no recovery without the passphrase, so
  keep your records in git as the backup. If you forget it, "Forgot your passphrase?" on the unlock screen deletes
  the browser's copy so you can set up again.
- **The same pages as the local app:** a Web Worker runs the `jobsearch` web app itself (Flask, the same routes and
  templates) in [Pyodide](https://pyodide.org), and git runs via [isomorphic-git](https://isomorphic-git.org).
- **Data** (in the header) is where you commit, export your records as a bundle (`git clone jobsearch.bundle` makes
  a normal repository of it; or `git pull` from it into your clone and push), import another one, and set your
  commit name and the name and AHV number for the URC/RAV form. You can also edit the files in `profile/` and
  `search/` and add files such as your CV to `raw/`.
- **Each browser keeps its own copy,** separate from any local clone: changes move between them only as bundles.
- **Libraries:** Pyodide, isomorphic-git and the Python packages load at pinned versions from jsdelivr and PyPI.
- **The "Save this job" extension** works with it: set the extension's address to the URL above.

## Run it locally for the full app

### What you get locally

The local app works directly on your clone of your records, and adds what a browser tab can't do:

- **Draft and revise letters with Claude.** "Draft with Claude" on a job page runs [Claude Code](https://claude.com/claude-code)
  on your computer: it maps the advert's requirements to your facts, writes the draft and fixes it until Check
  passes. It uses your Claude subscription - no API key or separate billing.
- **Fetch from every supported board.** The browser can only reach boards that allow it (Greenhouse, Lever,
  Ashby, SmartRecruiters, swissdevjobs); locally, Workday, SuccessFactors, Recruitee and Personio work too.
- **Gmail drafts.** The application email is saved to your Gmail Drafts with the letter PDF and your CV attached,
  ready for you to send. (In the browser, "Write in Gmail" opens a new message and you attach the files yourself.)
- **PDFs of advert pages,** saved automatically when you shortlist, draft or apply - proof of the advert for the RAV.
- **Letters rendered by LibreOffice,** with the exact layout; the browser's are close but not identical. Also the
  German RAV report as a PDF (`jobsearch report --lang de --pdf`).
- **Commit & push from the header** straight to your records repository - no bundles to move around.
- **The command line** (`jobsearch ...`) and Claude Code skills in this directory: `/find-jobs`, `/add-job`,
  `/cover-letter`, `/log-effort`.

The local app opens at http://127.0.0.1:8765/ and is reachable from your own computer only. Its records live in
`data/`, a clone of your own private records repository; [Setup](#setup) sets it up.

### Setup

Your records live in `data/`, a clone of a private `job-search-data` repository next to your fork. (It's
registered as a git submodule with a relative URL, `../job-search-data.git`, so each fork finds its own.)

1. **Fork** this repository on GitHub, and create an **empty private** repository for your records
   named `<you>/job-search-data` (`gh repo create job-search-data --private`). Keep it private: it will hold
   your CV, letters, salary expectations and the RAV/URC effort log.
2. **Clone your fork and run `setup.sh`** (needs Python 3.10+):
   ```sh
   git clone git@github.com:<you>/job-search.git
   cd job-search
   ./setup.sh
   ```
   It fills your empty records repository from the starter template (asking first), then installs the app.
   Named the records repository something else? Use `./setup.sh --data-url <its url>`, and commit the changed
   `.gitmodules` in your fork.
3. **Optional tools:** rendering letters and PDF reports needs LibreOffice (`soffice`) on the PATH; saving
   advert pages as PDF needs Google Chrome or Chromium.
4. **Start the web app** (see [below](#start-the-web-app)).

On another computer, the same clone and `./setup.sh` picks up your existing records.

#### Make it yours

Personal settings live in your data repository, plus a few places outside it:
- `data/profile/facts.yaml` - your evidence base (start from your CV; put the CV itself in `data/raw/`),
  and `data/profile/letter-rules.yaml` - your sender details, CV path and standard closing.
- `data/search/config.yaml` - title filters, locations, `commute_area`, `min_salary`; and the companies on the
  web app's **Sources** tab.
- Outside both repositories, in `~/.config/jobsearch/`: `insured.yaml` (your name and AHV number for the URC/RAV
  form) and, if you use Gmail drafts, `gmail-client.json` (see `jobsearch gmail login`) - or, without a Google
  Cloud client, `jobsearch gmail login --imap` stores a Gmail [app password](https://myaccount.google.com/apppasswords)
  in `gmail-imap.yaml` (needs 2-Step Verification and IMAP enabled in Gmail settings; used whenever it exists).

`examples/data-template/README.md` (copied into your data repository) describes every file and column.

### Start the web app

```sh
./run.sh                      # or: .venv/bin/jobsearch serve
```
Then open http://127.0.0.1:8765/ (local only; stop with Ctrl-C).

## Publishing the browser version

`web/` is published to GitHub Pages by `.github/workflows/pages.yml`, together with the `jobsearch` package it
runs and `starter.bundle`. For your fork: Settings > Pages > Source: GitHub Actions, then run the "pages" workflow.
To try changes before publishing, run `python3 -m http.server 8790` in this folder and open http://127.0.0.1:8790/web/.

## Use

```sh
.venv/bin/jobsearch serve                 # web app on http://127.0.0.1:8765/
.venv/bin/jobsearch fetch [--commit]      # poll boards (or use "Fetch new adverts" in the web inbox); new adverts land in data/jobs/ with status "new"
.venv/bin/jobsearch list --status shortlisted
.venv/bin/jobsearch add-job --company X --title Y --url U < advert.txt
.venv/bin/jobsearch check <job-id>        # fact-check data/jobs/<id>/letter.draft.md
.venv/bin/jobsearch render <job-id>       # check, then PDF + DOCX
.venv/bin/jobsearch log --type application --job-id <id> --method electronic --workload 100%
.venv/bin/jobsearch report 2026-10 --pdf            # monthly URC form summary in Italian (--lang de for the German RAV form) into data/reports/
.venv/bin/python -m unittest discover tests
.venv/bin/ruff check jobsearch tests      # lint (pip install -e ".[dev]" for ruff)
```

With Claude Code in this directory: `/find-jobs`, `/add-job`, `/cover-letter <id>`, `/log-effort`.

## Letter tags

Each sentence of `letter.draft.md` ends with at least one tag:

| Tag | Meaning | Checked against |
|---|---|---|
| `[F:ID]`, `[F:ID1,ID2]` | claim about you | `data/profile/facts.yaml` entries |
| `[J]` | statement about the job/company | the job's `posting.md` |
| `[P]` | your own preference or motivation | the job's `notes.md` |

The checker fails a draft for: untagged sentences, unknown fact IDs, any number not present in the
cited source, banned phrases, and guarded verbs (led, architected, designed...) not supported by the
cited fact. It warns about facts still marked `needs-confirmation`. Tags are stripped when rendering.
