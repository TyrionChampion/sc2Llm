#!/bin/bash
# SC2 + OSMesa runtime env.
# Mesa 26 removed OSMesa; we supply Debian's libOSMesa.so.8 + libLLVM19.
# PYSC2_OSMESA tells pysc2 to pass -osmesapath explicitly, because
# ldconfig -p ignores LD_LIBRARY_PATH and would silently kill RGB.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export SC2PATH="$HERE/engine/StarCraftII"
export LD_LIBRARY_PATH="$HERE/osmesa-lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export PYSC2_OSMESA="$HERE/osmesa-lib/libOSMesa.so.8"

# --- API keys -------------------------------------------------------------
# Keys live in .env so they survive across shells (a plain `export` does not
# persist between tool calls). An existing environment variable WINS, so a
# one-off override still works. We must snapshot-and-restore because sourcing
# .env would otherwise clobber an already-set (even if blank) variable.
if [ -f "$HERE/.env" ]; then
  __saved_env=""
  for __v in ARENA_PROVIDER \
             ARENA_P1_PROVIDER ARENA_P2_PROVIDER \
             ARENA_P1_API_KEY ARENA_P2_API_KEY ARENA_P1_MODEL ARENA_P2_MODEL \
             OPENAI_API_KEY ANTHROPIC_API_KEY DEEPSEEK_API_KEY \
             MOONSHOT_API_KEY DASHSCOPE_API_KEY ZHIPU_API_KEY GROQ_API_KEY \
             OLLAMA_API_KEY ARENA_MODEL_OPENAI ARENA_MODEL_ANTHROPIC \
             ARENA_MODEL_DEEPSEEK ARENA_MODEL_MOONSHOT ARENA_MODEL_DASHSCOPE \
             ARENA_MODEL_ZHIPU ARENA_MODEL_GROQ ARENA_MODEL_OLLAMA; do
    if [ -n "${!__v+x}" ]; then
      __saved_env="${__saved_env}export ${__v}=$(printf '%q' "${!__v}")"$'\n'
    fi
  done
  set -a
  # shellcheck disable=SC1091
  . "$HERE/.env"
  set +a
  [ -n "$__saved_env" ] && eval "$__saved_env"
  unset __saved_env __v
fi

exec "$@"
