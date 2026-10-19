#!/usr/bin/env bash
# Set up if needed, start the web app and open it in the browser. Stop with Ctrl-C.
#
#   ./run.sh                 # http://127.0.0.1:8765/
#   ./run.sh --port 9000     # another port
set -euo pipefail

cd "$(dirname "$0")"
port=8765
if [[ "${1:-}" == "--port" ]]; then port="${2:?--port needs a number}"; fi
url="http://127.0.0.1:$port/"

./setup.sh

up() { .venv/bin/python -c "import urllib.request as u; u.urlopen('$url', timeout=1)" 2>/dev/null; }
open_browser() {
    if command -v xdg-open >/dev/null; then xdg-open "$url" >/dev/null 2>&1 &
    elif command -v open >/dev/null; then open "$url"
    else echo "Open $url in your browser."; fi
}

if up; then
    echo
    echo "The app is already running at $url - opening it."
    open_browser
    exit 0
fi

echo
echo "==> Starting the app at $url (Ctrl-C to stop)"
.venv/bin/jobsearch serve --port "$port" &
server=$!
trap 'kill "$server" 2>/dev/null || true' EXIT INT TERM

for _ in $(seq 1 60); do   # wait up to ~15s for it to answer
    if up; then open_browser; break; fi
    if ! kill -0 "$server" 2>/dev/null; then echo "The app failed to start."; exit 1; fi
    sleep 0.25
done
up || echo "The app is slow to start - open $url when it's ready."

wait "$server"
