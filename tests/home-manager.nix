# Pin Home Manager for the test without adding it as a dependency for users.
let
  hm = builtins.getFlake "github:nix-community/home-manager/2c0c1f01521469cf0271180c453c79782d53c2f6";
  project = builtins.getFlake (toString ../.);
  pkgs = project.inputs.nixpkgs.legacyPackages.x86_64-linux;
  module = import ../modules/hm.nix {
    self.packages.x86_64-linux.default = pkgs.hello;
  };
  mkHome = enabled: hm.lib.homeManagerConfiguration {
    inherit pkgs;
    modules = [
      module
      {
        home.username = "test";
        home.homeDirectory = "/home/test";
        home.stateVersion = "26.05";
        programs.nix-stt = {
          enable = enabled;
          settings = {
            model = "test/model";
            language = "en";
            keyterms = [ "NixOS" "Ada Lovelace" ];
            recording.sample_rate = 16000;
          };
        };
      }
    ];
  };
  enabled = (mkHome true).config;
  disabled = (mkHome false).config;
in
assert builtins.elem pkgs.hello enabled.home.packages;
assert !(builtins.elem pkgs.hello disabled.home.packages);
assert !(disabled.xdg.configFile ? "nix-stt/config.toml");
enabled.xdg.configFile."nix-stt/config.toml".source
