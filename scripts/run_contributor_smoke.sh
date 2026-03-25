#!/usr/bin/env bash
set -euo pipefail

# Contributor smoke matrix (single-script PASS/FAIL)
# 1) pip install -e . (clean venv)
# 2) pytest exits 0
# 3) core-memory doctor exits 0 (fresh initialized root)
# 4) examples/pydanticai_basic.py runs without error

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

VENV_DIR="${SMOKE_VENV_DIR:-$ROOT_DIR/.venv-smoke}"
rm -rf "$VENV_DIR"
python3 -m venv "$VENV_DIR"
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
python -m pip install --upgrade pip >/dev/null

echo "[smoke] CHECK 1/4: pip install -e ."
pip install -e . >/dev/null
echo "[smoke] PASS install"

echo "[smoke] CHECK 2/4: pytest"
pytest -q >/dev/null
echo "[smoke] PASS pytest"

echo "[smoke] CHECK 3/4: core-memory doctor"
SMOKE_ROOT="$(mktemp -d)"
python - <<'PY'
import os
from core_memory.persistence.store import MemoryStore
from core_memory.persistence.rolling_record_store import write_rolling_records
r = os.environ['SMOKE_ROOT']
MemoryStore(root=r)  # initialize .beads + index
write_rolling_records(r, records=[], meta={"source":"smoke"}, included_bead_ids=[], excluded_bead_ids=[])
PY
core-memory --root "$SMOKE_ROOT" doctor >/dev/null
echo "[smoke] PASS doctor"

echo "[smoke] CHECK 4/4: examples/pydanticai_basic.py"
PYTHONPATH=. python3 examples/pydanticai_basic.py >/dev/null
echo "[smoke] PASS example"

echo "[smoke] ALL PASS"
