#!/bin/sh
set -eu

if ! command -v python3 >/dev/null 2>&1; then
  echo "rapp-infrastructure-city requires Python 3.9+." >&2
  exit 1
fi

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ -f "$HERE/install.py" ]; then
  python3 "$HERE/install.py" "$@"
  exit $?
fi

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
curl -fsSL \
  https://github.com/kody-w/rapp-infrastructure-city/archive/refs/heads/main.tar.gz \
  -o "$TMP/repo.tar.gz"
tar -xzf "$TMP/repo.tar.gz" -C "$TMP"
python3 "$TMP/rapp-infrastructure-city-main/install.py" "$@"
