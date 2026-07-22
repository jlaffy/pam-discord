#!/usr/bin/env bash
set -euo pipefail

repo_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
cd "$repo_dir"

if ! command -v python3 >/dev/null 2>&1; then
  echo "Pam needs Python 3.11 or newer, but python3 was not found." >&2
  exit 1
fi

python3 - <<'PY'
import sys

if sys.version_info < (3, 11):
    raise SystemExit(
        f"Pam needs Python 3.11 or newer; found {sys.version.split()[0]}"
    )
PY

echo "Installing Pam in $repo_dir/.venv"
python3 -m venv .venv
PIP_DISABLE_PIP_VERSION_CHECK=1 .venv/bin/python -m pip install --quiet -e .
mkdir -p "$HOME/.local/bin"
ln -sfn "$repo_dir/pam" "$HOME/.local/bin/pam"

if command -v codex >/dev/null 2>&1; then
  if ! codex plugin marketplace list --json 2>/dev/null | grep -q '"name": "pam-discord"'; then
    codex plugin marketplace add "$repo_dir"
  fi
  codex plugin add pam@pam-discord
fi

echo
echo "Pam is installed. Continue with:"
echo "  ./pam setup"
