# lib.rtSpeech — system-independent helpers shared by the NixOS module and the infra
# (swarm-nix renders per-host deploy-rs `env` files with mkEnvFile).
{ lib }:
let
  services = {
    asr = { port = 8091; unit = "rt-asr"; stateDir = "/var/lib/rt-asr"; };
    tts = { port = 8093; unit = "rt-tts"; stateDir = "/var/lib/rt-tts"; };
  };

  boolStr = b: if b then "true" else "false";

  kv = name: value:
    let s = toString value; in
    if lib.hasInfix "\n" s
    then throw "rtSpeech.envLines: value of ${name} contains a newline"
    else "${name}=${s}";

  # Single source of truth: services.rtSpeech.<service> cfg -> raw "K=V" lines
  # (EnvironmentFile format). The NixOS module quotes them for Environment=.
  # Only paths, never secrets: the token itself arrives via the unit's LoadCredential,
  # RT_API_TOKEN_FILE is the literal credential path (no %d — EnvironmentFile content
  # is not specifier-expanded; infra R3).
  envLines = service: cfg:
    assert lib.assertMsg (services ? ${service}) "rtSpeech.envLines: unknown service '${service}'";
    [
      "PYTHONUNBUFFERED=1"
      (kv "RT_HOST" cfg.host)
      (kv "RT_PORT" cfg.port)
      (kv "RT_DEVICE" cfg.device)
      (kv "RT_MODEL_DIR" cfg.modelDir)
      (kv "HF_HOME" "${cfg.modelDir}/hf")
      (kv "RT_LOG_LEVEL" cfg.logLevel)
    ]
    ++ lib.optional ((cfg.configState or null) != null) (kv "RT_CONFIG_STATE" cfg.configState)
    ++ lib.optional (cfg.model != null) (kv "RT_MODEL" cfg.model)
    ++ lib.optional ((cfg.computeType or null) != null) (kv "RT_COMPUTE_TYPE" cfg.computeType)
    ++ lib.optional (cfg.device == "rocm" && cfg.rocmGfxVersion != "") (kv "HSA_OVERRIDE_GFX_VERSION" cfg.rocmGfxVersion)
    ++ lib.optionals (service == "asr") (
      lib.optional (cfg.language != null) (kv "RT_ASR_LANGUAGE" cfg.language)
      ++ lib.optional (cfg.beamSize != null) (kv "RT_ASR_BEAM_SIZE" cfg.beamSize)
      ++ [ (kv "RT_ASR_VAD" (boolStr cfg.vad)) ]
    )
    ++ lib.optionals (service == "tts") [
      (kv "RT_TTS_VOICE" cfg.voice)
      (kv "RT_TTS_SPEED" cfg.speed)
      (kv "RT_TTS_SAMPLE_RATE" cfg.sampleRate)
    ]
    ++ lib.optional ((cfg.apiTokenFile or null) != null)
      (kv "RT_API_TOKEN_FILE" "/run/credentials/${services.${service}.unit}.service/api-token")
    ++ lib.mapAttrsToList kv cfg.extraEnv;
in
{
  inherit services envLines;

  variantForDevice = { cpu = "cpu"; cuda = "gpu"; rocm = "rocm"; };

  mkEnvFile = { pkgs, service, cfg }:
    pkgs.writeTextDir "env" (lib.concatLines (envLines service cfg));

  healthCheckCommand = _service: cfg:
    "curl -sf http://127.0.0.1:${toString cfg.port}/health > /dev/null";
}
