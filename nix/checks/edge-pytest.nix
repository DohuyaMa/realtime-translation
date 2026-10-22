{ pkgs }:
let
  lib = pkgs.lib;
  py = pkgs.python313.withPackages (ps: (import ../packages/edge-deps.nix ps) ++ [ ps.pytest ps.pytest-asyncio ps.pytest-timeout ]);
  testSrc = lib.fileset.toSource {
    root = ../..;
    fileset = lib.fileset.unions [ ../../src ../../tests ];
  };
in
pkgs.runCommand "rt-edge-pytest" { nativeBuildInputs = [ py ]; SSL_CERT_FILE = "${pkgs.cacert}/etc/ssl/certs/ca-bundle.crt"; } ''
  cp -r ${testSrc} work
  chmod -R u+w work
  cd work
  export HOME=$TMPDIR QT_QPA_PLATFORM=offscreen
  python -m pytest tests -q -p no:cacheprovider \
    --ignore=tests/services --ignore=tests/test_audio.py
  touch $out
''
