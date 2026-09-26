# nix-stt

Push-to-talk speech-to-text for Linux, powered by [OpenRouter](https://openrouter.ai).
Click a waybar button to record from your mic, click again to stop — the transcript is
copied to your clipboard, written to a file, and a notification shows the cost.

```
 ready  →   recording  →   finalizing/transcribing  →   done (copied)  /   error
```

## How it works

A tiny Rust binary drives a detached `ffmpeg` process and a state file at
`$XDG_STATE_HOME/nix-stt/state.json` (~/.local/state/nix-stt/). Waybar polls
`nix-stt status` once a second and renders the icon/state; the click handler runs
`nix-stt toggle`.

| Command | What it does |
| --- | --- |
| `nix-stt start` | Start recording (detached ffmpeg, survives waybar restarts) |
| `nix-stt stop` | Stop, transcribe via OpenRouter, copy to clipboard, notify |
| `nix-stt toggle` | Start if idle, stop if recording — the button target |
| `nix-stt status` | Print one line of waybar JSON (poll with `interval: 1`) |
| `nix-stt transcribe <file>` | One-shot transcription of an existing audio file |

## Requirements

- Linux with PipeWire/PulseAudio (or ALSA) — recording goes through ffmpeg
- `ffmpeg` / `ffprobe` on PATH
- `wl-copy` (wl-clipboard) for clipboard, `dunstify` (dunst) for notifications —
  both optional; missing tools never fail a transcription
- A Waybar font containing the status glyphs, such as `Font Awesome 7 Free`

## Install

### Home Manager module (recommended)

```nix
# flake.nix
{
  inputs = {
    nix-stt.url = "github:OpenStaticFish/nix-stt";
  };
}
```

```nix
# anywhere in your home-manager config
imports = [ inputs.nix-stt.homeManagerModules.nix-stt ];

programs.nix-stt = {
  enable = true;
  settings = {
    model = "mistralai/voxtral-small-24b-2507-stt";
    price_per_second = 0.00005;
    output_file = "~/.local/state/nix-stt/output.txt";
  };
};
```

### Package only

```nix
home.packages = [ inputs.nix-stt.packages.${system}.default ];
```

and write your own `~/.config/nix-stt/config.toml`.

### NixOS (system-wide)

```nix
environment.systemPackages = [ inputs.nix-stt.packages.${pkgs.system}.default ];
```

### Upgrading from nix-tts

Rename your flake input and Home Manager option from `nix-tts` to `nix-stt`,
update any Waybar commands to `nix-stt`, and change `NIX_TTS_CONFIG` /
`NIX_TTS_STATE` to `NIX_STT_CONFIG` / `NIX_STT_STATE` if you use them.
Move your existing `~/.config/nix-tts/` (including `.env`) to
`~/.config/nix-stt/`. If you want to keep previous transcripts or state,
move `~/.local/state/nix-tts/` to `~/.local/state/nix-stt/` after stopping
any active recording.

## API key

Keep the key outside your Nix expressions and outside this repository. The
recommended installed setup is:

```text
~/.config/nix-stt/.env
```

```env
OPENROUTER_API_KEY=sk-or-v1-...
```

Secure the directory and file:

```sh
chmod 700 ~/.config/nix-stt
chmod 600 ~/.config/nix-stt/.env
```

Get a key at <https://openrouter.ai/keys>. The binary checks the process
environment first, then `~/.config/nix-stt/.env`, then a `.env` next to the
selected config file or in the CWD. The key does not need to be exported to
every process in your desktop session.

## Configuration

Lookup order: `$NIX_STT_CONFIG` → `~/.config/nix-stt/config.toml` → `./config.toml`.

```toml
model = "mistralai/voxtral-small-24b-2507-stt"  # any OpenRouter STT model
# language = "en"              # optional ISO-639-1, omit for auto-detect
response_format = "json"       # or "verbose_json" for segments/timestamps
api_base = "https://openrouter.ai/api/v1"
timeout_secs = 120
# output_file = "output.txt"   # relative → $HOME; default: state dir / output.txt
price_per_second = 0.00005     # optional: enables the cost log + notification

[recording]
input = "default"        # ffmpeg source; "default" = system default mic
input_format = "pulse"   # "pulse" (PipeWire/PulseAudio) or "alsa"
sample_rate = 16000
channels = 1
```

## Waybar setup

Add a custom module to your waybar config (in your own home-manager/NixOS config):

```jsonc
"custom/dictation": {
  "return-type": "json",
  "format": "{}",
  "exec": "nix-stt status",
  "interval": 1,
  "on-click": "nix-stt toggle",
  "tooltip": true
}
```

and add `"custom/dictation"` to your `modules-left`/`modules-right`. Then style
by state class:

```css
#custom-dictation {
  background-color: #282828;
  border-radius: 24px;
  color: #e5809e;
  font-family: "Font Awesome 7 Free", "Font Awesome 7 Brands", "Iosevka", sans-serif;
  font-size: 15px;
  font-weight: 900;
  margin: 5px 10px 5px 0;
  min-width: 24px;
  padding: 4px 20px;
}
#custom-dictation.recording {
  background-color: rgba(247, 118, 142, 0.16);
  color: #f7768e;
}
#custom-dictation.finalizing,
#custom-dictation.transcribing {
  background-color: rgba(224, 175, 104, 0.14);
  color: #e0af68;
}
#custom-dictation.done {
  background-color: rgba(158, 206, 106, 0.12);
  color: #9ece6a;
}
#custom-dictation.error {
  background-color: rgba(247, 118, 142, 0.16);
  color: #f7768e;
}
```

The binary emits theme-matching Font Awesome state icons, while the tooltip contains the elapsed time,
transcript preview, and estimated cost.

### Local dev bar

This repository includes a standalone bottom-of-screen Waybar example:

```sh
nix build .#default
./dev/run-waybar.sh
```

It uses the local `result/bin/nix-stt` build and `config.toml`. Stop only the
dev instance with:

```sh
pkill -f '^waybar .*dev/waybar/config.jsonc'
```

## Files

- `~/.local/state/nix-stt/state.json` — current phase (recording/finalizing/transcribing/...)
- `~/.local/state/nix-stt/recording.wav` — last recording (16 kHz mono PCM WAV)
- output file — transcript (default `~/.local/state/nix-stt/output.txt`)

## Troubleshooting

- **`failed to start ffmpeg`** — add `ffmpeg` to `home.packages`/`environment.systemPackages`.
- **Icon stuck on ** — recording finalization times out after 30 seconds and
  transcription times out after 3 minutes; check the tooltip or run `nix-stt stop`
  in a terminal to see the API error.
- **No audio captured** — check `wpctl status` that your mic is the default
  source, or set `[recording] input` to a specific pulse device name.
- **401 from OpenRouter** — check `~/.config/nix-stt/.env` and its file permissions.

## Development

```sh
nix develop        # or just use your system rust
cargo run -- status
cargo test
```
