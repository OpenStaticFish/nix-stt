#!/usr/bin/env sh
# Run a dev waybar (bottom of screen) wired to this repo's nix-stt binary.
# Safe: only kills the dev instance, never your main bar.
set -e
cd "$(dirname "$0")/.."

pkill -f 'waybar .*dev/waybar/config.jsonc' 2>/dev/null || true
sleep 0.3

# The binary loads ~/.config/nix-stt/.env itself.
export NIX_STT_CONFIG="$PWD/config.toml"

exec waybar -c dev/waybar/config.jsonc -s dev/waybar/style.css
