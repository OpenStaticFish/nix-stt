#!/usr/bin/env sh
# Run a dev waybar (bottom of screen) wired to this repo's nix-tts binary.
# Safe: only kills the dev instance, never your main bar.
set -e
cd "$(dirname "$0")/.."

pkill -f 'waybar .*dev/waybar/config.jsonc' 2>/dev/null || true
sleep 0.3

# load OPENROUTER_API_KEY from .env so waybar (and its children) inherit it
if [ -f ./.env ]; then
  set -a
  . ./.env
  set +a
fi
export NIX_TTS_CONFIG="$PWD/config.toml"

exec waybar -c dev/waybar/config.jsonc -s dev/waybar/style.css
