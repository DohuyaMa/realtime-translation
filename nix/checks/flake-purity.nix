{ pkgs, src }:
pkgs.runCommand "rt-flake-purity" { nativeBuildInputs = [ pkgs.gnugrep ]; } ''
  cd ${src}
  if grep -rnE '/home/|builtins\.getEnv|builtins\.currentSystem|builtins\.currentTime|<nixpkgs>|--impure' \
       flake.nix nix/ --include='*.nix' --exclude='flake-purity.nix'; then
    echo "machine-local or impure reference found in Nix sources (see above)"
    exit 1
  fi
  touch $out
''
