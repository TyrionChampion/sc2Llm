#!/usr/bin/env bash
set -e
ARENA_ROOM_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ARENA_ROOM_ROOT"
if [ -x "$ARENA_ROOM_ROOT/.venv/bin/python" ]; then
  ARENA_ROOM_PYTHON="$ARENA_ROOM_ROOT/.venv/bin/python"
else
  ARENA_ROOM_PYTHON="$ARENA_ROOM_ROOT/.venv311/bin/python"
fi
exec "$ARENA_ROOM_ROOT/run_sc2.sh" "$ARENA_ROOM_PYTHON" "$ARENA_ROOM_ROOT/arena_room.py" --open "$@"
