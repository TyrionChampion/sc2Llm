#!/usr/bin/env bash
set -e
ARENA_CLIENT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ARENA_CLIENT_ROOT"
if [ -x "$ARENA_CLIENT_ROOT/.venv/bin/python" ]; then
  ARENA_CLIENT_PYTHON="$ARENA_CLIENT_ROOT/.venv/bin/python"
else
  ARENA_CLIENT_PYTHON="$ARENA_CLIENT_ROOT/.venv311/bin/python"
fi
exec "$ARENA_CLIENT_ROOT/run_sc2.sh" "$ARENA_CLIENT_PYTHON" -X utf8 "$ARENA_CLIENT_ROOT/arena_client.py" "$@"
