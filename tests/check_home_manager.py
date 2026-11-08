"""Validate the TOML produced by tests/home-manager.nix."""
import sys
import tomllib

with open(sys.argv[1], "rb") as config:
    settings = tomllib.load(config)

assert settings["model"] == "test/model", settings
assert settings["language"] == "en", settings
assert settings["recording"]["sample_rate"] == 16000, settings
print("Home Manager module generated the expected TOML")
