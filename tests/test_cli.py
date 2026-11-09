"""Offline CLI integration tests: no microphone, desktop session, or API key required."""
import http.server
import json
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import wave


ROOT = Path(__file__).resolve().parents[1]
BINARY = ROOT / "target" / "debug" / "nix-stt"


class MockAPI:
    def __init__(self, body=b'{"text":"Hello from the mock API."}', status=200, delay=0):
        self.body, self.status, self.delay = body, status, delay
        self.requests = []
        owner = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                owner.requests.append((self.path, self.headers, body))
                if owner.delay:
                    time.sleep(owner.delay)
                self.send_response(owner.status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                try:
                    self.wfile.write(owner.body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def log_message(self, *args):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server.server_port}/api/v1"


class CliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="nix-stt-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.state_home = self.root / "state"
        self.cwd = self.root / "work"
        self.cwd.mkdir()
        self.config = self.root / "config.toml"
        self.wav = self.root / "sample.wav"
        with wave.open(str(self.wav), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(16000)
            audio.writeframes(bytes(3200))
        self.env = {
            "HOME": str(self.home),
            "XDG_STATE_HOME": str(self.state_home),
            "PATH": str(self.bin),
            "OPENROUTER_API_KEY": "test-key-not-a-secret",
        }

    def config_text(self, url="http://127.0.0.1:1", extra=""):
        return f'model = "test/model"\napi_base = "{url}"\n{extra}\n'

    def write_config(self, url="http://127.0.0.1:1", extra=""):
        self.config.write_text(self.config_text(url, extra))

    def run_cli(self, *args, env=None, timeout=20):
        result = subprocess.run(
            [str(BINARY), *map(str, args)], cwd=self.cwd,
            env=self.env | (env or {}), text=True, capture_output=True, timeout=timeout,
        )
        return result

    def executable(self, name, body):
        path = self.bin / name
        path.write_text(f"#!{sys.executable}\n" + body)
        path.chmod(0o755)

    def state(self, phase, **kwargs):
        path = self.state_home / "nix-stt" / "state.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"phase": phase, "phase_at": int(time.time()), **kwargs}))
        return path

    def status(self):
        result = self.run_cli("status")
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_status_without_config_is_idle(self):
        self.assertEqual(self.status()["class"], "idle")

    def test_status_states_and_decay(self):
        self.state("recording", pid=os.getpid(), started_at=int(time.time()) - 3)
        self.assertIn("Recording", self.status()["tooltip"])
        for phase in ("finalizing", "transcribing"):
            self.state(phase)
            self.assertEqual(self.status()["class"], phase)
        self.state("done", preview="hello", cost=0.001)
        self.assertIn("hello ($0.001000)", self.status()["tooltip"])
        self.state("error", error="example failure")
        self.assertEqual(self.status()["tooltip"], "example failure")
        self.state("done", phase_at=int(time.time()) - 20)
        self.assertEqual(self.status()["class"], "idle")
        self.state("transcribing", phase_at=int(time.time()) - 200)
        self.assertEqual(self.status()["class"], "error")
        self.state("recording", pid=2147483647)
        self.assertIn("died", self.status()["tooltip"])

    def test_missing_invalid_config_and_key(self):
        result = self.run_cli("start")
        self.assertIn("no config found", result.stderr)
        result = self.run_cli("--config", self.config, "start")
        self.assertIn("not found", result.stderr)
        self.config.write_text("not valid toml =")
        self.assertIn("failed to parse", self.run_cli("--config", self.config, "start").stderr)
        self.write_config()
        env = self.env.copy()
        env.pop("OPENROUTER_API_KEY")
        result = subprocess.run([str(BINARY), "--config", str(self.config), "transcribe", str(self.wav)],
                                cwd=self.cwd, env=env, capture_output=True, text=True)
        self.assertIn("OPENROUTER_API_KEY is not set", result.stderr)

    def test_key_lookup_environment_and_dotenv_priority(self):
        config_dir = self.home / ".config" / "nix-stt"
        config_dir.mkdir(parents=True)
        (config_dir / ".env").write_text("OPENROUTER_API_KEY=home-test-key\n")
        (self.root / ".env").write_text("OPENROUTER_API_KEY=config-test-key\n")
        (self.cwd / ".env").write_text("OPENROUTER_API_KEY=cwd-test-key\n")
        with MockAPI() as api:
            self.write_config(api.url)
            env = self.env.copy()
            cases = [
                ("process-test-key", "process-test-key"),
                (None, "home-test-key"),
            ]
            for key, expected in cases:
                if key is None:
                    env.pop("OPENROUTER_API_KEY", None)
                else:
                    env["OPENROUTER_API_KEY"] = key
                result = subprocess.run([str(BINARY), "--config", str(self.config), "transcribe", str(self.wav)],
                                        cwd=self.cwd, env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(api.requests[-1][1]["Authorization"], f"Bearer {expected}")
            (config_dir / ".env").unlink()
            result = subprocess.run([str(BINARY), "--config", str(self.config), "transcribe", str(self.wav)],
                                    cwd=self.cwd, env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(api.requests[-1][1]["Authorization"], "Bearer config-test-key")
            (self.root / ".env").unlink()
            result = subprocess.run([str(BINARY), "--config", str(self.config), "transcribe", str(self.wav)],
                                    cwd=self.cwd, env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(api.requests[-1][1]["Authorization"], "Bearer cwd-test-key")

    def test_config_lookup_priority(self):
        with MockAPI() as api:
            self.write_config(api.url, 'language = "fr"')
            env_config = self.root / "env.toml"
            env_config.write_text(self.config_text(api.url, 'language = "de"'))
            config_dir = self.home / ".config" / "nix-stt"
            config_dir.mkdir(parents=True)
            (config_dir / "config.toml").write_text(self.config_text(api.url, 'language = "en"'))
            (self.cwd / "config.toml").write_text(self.config_text(api.url, 'language = "es"'))
            for args, env, language in [
                (["--config", self.config], {"NIX_STT_CONFIG": str(env_config)}, "fr"),
                ([], {"NIX_STT_CONFIG": str(env_config)}, "de"),
                ([], {}, "en"),
            ]:
                result = self.run_cli(*args, "transcribe", self.wav, env=env)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(f'\r\n\r\n{language}\r\n'.encode(), api.requests[-1][2])
            (config_dir / "config.toml").unlink()
            result = self.run_cli("transcribe", self.wav)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(b'\r\n\r\nes\r\n', api.requests[-1][2])

    def test_transcribe_request_clipboard_output_and_cost(self):
        clipboard = self.root / "clipboard"
        self.executable("wl-copy", f'import sys\nfrom pathlib import Path\nPath({str(clipboard)!r}).write_text(sys.stdin.read())\n')
        self.executable("ffprobe", 'print("2.5")\n')
        with MockAPI() as api:
            self.write_config(api.url, 'language = "en"\nresponse_format = "verbose_json"\nprice_per_second = 0.01\noutput_file = "configured.txt"')
            result = self.run_cli("--config", self.config, "transcribe", self.wav, "--output", "override.txt")
            self.assertEqual(result.returncode, 0, result.stderr)
            path, headers, body = api.requests[0]
            self.assertEqual(path, "/api/v1/audio/transcriptions")
            self.assertEqual(headers["Authorization"], "Bearer test-key-not-a-secret")
            for value in (b'name="model"', b'test/model', b'name="file"', b'sample.wav', b'name="language"', b'en', b'name="response_format"', b'verbose_json'):
                self.assertIn(value, body)
            self.assertIn(self.wav.read_bytes(), body)
            self.assertEqual((self.home / "override.txt").read_text(), "Hello from the mock API.\n")
            self.assertFalse((self.home / "configured.txt").exists())
            self.assertEqual(clipboard.read_text(), "Hello from the mock API.")
            self.assertIn("Cost (estimated): $0.025000", result.stdout)

    def test_returned_cost_wins_without_probing_duration(self):
        for cost in (0.000508, 0):
            with self.subTest(cost=cost), MockAPI(json.dumps({
                "text": "API transcript", "usage": {"cost": cost, "seconds": 9.2,
                                                       "input_tokens": 83, "total_tokens": 113}
            }).encode()) as api:
                # No ffprobe on PATH; actual cost must not depend on it or the estimate.
                for extra in ("price_per_second = 0.01", ""):
                    self.write_config(api.url, extra)
                    result = self.run_cli("--config", self.config, "transcribe", self.wav)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn(f"Cost (API): ${cost:.6f}", result.stdout)
                    self.assertNotIn("estimated", result.stdout)
                    self.assertEqual((self.cwd / "output.txt").read_text(), "API transcript\n")

    def test_missing_or_invalid_usage_falls_back_or_omits_cost(self):
        self.executable("ffprobe", 'print("2.5")\n')
        for usage in (None, {}, {"seconds": 2.5}, {"cost": None}, {"cost": -1},
                      {"cost": "0.5"}, {"cost": []}, [], "unexpected"):
            with self.subTest(usage=usage), MockAPI(json.dumps({"text": "hello", "usage": usage}).encode()) as api:
                for extra, expected in (("price_per_second = 0.01", "Cost (estimated): $0.025000"),
                                        ("", None)):
                    self.write_config(api.url, extra)
                    result = self.run_cli("--config", self.config, "transcribe", self.wav)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    if expected:
                        self.assertIn(expected, result.stdout)
                    else:
                        self.assertNotIn("Cost", result.stdout)
                    self.assertEqual((self.cwd / "output.txt").read_text(), "hello\n")

    def test_estimate_probe_failure_retains_existing_error(self):
        with MockAPI() as api:
            self.write_config(api.url, "price_per_second = 0.01")
            result = self.run_cli("--config", self.config, "transcribe", self.wav)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("failed to run ffprobe", result.stderr)
            self.assertEqual((self.cwd / "output.txt").read_text(), "Hello from the mock API.\n")

    def test_optional_fields_omitted_and_clipboard_optional(self):
        with MockAPI() as api:
            self.write_config(api.url)
            result = self.run_cli("--config", self.config, "transcribe", self.wav)
            self.assertEqual(result.returncode, 0, result.stderr)
            body = api.requests[0][2]
            self.assertNotIn(b'name="language"', body)
            self.assertNotIn(b'name="response_format"', body)
            self.assertEqual((self.cwd / "output.txt").read_text(), "Hello from the mock API.\n")

    def test_multipart_size_boundary_and_no_upload_when_oversized(self):
        with MockAPI() as api:
            self.write_config(api.url)
            with self.wav.open("r+b") as audio:
                audio.truncate(25_000_000)
            result = self.run_cli("--config", self.config, "transcribe", self.wav)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(len(api.requests), 1)
            self.assertIn(b'name="file"', api.requests[0][2])
            output = self.cwd / "output.txt"
            output.write_text("existing transcript\n")
            with self.wav.open("r+b") as audio:
                audio.truncate(25_000_001)
            result = self.run_cli("--config", self.config, "transcribe", self.wav)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("25,000,000 bytes", result.stderr)
            self.assertIn("no request was sent", result.stderr)
            self.assertIn("Split", result.stderr)
            self.assertEqual(len(api.requests), 1)
            self.assertEqual(output.read_text(), "existing transcript\n")

    def test_http_413_explains_provider_limit(self):
        with MockAPI(b"provider limit", 413) as api:
            self.write_config(api.url)
            result = self.run_cli("--config", self.config, "transcribe", self.wav)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("HTTP 413", result.stderr)
            self.assertIn("Split", result.stderr)
            self.assertIn("provider limit", result.stderr)
            self.assertEqual(len(api.requests), 1)
            self.assertFalse((self.cwd / "output.txt").exists())

    def test_http_errors_invalid_response_and_timeout(self):
        for body, status, expected in [(b"bad request", 400, "HTTP 400"), (b"not json", 200, "failed to parse"), (b'{}', 200, "failed to parse")]:
            with self.subTest(expected=expected), MockAPI(body, status) as api:
                self.write_config(api.url)
                result = self.run_cli("--config", self.config, "transcribe", self.wav)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(expected, result.stderr)
        with MockAPI(delay=2) as api:
            self.write_config(api.url, "timeout_secs = 1")
            result = self.run_cli("--config", self.config, "transcribe", self.wav)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("request to OpenRouter failed", result.stderr)

    def test_busy_toggle_and_start(self):
        self.write_config()
        for phase in ("finalizing", "transcribing"):
            self.state(phase)
            self.assertIn("in progress", self.run_cli("--config", self.config, "toggle").stdout)
            self.assertNotEqual(self.run_cli("--config", self.config, "start").returncode, 0)

    def test_dead_recorder_and_missing_ffmpeg(self):
        self.write_config()
        self.state("recording", pid=2147483647)
        result = self.run_cli("--config", self.config, "stop")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.status()["class"], "error")
        result = self.run_cli("--config", self.config, "start")
        self.assertIn("failed to start ffmpeg", result.stderr)

    def test_stop_rejects_wrong_phase(self):
        self.write_config()
        self.state("done")
        self.assertIn("not recording", self.run_cli("--config", self.config, "stop").stderr)

    def test_cli_rejects_unknown_command_and_missing_audio(self):
        self.assertNotEqual(self.run_cli("unknown-command").returncode, 0)
        self.assertIn("transcribe", self.run_cli("--help").stdout)
        self.write_config()
        result = self.run_cli("--config", self.config, "transcribe", self.root / "missing.wav")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("failed to read audio file", result.stderr)

    def install_recorder(self):
        ready = self.root / "recorder-ready"
        args_file = self.root / "recorder-args"
        self.executable("ffmpeg", f'''
import json, signal, sys, time, wave
from pathlib import Path
Path({str(args_file)!r}).write_text(json.dumps(sys.argv[1:]))
with wave.open(sys.argv[-1], "wb") as audio:
    audio.setnchannels(1)
    audio.setsampwidth(2)
    audio.setframerate(16000)
    audio.writeframes(bytes(3200))
def stop(*args):
    sys.exit(0)
signal.signal(signal.SIGINT, stop)
Path({str(ready)!r}).touch()
while True:
    time.sleep(0.05)
''')
        return ready, args_file

    def start_fake_recording(self):
        ready, args_file = self.install_recorder()
        result = self.run_cli("--config", self.config, "toggle")
        self.assertEqual(result.returncode, 0, result.stderr)
        state_path = self.state_home / "nix-stt" / "state.json"
        pid = json.loads(state_path.read_text())["pid"]

        def cleanup_recorder():
            # Only kill a process that still looks like this test's recorder.
            try:
                cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
                if str(self.bin / "ffmpeg").encode() in cmdline:
                    os.kill(pid, signal.SIGKILL)
            except (FileNotFoundError, ProcessLookupError):
                pass

        self.addCleanup(cleanup_recorder)
        deadline = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertTrue(ready.exists(), "fake recorder did not start")
        return args_file

    def test_record_toggle_stop_end_to_end(self):
        clipboard = self.root / "clipboard"
        notification = self.root / "notification"
        self.executable("wl-copy", f'import sys\nfrom pathlib import Path\nPath({str(clipboard)!r}).write_text(sys.stdin.read())\n')
        self.executable("dunstify", f'import json,sys\nfrom pathlib import Path\nPath({str(notification)!r}).write_text(json.dumps(sys.argv[1:]))\n')
        self.executable("ffprobe", 'print("2.5")\n')
        with MockAPI() as api:
            self.write_config(api.url, 'price_per_second = 0.01\n[recording]\ninput = "test-source"\ninput_format = "alsa"\nsample_rate = 22050\nchannels = 2')
            args_file = self.start_fake_recording()
            args = json.loads(args_file.read_text())
            for expected in ("test-source", "alsa", "22050", "2", "pcm_s16le"):
                self.assertIn(expected, args)
            self.assertEqual(self.status()["class"], "recording")
            self.assertIn("already recording", self.run_cli("--config", self.config, "start").stdout)
            result = self.run_cli("--config", self.config, "toggle")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.status()["class"], "done")
            self.assertEqual((self.state_home / "nix-stt" / "output.txt").read_text(), "Hello from the mock API.\n")
            self.assertEqual(clipboard.read_text(), "Hello from the mock API.")
            deadline = time.monotonic() + 3
            while not notification.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertIn("Cost (estimated): $0.025000", notification.read_text())
            self.assertIn("estimated cost: $0.025000", self.status()["tooltip"])
            self.assertIn("Cost (estimated): $0.025000", result.stdout)
            state = json.loads((self.state_home / "nix-stt" / "state.json").read_text())
            self.assertEqual(state["cost_source"], "estimated")
            self.assertEqual(len(api.requests), 1)

    def test_recording_returned_cost_in_output_notification_and_status(self):
        notification = self.root / "notification"
        self.executable("dunstify", f'import json,sys\nfrom pathlib import Path\nPath({str(notification)!r}).write_text(json.dumps(sys.argv[1:]))\n')
        # A failed duration probe must not prevent the returned cost being used.
        with MockAPI(b'{"text":"actual", "usage":{"cost":0.000508,"seconds":9.2}}') as api:
            self.write_config(api.url, "price_per_second = 0.01")
            self.start_fake_recording()
            result = self.run_cli("--config", self.config, "stop")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Cost (API): $0.000508", result.stdout)
            self.assertIn("API cost: $0.000508", self.status()["tooltip"])
            state = json.loads((self.state_home / "nix-stt" / "state.json").read_text())
            self.assertEqual(state["cost"], 0.000508)
            self.assertEqual(state["cost_source"], "api")
            deadline = time.monotonic() + 3
            while not notification.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertIn("Cost (API): $0.000508", notification.read_text())

    def test_recording_without_usage_or_price_has_no_cost(self):
        with MockAPI() as api:
            self.write_config(api.url)
            self.start_fake_recording()
            result = self.run_cli("--config", self.config, "stop")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn("Cost", result.stdout)
            self.assertNotIn("cost", self.status()["tooltip"])
            state = json.loads((self.state_home / "nix-stt" / "state.json").read_text())
            self.assertNotIn("cost", state)
            self.assertNotIn("cost_source", state)

    def test_recording_http_failure_sets_error(self):
        with MockAPI(b"server error", 500) as api:
            self.write_config(api.url)
            self.start_fake_recording()
            result = self.run_cli("--config", self.config, "stop")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("HTTP 500", result.stderr)
            self.assertEqual(self.status()["class"], "error")
            self.assertIn("HTTP 500", self.status()["tooltip"])

    def test_oversized_recording_is_preserved_and_sets_error(self):
        with MockAPI() as api:
            self.write_config(api.url)
            self.start_fake_recording()
            recording = self.state_home / "nix-stt" / "recording.wav"
            with recording.open("r+b") as audio:
                audio.truncate(25_000_001)
            result = self.run_cli("--config", self.config, "stop")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("no request was sent", result.stderr)
            self.assertEqual(self.status()["class"], "error")
            self.assertIn("25 MB", self.status()["tooltip"])
            self.assertEqual(recording.stat().st_size, 25_000_001)
            self.assertEqual(len(api.requests), 0)

    def test_recording_without_usable_audio_sets_error(self):
        self.write_config()
        self.start_fake_recording()
        (self.state_home / "nix-stt" / "recording.wav").write_bytes(b"")
        result = self.run_cli("--config", self.config, "stop")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no usable audio", result.stderr)
        self.assertEqual(self.status()["class"], "error")

    def test_generated_audio_with_real_ffmpeg_and_ffprobe(self):
        ffmpeg = shutil.which("ffmpeg")
        ffprobe = shutil.which("ffprobe")
        self.assertIsNotNone(ffmpeg, "ffmpeg is required for integration tests")
        self.assertIsNotNone(ffprobe, "ffprobe is required for integration tests")
        generated = self.root / "generated.wav"
        subprocess.run([ffmpeg, "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=0.2",
                        "-ar", "16000", "-ac", "1", str(generated)], check=True, timeout=10)
        (self.bin / "ffprobe").symlink_to(ffprobe)
        with MockAPI() as api:
            self.write_config(api.url, "price_per_second = 0.01")
            result = self.run_cli("--config", self.config, "transcribe", generated)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Duration: 0.2s | Cost (estimated): $0.002000", result.stdout)
            self.assertIn(generated.read_bytes(), api.requests[0][2])


if __name__ == "__main__":
    unittest.main()
