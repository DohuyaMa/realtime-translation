{ pkgs, lib }:
{ service, variant, rocmGpuTargets ? [ "gfx900" ] }:
let
  rtLib = import ../lib { inherit lib; };
  svc = rtLib.services.${service} or (throw "rtSpeech.mkBundle: unknown service '${service}'");
  sources = import ./sources.nix { inherit lib; };
  vp = import ./variant-pkgs.nix { inherit pkgs lib variant rocmGpuTargets; };

  pythonEnv = vp.python.withPackages (import ./deps.nix { pkgs = vp.pkgs; inherit service; });
  module = "src.services.${service}";
  bin = "rt-${service}";
  wrapperArgs = [
    "--add-flags" "-m ${module}"
    "--set" "PYTHONPATH" "${sources.speechSrc}"
    "--set" "PYTHONNOUSERSITE" "1"
  ]
  ++ lib.optionals (variant == "gpu") [ "--prefix" "LD_LIBRARY_PATH" ":" "/run/opengl-driver/lib" ]
  ++ lib.optionals (service == "tts") [ "--prefix" "PATH" ":" "${pkgs.espeak-ng}/bin" ];
in
pkgs.stdenvNoCC.mkDerivation {
  pname = "rt-${service}-${variant}";
  version = "0.1.0";
  dontUnpack = true;
  nativeBuildInputs = [ pkgs.makeWrapper ];

  installPhase = ''
    runHook preInstall
    mkdir -p $out/bin $out/share/rt-speech
    makeWrapper ${pythonEnv}/bin/python $out/bin/${bin} ${lib.escapeShellArgs wrapperArgs}
    makeWrapper ${pythonEnv}/bin/python $out/bin/rt-models \
      --add-flags "-m src.services.common.models_cli" \
      --set PYTHONPATH ${sources.speechSrc} --set PYTHONNOUSERSITE 1
    echo '{"service": "${service}", "variant": "${variant}", "port": ${toString svc.port}}' > $out/share/rt-speech/info.json
    runHook postInstall
  '';

  # A missing dependency fails the build, not the deploy.
  doInstallCheck = true;
  installCheckPhase = ''
    runHook preInstallCheck
    HOME=$TMPDIR PYTHONPATH=${sources.speechSrc} ${pythonEnv}/bin/python -c "import ${module}, src.services.common.models_cli"
    runHook postInstallCheck
  '';

  passthru = { inherit service variant pythonEnv; inherit (svc) port; };
  meta = {
    description = "rt-speech ${service} service bundle (${variant})";
    mainProgram = bin;
    platforms = [ "x86_64-linux" ];
  };
}
