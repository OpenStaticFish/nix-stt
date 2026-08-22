use anyhow::{Context, Result, bail};
use clap::{Parser, Subcommand};
use serde::{Deserialize, Serialize};
use std::io::Write as _;
use std::os::unix::process::CommandExt as _;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

const DONE_DECAY_SECS: u64 = 5;
const ERROR_DECAY_SECS: u64 = 10;
const FINALIZING_TIMEOUT_SECS: u64 = 30;
const TRANSCRIBING_TIMEOUT_SECS: u64 = 180;

#[derive(Debug, Deserialize)]
struct Config {
    model: String,
    language: Option<String>,
    response_format: Option<String>,
    api_base: Option<String>,
    timeout_secs: Option<u64>,
    output_file: Option<String>,
    price_per_second: Option<f64>,
    recording: Option<Recording>,
}

#[derive(Debug, Deserialize)]
struct Recording {
    input: Option<String>,
    input_format: Option<String>,
    sample_rate: Option<u32>,
    channels: Option<u16>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct State {
    phase: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pid: Option<u32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    wav: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    started_at: Option<u64>,
    phase_at: u64,
    #[serde(skip_serializing_if = "Option::is_none")]
    error: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    preview: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    cost: Option<f64>,
}

impl Default for State {
    fn default() -> Self {
        Self {
            phase: "idle".into(),
            pid: None,
            wav: None,
            started_at: None,
            phase_at: now_secs(),
            error: None,
            preview: None,
            cost: None,
        }
    }
}

#[derive(Parser)]
#[command(
    name = "nix-tts",
    version,
    about = "Push-to-talk dictation via OpenRouter, wired for waybar"
)]
struct Cli {
    #[arg(short, long, global = true)]
    config: Option<PathBuf>,
    #[command(subcommand)]
    command: Cmd,
}

#[derive(Subcommand)]
enum Cmd {
    #[command(about = "Start recording from the mic (spawns ffmpeg detached)")]
    Start,
    #[command(about = "Stop recording, transcribe, copy to clipboard, notify")]
    Stop {
        #[arg(short, long)]
        output: Option<String>,
    },
    #[command(about = "Start if idle, stop if recording (waybar on-click target)")]
    Toggle {
        #[arg(short, long)]
        output: Option<String>,
    },
    #[command(about = "Print current state as waybar JSON (one line, for interval polling)")]
    Status,
    #[command(about = "Transcribe an existing audio file, one-shot")]
    Transcribe {
        file: PathBuf,
        #[arg(short, long)]
        output: Option<String>,
    },
}

#[derive(Deserialize)]
struct TranscriptionResponse {
    text: String,
}

fn now_secs() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

fn home_dir() -> Option<PathBuf> {
    std::env::var_os("HOME").map(PathBuf::from)
}

fn state_dir() -> PathBuf {
    std::env::var_os("XDG_STATE_HOME")
        .or_else(|| std::env::var_os("NIX_TTS_STATE"))
        .map(PathBuf::from)
        .or_else(|| home_dir().map(|h| h.join(".local/state")))
        .unwrap_or_else(|| PathBuf::from("/tmp"))
        .join("nix-tts")
}

fn state_file() -> PathBuf {
    state_dir().join("state.json")
}

fn read_state() -> Option<State> {
    let raw = std::fs::read_to_string(state_file()).ok()?;
    serde_json::from_str(&raw).ok()
}

fn write_state(state: &State) -> Result<()> {
    let dir = state_dir();
    std::fs::create_dir_all(&dir).with_context(|| format!("failed to create {}", dir.display()))?;
    std::fs::write(state_file(), serde_json::to_string_pretty(state)?)
        .with_context(|| format!("failed to write {}", state_file().display()))?;
    Ok(())
}

fn pid_alive(pid: u32) -> bool {
    unsafe { libc::kill(pid as libc::pid_t, 0) == 0 }
}

fn effective_state(state: Option<State>, now: u64) -> State {
    let Some(mut s) = state else {
        return State::default();
    };
    match s.phase.as_str() {
        "recording" => {
            if s.pid.map(pid_alive).unwrap_or(false) {
                s
            } else {
                s.phase = "error".into();
                s.error = Some("recording process died".into());
                s.phase_at = now;
                s
            }
        }
        "finalizing" => {
            if now.saturating_sub(s.phase_at) > FINALIZING_TIMEOUT_SECS {
                s.phase = "error".into();
                s.error = Some("recording finalization timed out".into());
                s.phase_at = now;
                s
            } else {
                s
            }
        }
        "transcribing" => {
            if now.saturating_sub(s.phase_at) > TRANSCRIBING_TIMEOUT_SECS {
                s.phase = "error".into();
                s.error = Some("transcription timed out".into());
                s.phase_at = now;
                s
            } else {
                s
            }
        }
        "done" => {
            if now.saturating_sub(s.phase_at) > DONE_DECAY_SECS {
                State::default()
            } else {
                s
            }
        }
        "error" => {
            if now.saturating_sub(s.phase_at) > ERROR_DECAY_SECS {
                State::default()
            } else {
                s
            }
        }
        _ => s,
    }
}

fn preview(text: &str) -> String {
    let one_line: String = text
        .chars()
        .map(|c| if c == '\n' { ' ' } else { c })
        .collect();
    one_line.chars().take(80).collect()
}

fn status() {
    let s = effective_state(read_state(), now_secs());
    let (text, class, tooltip) = match s.phase.as_str() {
        "recording" => {
            let secs = s
                .started_at
                .map(|t| now_secs().saturating_sub(t))
                .unwrap_or(0);
            (
                "\u{f111}",
                "recording",
                format!("Recording {secs}s — click to stop"),
            )
        }
        "finalizing" => ("\u{f110}", "finalizing", "Finishing recording…".to_string()),
        "transcribing" => ("\u{f110}", "transcribing", "Transcribing…".to_string()),
        "done" => {
            let p = s.preview.unwrap_or_default();
            let cost = s.cost.map(|c| format!(" (${c:.6})")).unwrap_or_default();
            (
                "\u{f00c}",
                "done",
                format!("{p}{cost} — copied to clipboard"),
            )
        }
        "error" => (
            "\u{f071}",
            "error",
            s.error.unwrap_or_else(|| "error".into()),
        ),
        _ => ("\u{f130}", "idle", "Click to start recording".to_string()),
    };
    println!(
        "{}",
        serde_json::json!({ "text": text, "class": class, "tooltip": tooltip })
    );
}

fn expand_tilde(path: &str) -> PathBuf {
    if let Some(rest) = path.strip_prefix("~/") {
        home_dir()
            .map(|h| h.join(rest))
            .unwrap_or_else(|| PathBuf::from(path))
    } else {
        PathBuf::from(path)
    }
}

fn resolve_output(cli: Option<&str>, cfg: Option<&String>, default: PathBuf) -> PathBuf {
    let raw = cli.map(str::to_string).or_else(|| {
        cfg.map(|c| {
            c.replace(
                "~",
                &home_dir()
                    .map(|h| h.to_string_lossy().into_owned())
                    .unwrap_or_default(),
            )
        })
    });
    match raw {
        Some(p) => {
            let pb = expand_tilde(&p);
            if pb.is_relative() {
                home_dir().unwrap_or_default().join(pb)
            } else {
                pb
            }
        }
        None => default,
    }
}

fn find_config(cli: Option<&Path>) -> Result<PathBuf> {
    if let Some(p) = cli {
        if p.exists() {
            return Ok(p.to_path_buf());
        }
        bail!("config file {} not found", p.display());
    }
    if let Ok(p) = std::env::var("NIX_TTS_CONFIG") {
        let p = PathBuf::from(p);
        if p.exists() {
            return Ok(p);
        }
        bail!("$NIX_TTS_CONFIG ({}) does not exist", p.display());
    }
    if let Some(home) = home_dir() {
        let p = home.join(".config/nix-tts/config.toml");
        if p.exists() {
            return Ok(p);
        }
    }
    let p = PathBuf::from("config.toml");
    if p.exists() {
        return Ok(p);
    }
    bail!(
        "no config found: set $NIX_TTS_CONFIG, create ~/.config/nix-tts/config.toml, \
         or run from a directory containing config.toml"
    )
}

fn load_config(cli: Option<&Path>) -> Result<Config> {
    let path = find_config(cli)?;
    if let Some(home) = home_dir() {
        dotenvy::from_path(home.join(".config/nix-tts/.env")).ok();
    }
    if let Some(dir) = path.parent() {
        dotenvy::from_path(dir.join(".env")).ok();
    }
    dotenvy::dotenv().ok();
    let raw = std::fs::read_to_string(&path)
        .with_context(|| format!("failed to read config file {}", path.display()))?;
    toml::from_str(&raw).with_context(|| format!("failed to parse {}", path.display()))
}

fn api_key() -> Result<String> {
    std::env::var("OPENROUTER_API_KEY")
        .context("OPENROUTER_API_KEY is not set (put it in ~/.config/nix-tts/.env or .env next to the config)")
}

fn notify(summary: &str, body: &str) {
    let _ = Command::new("dunstify")
        .arg(summary)
        .arg(body)
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn();
}

fn copy_to_clipboard(text: &str) {
    let Ok(mut child) = Command::new("wl-copy")
        .stdin(Stdio::piped())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
    else {
        return;
    };
    if let Some(mut stdin) = child.stdin.take() {
        let _ = stdin.write_all(text.as_bytes());
    }
    let _ = child.wait();
}

fn audio_duration_seconds(path: &Path) -> Result<f64> {
    let out = Command::new("ffprobe")
        .arg("-v")
        .arg("error")
        .arg("-show_entries")
        .arg("format=duration")
        .arg("-of")
        .arg("default=noprint_wrappers=1:nokey=1")
        .arg(path)
        .output()
        .context("failed to run ffprobe (is ffmpeg installed?)")?;
    let s = String::from_utf8_lossy(&out.stdout).trim().to_string();
    s.parse::<f64>()
        .with_context(|| format!("could not determine duration of {}", path.display()))
}

async fn transcribe(config: &Config, api_key: &str, path: &Path) -> Result<String> {
    let audio = std::fs::read(path)
        .with_context(|| format!("failed to read audio file {}", path.display()))?;

    let file_name = path
        .file_name()
        .map(|n| n.to_string_lossy().into_owned())
        .unwrap_or_else(|| "audio.wav".to_string());

    let api_base = config
        .api_base
        .clone()
        .unwrap_or_else(|| "https://openrouter.ai/api/v1".to_string());
    let url = format!("{}/audio/transcriptions", api_base.trim_end_matches('/'));

    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(config.timeout_secs.unwrap_or(120)))
        .build()?;

    let mut form = reqwest::multipart::Form::new()
        .text("model", config.model.clone())
        .part(
            "file",
            reqwest::multipart::Part::bytes(audio).file_name(file_name),
        );

    if let Some(language) = &config.language {
        form = form.text("language", language.clone());
    }
    if let Some(response_format) = &config.response_format {
        form = form.text("response_format", response_format.clone());
    }

    let response = client
        .post(&url)
        .bearer_auth(api_key)
        .multipart(form)
        .send()
        .await
        .context("request to OpenRouter failed")?;

    let status = response.status();
    let body = response.text().await?;

    if !status.is_success() {
        bail!("OpenRouter returned HTTP {status}: {body}");
    }

    let parsed: TranscriptionResponse =
        serde_json::from_str(&body).context("failed to parse transcription response")?;

    Ok(parsed.text)
}

fn start(config: &Config) -> Result<()> {
    if let Some(s) = read_state() {
        match s.phase.as_str() {
            "recording" if s.pid.map(pid_alive).unwrap_or(false) => {
                println!("already recording");
                return Ok(());
            }
            "transcribing" => bail!("transcription in progress"),
            "finalizing" => bail!("recording finalization in progress"),
            _ => {}
        }
    }

    let dir = state_dir();
    std::fs::create_dir_all(&dir).with_context(|| format!("failed to create {}", dir.display()))?;
    let wav = dir.join("recording.wav");

    let rec = config.recording.as_ref();
    let input = rec
        .and_then(|r| r.input.clone())
        .unwrap_or_else(|| "default".to_string());
    let input_format = rec
        .and_then(|r| r.input_format.clone())
        .unwrap_or_else(|| "pulse".to_string());
    let sample_rate = rec.and_then(|r| r.sample_rate).unwrap_or(16000);
    let channels = rec.and_then(|r| r.channels).unwrap_or(1);

    let child = Command::new("ffmpeg")
        .arg("-y")
        .arg("-hide_banner")
        .arg("-loglevel")
        .arg("error")
        .arg("-f")
        .arg(&input_format)
        .arg("-i")
        .arg(&input)
        .arg("-ar")
        .arg(sample_rate.to_string())
        .arg("-ac")
        .arg(channels.to_string())
        .arg("-c:a")
        .arg("pcm_s16le")
        .arg(&wav)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .process_group(0)
        .spawn()
        .context("failed to start ffmpeg — is it installed and on PATH?")?;

    let pid = child.id();
    write_state(&State {
        phase: "recording".into(),
        pid: Some(pid),
        wav: Some(wav.to_string_lossy().into_owned()),
        started_at: Some(now_secs()),
        ..State::default()
    })?;

    println!("recording started (pid {pid})");
    Ok(())
}

async fn stop(config: &Config, output: Option<String>) -> Result<()> {
    let mut s = read_state().context("no recording in progress")?;
    if s.phase != "recording" {
        bail!("not recording (phase: {})", s.phase);
    }
    let pid = s.pid.context("state has no pid")?;

    if !pid_alive(pid) {
        s.phase = "error".into();
        s.error = Some("recording process died".into());
        s.phase_at = now_secs();
        write_state(&s)?;
        bail!("recording process is not running");
    }

    s.phase = "finalizing".into();
    s.phase_at = now_secs();
    write_state(&s)?;

    if unsafe { libc::kill(pid as libc::pid_t, libc::SIGINT) } != 0 {
        s.phase = "error".into();
        s.error = Some("failed to stop recording process".into());
        s.phase_at = now_secs();
        write_state(&s)?;
        bail!("failed to stop recording process");
    }

    for _ in 0..100 {
        if !pid_alive(pid) {
            break;
        }
        std::thread::sleep(Duration::from_millis(100));
    }
    if pid_alive(pid) {
        s.phase = "error".into();
        s.error = Some("ffmpeg did not exit after SIGINT".into());
        s.phase_at = now_secs();
        write_state(&s)?;
        bail!("ffmpeg did not exit after SIGINT");
    }

    let wav = PathBuf::from(s.wav.clone().unwrap_or_default());
    let wav_ok = wav.exists() && std::fs::metadata(&wav).map(|m| m.len()).unwrap_or(0) >= 44;
    if !wav_ok {
        s.phase = "error".into();
        s.error = Some("recording produced no usable audio".into());
        s.phase_at = now_secs();
        write_state(&s)?;
        bail!("recording produced no usable audio ({})", wav.display());
    }

    s.phase = "transcribing".into();
    s.phase_at = now_secs();
    write_state(&s)?;

    let key = api_key()?;
    let text = match transcribe(config, &key, &wav).await {
        Ok(t) => t,
        Err(e) => {
            let mut es = s.clone();
            es.phase = "error".into();
            es.error = Some(e.to_string());
            es.phase_at = now_secs();
            let _ = write_state(&es);
            notify("nix-tts", &format!("✗ transcription failed: {e}"));
            return Err(e);
        }
    };

    let duration = audio_duration_seconds(&wav).unwrap_or(0.0);
    let cost = config.price_per_second.map(|p| duration * p);

    let out = resolve_output(
        output.as_deref(),
        config.output_file.as_ref(),
        state_dir().join("output.txt"),
    );
    std::fs::write(&out, format!("{text}\n"))
        .with_context(|| format!("failed to write {}", out.display()))?;

    copy_to_clipboard(&text);

    let mut done = s.clone();
    done.phase = "done".into();
    done.phase_at = now_secs();
    done.preview = Some(preview(&text));
    done.cost = cost;
    write_state(&done)?;

    let cost_line = cost
        .map(|c| format!("\nDuration: {duration:.1}s | Cost: ${c:.6}"))
        .unwrap_or_default();
    println!("{text}{cost_line}");

    let notif_body = match cost {
        Some(c) => format!("{duration:.1}s • ${c:.6}\n{}", preview(&text)),
        None => preview(&text),
    };
    notify("nix-tts", &notif_body);

    println!("written to {}", out.display());
    Ok(())
}

async fn toggle(config: &Config, output: Option<String>) -> Result<()> {
    let phase = read_state()
        .map(|s| s.phase)
        .unwrap_or_else(|| "idle".to_string());
    match phase.as_str() {
        "recording" => stop(config, output).await,
        "finalizing" | "transcribing" => {
            println!("transcription in progress");
            Ok(())
        }
        _ => start(config),
    }
}

async fn transcribe_file(config: &Config, file: &Path, output: Option<String>) -> Result<()> {
    let key = api_key()?;
    let text = transcribe(config, &key, file).await?;

    let out = resolve_output(
        output.as_deref(),
        config.output_file.as_ref(),
        PathBuf::from("output.txt"),
    );
    std::fs::write(&out, format!("{text}\n"))
        .with_context(|| format!("failed to write {}", out.display()))?;

    copy_to_clipboard(&text);
    println!("{text}");

    if let Some(price) = config.price_per_second {
        let duration = audio_duration_seconds(file)?;
        let cost = duration * price;
        println!("Duration: {duration:.1}s | Cost: ${cost:.6}");
    }

    println!("written to {}", out.display());
    Ok(())
}

#[tokio::main]
async fn main() -> Result<()> {
    let cli = Cli::parse();

    match cli.command {
        Cmd::Status => {
            status();
            Ok(())
        }
        Cmd::Start => {
            let config = load_config(cli.config.as_deref())?;
            start(&config)
        }
        Cmd::Stop { output } => {
            let config = load_config(cli.config.as_deref())?;
            stop(&config, output).await
        }
        Cmd::Toggle { output } => {
            let config = load_config(cli.config.as_deref())?;
            toggle(&config, output).await
        }
        Cmd::Transcribe { file, output } => {
            let config = load_config(cli.config.as_deref())?;
            transcribe_file(&config, &file, output).await
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn preview_flattens_and_truncates() {
        assert_eq!(preview("line1\nline2"), "line1 line2");
        let long = "a".repeat(200);
        assert!(preview(&long).chars().count() <= 80);
    }

    #[test]
    fn done_decays_to_idle() {
        let s = State {
            phase: "done".into(),
            phase_at: 1000,
            ..State::default()
        };
        assert_eq!(effective_state(Some(s), 1100).phase, "idle");
    }

    #[test]
    fn done_stays_within_window() {
        let s = State {
            phase: "done".into(),
            phase_at: 1000,
            ..State::default()
        };
        assert_eq!(effective_state(Some(s), 1003).phase, "done");
    }

    #[test]
    fn none_state_is_idle() {
        assert_eq!(effective_state(None, 0).phase, "idle");
    }

    #[test]
    fn output_cli_overrides_config() {
        let p = resolve_output(
            Some("/tmp/x.txt"),
            Some(&"~/out.txt".to_string()),
            PathBuf::from("default.txt"),
        );
        assert_eq!(p, PathBuf::from("/tmp/x.txt"));
    }

    #[test]
    fn output_default_passthrough() {
        let p = resolve_output(None, None, PathBuf::from("/tmp/d/output.txt"));
        assert_eq!(p, PathBuf::from("/tmp/d/output.txt"));
    }

    #[test]
    fn own_pid_is_alive() {
        let pid = std::process::id();
        assert!(pid_alive(pid));
    }
}
