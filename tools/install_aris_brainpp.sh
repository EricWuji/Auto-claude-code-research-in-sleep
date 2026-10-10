#!/usr/bin/env bash
# Install ARIS Codex skills and the optional Brain++ project package.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$SCRIPT_DIR/install_aris_brainpp.py" "$@"
