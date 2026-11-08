# Contributing and tests

Pull requests run the checks below on x86_64 Ubuntu 24.04. They exercise the CLI
without a real desktop or API account. All Rust and Python test dependencies are
in the Nix development shell. The runner is pinned to avoid unannounced
`ubuntu-latest` image transitions; update it deliberately after testing.

```sh
nix shell --inputs-from . nixpkgs#actionlint --command actionlint
nix shell --inputs-from . nixpkgs#python3Packages.pyflakes --command pyflakes tests/test_cli.py tests/check_home_manager.py
nix develop --command cargo fmt --all -- --check
nix develop --command cargo clippy --locked --all-targets -- -D warnings
nix develop --command cargo test --locked
nix develop --command cargo build --locked
nix develop --command python3 -m unittest discover -s tests -v
nix build --no-link
config_path="$(nix build --impure --no-link --print-out-paths --file tests/home-manager.nix)"
nix develop --command python3 tests/check_home_manager.py "$config_path"
```

The last check uses a separately pinned Home Manager revision, and needs network
access if it has not been fetched before. It evaluates enabled and disabled
module configurations and checks the generated TOML. The Nix package build also
runs the Rust unit tests; the separate CI test job runs both Rust and integration
tests.

## Adding tests

- Add small pure tests to the Rust test module and CLI/workflow tests to
  `tests/test_cli.py`. The integration suite runs the binary built in
  `target/debug`, so run `cargo build` after changes.
- `CliTests` creates an isolated HOME, state directory, working directory and
  PATH. `MockAPI` captures local transcription requests and can return errors,
  malformed responses or delayed responses. `executable` creates fake external
  commands; use synthetic audio rather than private recordings.
- For API features, assert both the outgoing fields and the handling of successful,
  missing, malformed and unsupported responses where relevant. Preserve default
  behavior when optional settings are omitted. Add tests for regressions and
  update the README for user-facing behavior.
- Mock tests do not establish that a specific OpenRouter provider/model supports
  a feature. Document any provider-specific limitations and test compatibility
  separately when appropriate.

## Optional live smoke test

The **Live OpenRouter smoke test** workflow runs only when manually dispatched on
`master`, never on pull requests. It generates synthetic spoken audio locally,
transcribes it with `mistralai/voxtral-small-transcribe-3-0`, and checks for a
nonempty transcript. It incurs API charges and requires network access. It is a
basic availability check, not a transcription accuracy or feature test.

To enable it, create a GitHub environment named `live-smoke` with a dedicated,
spend-limited `OPENROUTER_API_KEY` environment secret. Configure environment
approvals or deployment restrictions as desired. Run the workflow manually from
the Actions tab after reviewing changes on `master`. Never add keys or private
recordings to the repository or PR logs. The smoke test is not a required PR
check.

Once CI has run successfully on GitHub, protect `master` and require the
`Rust and integration tests` and `Nix build and Home Manager module` checks.
The jobs currently run only on x86_64; they do not establish aarch64 support.
