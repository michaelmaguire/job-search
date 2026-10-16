# job-search

Personal job-search system, run as a **local web app** (http://127.0.0.1:8765/, on your own computer only)
where you do everything - with or without Claude:

- **fetch** job adverts from company boards (Greenhouse, Lever, Ashby, SmartRecruiters, Workday, SuccessFactors,
  Recruitee, Personio) and swissdevjobs.ch, saving the full text before adverts disappear;
- **fact-checked cover letters** - every claim is tagged with an entry from a curated evidence
  base and checked mechanically (numbers, verbs, banned puffery) before rendering to PDF/DOCX;
- **effort records** with the fields of the Swiss RAV form 716.007, plus a monthly report in that layout, in German (RAV) or Italian (URC);
- **search curation** - sources, blocked companies, salary and commute filters, recruiter conversations.

The core steps (fetch, add, check, render, log, report) also work on the command line (`jobsearch ...`), and Claude Code
in this directory can drive them too.

Your records (adverts, letters, CV, effort log) live in `data/`, a git submodule pointing at your own
**private** repository; this code repository holds no records. [Setup](#setup) creates that repository.

## Setup

The `data/` submodule's URL is relative (`../job-search-data.git`): it resolves to a `job-search-data`
repository next to whichever `job-search` you cloned. So: fork the code, create your own private
`job-search-data` beside your fork from the starter template, and the submodule finds it.

1. **Fork** this repository on GitHub, and create an **empty private** repository for your records
   named `<you>/job-search-data`. Keep it private: it will hold your CV, letters, salary
   expectations and the RAV/URC effort log.
2. **Clone your fork without the submodule** (your data repository is still empty):
   ```sh
   git clone git@github.com:<you>/job-search.git
   cd job-search
   ```
3. **Fill your data repository from the starter template** and push it:
   ```sh
   cp -r examples/data-template /tmp/job-search-data && cd /tmp/job-search-data
   git init -b main && git add -A && git commit -m "Start from the job-search template"
   git remote add origin git@github.com:<you>/job-search-data.git && git push -u origin main
   cd -
   ```
4. **Check out your data repository** as the submodule and record its commit in your fork:
   ```sh
   git submodule update --init --remote data
   git add data && git commit -m "Use my own data repository" && git push
   ```
   (Named it something else? `git submodule set-url data <its url>` first, and commit `.gitmodules` too.)
5. **Install** (Python 3.10+). Rendering letters and PDF reports also needs LibreOffice (`soffice`) on the PATH;
   saving advert pages as PDF needs Google Chrome or Chromium.
   ```sh
   python3 -m venv .venv && .venv/bin/pip install -e .
   ```
6. **Start the web app** (see [below](#start-the-web-app)).

Once your fork points at your own data repository, setting up another computer is just
`git clone --recurse-submodules git@github.com:<you>/job-search.git` followed by step 5.

### Make it yours

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

## Start the web app

```sh
cd ~/code/job-search && .venv/bin/jobsearch serve
```
Then open http://127.0.0.1:8765/ (local only; stop with Ctrl-C).

## Browser version

`web/` runs this same app entirely in your browser: no install, and no server holding your data. It's
published to GitHub Pages by `.github/workflows/pages.yml` (enable it once under Settings > Pages > Source:
GitHub Actions) and works alongside the local app. Nothing above changes.

- **Your data** is imported once as a git bundle (`git -C data bundle create ~/jobsearch.bundle --all`). It's kept
  in the browser as one blob encrypted with your passphrase (AES-GCM, PBKDF2) and decrypted only in the open tab.
  There's no recovery without the passphrase, so keep your git repo as the backup.
- **The same pages:** a Web Worker runs the `jobsearch` web app itself (Flask, the same routes and templates) in
  [Pyodide](https://pyodide.org). Pages look and behave as in the local app and write the same files. Git runs
  via [isomorphic-git](https://isomorphic-git.org).
- **Data** (in the header) is where you commit, export a bundle (`git clone jobsearch.bundle`, or `git pull` from it
  into your clone and push), import another one, and set your commit name and the name and AHV number for the
  URC/RAV form. You can also edit `profile/` and `search/` files and add files such as your CV to `raw/`.
- **What differs from the local app:**
  - **Fetching:** only reaches boards that allow browser access (Greenhouse, Lever, Ashby, SmartRecruiters,
    swissdevjobs). The others are listed as source errors.
  - **Letters:** rendered without LibreOffice, so they look close to, but not exactly like, the local app's.
  - **Gmail:** there are no Gmail drafts. **Write in Gmail** opens a new message for you to attach the files to.
  - **Local app only:** Claude drafting and advert-page PDFs.
- **Libraries:** Pyodide, isomorphic-git and the Python packages load at pinned versions from jsdelivr and PyPI.
  The "Save this job" extension works with it: set the extension's address to the Pages URL.

Try it locally: run `python3 -m http.server 8790` in this folder, then open http://127.0.0.1:8790/web/.

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
