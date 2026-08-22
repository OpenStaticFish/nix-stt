{ self }:
{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.programs.nix-tts;
  toml = pkgs.formats.toml { };
in
{
  options.programs.nix-tts = {
    enable = lib.mkEnableOption "nix-tts — push-to-talk dictation via OpenRouter";

    package = lib.mkOption {
      type = lib.types.package;
      default = self.packages.${pkgs.system}.default;
      defaultText = "nix-tts flake package for this system";
      description = "The nix-tts package to install.";
    };

    settings = lib.mkOption {
      type = toml.type;
      default = { };
      example = {
        model = "mistralai/voxtral-small-24b-2507-stt";
        price_per_second = 0.00005;
      };
      description = ''
        Configuration written to ~/.config/nix-tts/config.toml.
        See the repo's config.toml / README for all options.
      '';
    };
  };

  config = lib.mkIf cfg.enable {
    home.packages = [ cfg.package ];

    xdg.configFile."nix-tts/config.toml" = lib.mkIf (cfg.settings != { }) {
      source = toml.generate "nix-tts-config.toml" cfg.settings;
    };
  };
}
