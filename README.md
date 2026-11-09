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
response_format = "json"       # "verbose_json" may return metadata, but nix-stt currently uses only text
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

## Long recordings and upload limits

OpenRouter documents a [25 MB multipart file limit](https://openrouter.ai/docs/api/api-reference/stt/create-transcription).
For both `stop` and `transcribe`, nix-stt conservatively accepts up to
**25,000,000 audio-file bytes**; larger files are rejected locally before upload.
This is about 13 minutes of 16 kHz mono 16-bit PCM WAV, including the WAV header.
Different sample rates, channel counts, codecs and headers change that duration.
An accepted size is not a guarantee: models, providers, intermediaries or custom
`api_base` servers can impose lower size, duration or processing-time limits.
OpenRouter notes that upstream requests can time out after 60 seconds.

The recording is not modified on failure, but **starting another recording
overwrites `recording.wav`**. Copy it elsewhere first if you need to keep it.
To recover, split the original into shorter files and transcribe each separately,
or compress it to a format supported by your selected model and provider. For
example, if MP3 is supported, this creates a separate 64 kbps file:

```sh
ffmpeg -i recording.wav -c:a libmp3lame -b:a 64k recording-compressed.mp3
nix-stt transcribe recording-compressed.mp3 --output transcript.txt
```

Check the resulting file size; compression does not guarantee it will fit or
meet the provider's duration limit, and may reduce accuracy. Splitting at speech
boundaries can lose words or context; overlap can duplicate words and increase
cost. Transcribe parts in order with distinct output filenames and reconcile them
manually. Each request can be billed separately; the configured cost estimate is
duration-based and may not match actual provider billing. nix-stt uses only the
returned text, not timestamps, and does not align or merge chunk timestamps.

The [OpenRouter STT guide](https://openrouter.ai/docs/guides/overview/multimodal/stt)
documents larger base64 JSON uploads for OpenAI and Groq models, but says other
providers reject them above the multipart cap; URL uploads also require provider
support and a reachable URL. nix-stt does not automatically switch formats,
upload by URL, compress, or chunk: routing and supported formats vary, and
automatic retries or chunking could incur unexpected charges or alter results.

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
nix develop
cargo run -- status
cargo fmt --all -- --check
cargo clippy --locked --all-targets -- -D warnings
cargo test --locked
cargo build --locked
python3 -m unittest discover -s tests -v
```

The integration tests use temporary directories, synthetic audio, a local mock API,
and fake desktop commands; they do not use a microphone or an OpenRouter key.
See [CONTRIBUTING.md](CONTRIBUTING.md) for the Nix and Home Manager checks,
guidance for adding tests, and the separately triggered paid live smoke test.
