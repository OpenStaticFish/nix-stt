{ self }:
{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.programs.nix-stt;
  toml = pkgs.formats.toml { };
in
{
  options.programs.nix-stt = {
    enable = lib.mkEnableOption "nix-stt — push-to-talk dictation via OpenRouter";

    package = lib.mkOption {
      type = lib.types.package;
      default = self.packages.${pkgs.system}.default;
      defaultText = "nix-stt flake package for this system";
      description = "The nix-stt package to install.";
    };

    settings = lib.mkOption {
      type = toml.type;
      default = { };
      example = {
        model = "mistralai/voxtral-small-24b-2507-stt";
        price_per_second = 0.00005;
      };
      description = ''
        Configuration written to ~/.config/nix-stt/config.toml.
        See the repo's config.toml / README for all options.
      '';
    };
  };

  config = lib.mkIf cfg.enable {
    home.packages = [ cfg.package ];

    xdg.configFile."nix-stt/config.toml" = lib.mkIf (cfg.settings != { }) {
      source = toml.generate "nix-stt-config.toml" cfg.settings;
    };
  };
}
