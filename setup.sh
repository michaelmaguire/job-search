#!/usr/bin/env bash
# Set up (or repair) a local checkout: records in data/, Python venv, optional tools.
# Safe to re-run any time, e.g. after cloning, pulling, or moving the folder.
#
#   ./setup.sh                    # set up records + install the app
#   ./setup.sh --dev              # also install dev tools (ruff, fpdf2 for tests)
#   ./setup.sh --data-url URL     # use this records repository instead of the default
#
# Records live in a separate private repository. By default it's job-search-data next to
# wherever you cloned this from (git@github.com:<you>/job-search.git ->
# git@github.com:<you>/job-search-data.git). If that repository is empty, it is filled
# from examples/data-template and pushed (after asking).
set -euo pipefail

cd "$(dirname "$0")"
here="$PWD"
extras=""
data_url=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --dev) extras="[dev]" ;;
        --data-url) data_url="${2:?--data-url needs a URL}"; shift ;;
        -h|--help) sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "unknown option: $1 (see --help)"; exit 2 ;;
    esac
    shift
done

step() { printf '\n==> %s\n' "$*"; }
warn() { printf '    warning: %s\n' "$*"; }
ask() {  # ask "question" -> 0 for yes
    local reply=""
    read -r -p "    $1 [y/N] " reply || true
    [[ "$reply" == [yY]* ]]
}

# 1. Records: data/, on a branch so the app can commit to it.
step "Records (data/)"
if [[ ! -e data/.git ]]; then
    if [[ -n "$data_url" ]]; then
        git submodule set-url data "$data_url"   # changes .gitmodules: commit it in your fork
    fi
    # Resolves the relative URL in .gitmodules against this repo's origin remote.
    git submodule init -q data
    url="$(git config submodule.data.url)"
    echo "    records repository: $url"

    if ! refs="$(git ls-remote --heads "$url" 2>/dev/null)"; then
        echo
        echo "    Can't reach $url."
        echo "    Create it as an EMPTY PRIVATE repository (it will hold your CV, letters and"
        echo "    salary expectations), then run ./setup.sh again. With the GitHub CLI:"
        echo "        gh repo create $(sed -E 's#^.*[:/]([^/]+/[^/]+)$#\1#; s#\.git$##' <<<"$url") --private"
        echo "    Or point at another repository: ./setup.sh --data-url <url>"
        exit 1
    fi

    rmdir data 2>/dev/null || true   # the empty placeholder git leaves for a submodule
    if [[ -n "$refs" ]]; then
        # Clone the latest records rather than the commit recorded in this repo: in a
        # fork, that commit belongs to someone else's records and doesn't exist in yours.
        git clone -q "$url" data
        echo "    cloned your records"
    else
        echo "    $url is empty."
        if ask "Fill it from the starter template (examples/data-template) and push?"; then
            cp -r examples/data-template data
            git -C data init -q -b main
            git -C data add -A
            git -C data commit -q -m "Start from the job-search template"
            git -C data remote add origin "$url"
            git -C data push -q -u origin main
            echo "    created your records from the template and pushed them"
        else
            echo "    Left alone. Run ./setup.sh again when you want to set up your records."
            mkdir -p data
            exit 1
        fi
    fi
fi
if ! git -C data symbolic-ref -q HEAD >/dev/null; then
    # Detached HEAD (normal straight after a submodule checkout): move to main
    # if that loses nothing.
    if git -C data merge-base --is-ancestor HEAD main 2>/dev/null; then
        git -C data checkout -q main
    else
        warn "data/ is on a detached HEAD not contained in main - left as is."
        warn "Check out a branch yourself: git -C data checkout main"
    fi
fi
if git -C data symbolic-ref -q HEAD >/dev/null; then
    if [[ -z "$(git -C data status --porcelain)" ]]; then
        git -C data pull -q --ff-only || warn "could not fast-forward data/ - pull it yourself"
    else
        warn "data/ has uncommitted changes - not pulling"
    fi
fi
echo "    data/ is on: $(git -C data rev-parse --abbrev-ref HEAD)"
if [[ -n "$(git status --porcelain -- data)" ]]; then
    echo "    (git status shows data/ as changed: this repo records a different records commit."
    echo "     Harmless; commit it in your fork if you like: git add data && git commit -m 'Update records')"
fi

# 2. Python virtualenv in .venv
step "Python environment (.venv)"
python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' \
    || { echo "Python 3.10+ is needed (found $(python3 --version))"; exit 1; }
if [[ -d .venv ]]; then
    # A venv's scripts have its absolute path baked in, so a moved folder or a
    # removed Python leaves it broken. Rebuild it in that case.
    if [[ "$(head -1 .venv/bin/pip 2>/dev/null)" != "#!$here/.venv/bin/python"* ]] \
        || ! .venv/bin/python -c '' 2>/dev/null; then
        echo "    existing .venv is broken or was made elsewhere - rebuilding it"
        rm -rf .venv
    fi
fi
[[ -d .venv ]] || python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -e ".$extras"
echo "    installed: $(.venv/bin/jobsearch --help >/dev/null && echo jobsearch OK)"

# 3. Optional tools
step "Optional tools"
if command -v soffice >/dev/null; then echo "    LibreOffice: found"
else warn "LibreOffice (soffice) not found - needed to render letters and PDF reports"; fi
if command -v google-chrome >/dev/null || command -v chromium >/dev/null || command -v chromium-browser >/dev/null; then
    echo "    Chrome/Chromium: found"
else warn "Chrome/Chromium not found - needed to save advert pages as PDF"; fi
if command -v claude >/dev/null; then echo "    Claude Code: found"
else warn "Claude Code not found - needed for 'Draft with Claude' (optional)"; fi

step "Setup done."
echo "    Start the app with ./run.sh (or .venv/bin/jobsearch serve, then open http://127.0.0.1:8765/)"
