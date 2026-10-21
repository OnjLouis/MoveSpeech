#!/bin/sh
set -eu

if ! command -v python3 >/dev/null 2>&1; then
    printf '%s\n' 'Move Speech needs Python 3, which is normally included on Move.' >&2
    exit 1
fi

run_installer() {
    if [ "$0" = /data/onj-move-accessibility/install.sh ]; then
        exec python3 -E -s -B /data/onj-move-accessibility/install.py "$@"
    fi
    printf '%s\n' "Security note: downloading this script with Move's wget trusts your network; wget does not verify HTTPS certificates." >&2
    exec python3 -E -s -B -c 'import urllib.request;exec(urllib.request.urlopen("https://raw.githubusercontent.com/OnjLouis/MoveSpeech/main/bootstrap.py",timeout=30).read())' "$@"
}

# A piped shell script occupies stdin; confirmations must use the SSH terminal.
if [ -t 0 ]; then
    run_installer "$@"
elif ( : </dev/tty ) 2>/dev/null; then
    run_installer "$@" </dev/tty
else
    run_installer "$@" </dev/null
fi
