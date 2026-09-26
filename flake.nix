{
  description = "nix-stt — push-to-talk speech-to-text dictation via OpenRouter, with waybar integration";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  };

  outputs =
    { self, nixpkgs }:
    let
      systems = [
        "x86_64-linux"
        "aarch64-linux"
      ];
      forAllSystems = nixpkgs.lib.genAttrs systems;
      nixpkgsFor = forAllSystems (system: nixpkgs.legacyPackages.${system});
    in
    {
      packages = forAllSystems (
        system:
        let pkgs = nixpkgsFor.${system};
        in
        {
          default = pkgs.rustPlatform.buildRustPackage {
            pname = "nix-stt";
            version = "0.2.0";

            src = nixpkgs.lib.cleanSourceWith {
              src = ./.;
              filter =
                path: type:
                let
                  base = baseNameOf path;
                in
                base != ".env"
                && base != ".git"
                && base != "target"
                && base != "result";
            };

            cargoLock.lockFile = ./Cargo.lock;

            doCheck = false; # pure-Rust unit tests run via `cargo test` in dev

            meta = {
              description = "Push-to-talk speech-to-text dictation via OpenRouter, with waybar integration";
              mainProgram = "nix-stt";
              platforms = nixpkgs.lib.platforms.linux;
            };
          };
        }
      );

      devShells = forAllSystems (
        system:
        let pkgs = nixpkgsFor.${system};
        in
        {
          default = pkgs.mkShell {
            packages = with pkgs; [
              cargo
              rustc
              rustfmt
              clippy
              rust-analyzer
              ffmpeg
              wl-clipboard
              dunst
            ];
            RUST_SRC_PATH = "${pkgs.rustPlatform.rustLibSrc}";
          };
        }
      );

      homeManagerModules = {
        nix-stt = import ./modules/hm.nix { inherit self; };
        default = self.homeManagerModules.nix-stt;
      };

      formatter = forAllSystems (system: nixpkgsFor.${system}.nixfmt-rfc-style);
    };
}
