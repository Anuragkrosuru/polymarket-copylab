#!/bin/zsh
set -eu
cd "$(dirname "$0")/.."
btc_python="$HOME/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3"
if [[ ! -x "$btc_python" ]]; then
  btc_python=python3
fi
if ! "$btc_python" -c 'import cryptography' >/dev/null 2>&1; then
  echo 'Install the optional quote-signing dependency with: python3 -m pip install ".[robinhood]"'
  exit 1
fi
"$btc_python" -m copylab.btc15 --connect
