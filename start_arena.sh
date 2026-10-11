#!/usr/bin/env bash
set -e
ARENA_LAUNCH_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ARENA_LAUNCH_ROOT"
exec "$ARENA_LAUNCH_ROOT/run_sc2.sh" "$ARENA_LAUNCH_ROOT/.venv311/bin/python" "$ARENA_LAUNCH_ROOT/arena_launcher.py" --open "$@"
