# Shared environment for the Claude Code subscription tooling (sourced, not executed).
#   PIXELWORLD_HOME      operator root (default $HOME/pixelworld_opus) holding
#                        PixelWorld/ (checkout + .venv), configs_claude/, runtime/, locks.json,
#                        inputs/, runs_claude/, runs_claude_fable/, hf_publish/
#   PIXELWORLD_REPO      checkout (default $PIXELWORLD_HOME/PixelWorld)
#   PIXELWORLD_CONFIGS   rendered configs (default $PIXELWORLD_HOME/configs_claude)
PIXELWORLD_HOME="${PIXELWORLD_HOME:-$HOME/pixelworld_opus}"
PIXELWORLD_REPO="${PIXELWORLD_REPO:-$PIXELWORLD_HOME/PixelWorld}"
PIXELWORLD_CONFIGS="${PIXELWORLD_CONFIGS:-$PIXELWORLD_HOME/configs_claude}"
export PIXELWORLD_HOME PIXELWORLD_REPO PIXELWORLD_CONFIGS
cd "$PIXELWORLD_REPO" && . .venv/bin/activate
# The case credential comes from the host Claude Code login (host_subscription_token),
# never from the environment.
unset CLAUDE_CODE_OAUTH_TOKEN ANTHROPIC_API_KEY
export PIXELWORLD_CONTROLLED_RUNTIME="$PIXELWORLD_HOME/runtime"
export PIXELWORLD_CONTROLLED_LOCKS="$PIXELWORLD_HOME/locks.json"
