# lib.rtSpeech — system-independent helpers shared by the NixOS module and the infra
# (swarm-nix renders per-host deploy-rs `env` files with mkEnvFile).
{ lib }:
let
  services = {
    asr = { port = 8091; unit = "rt-asr"; stateDir = "/var/lib/rt-asr"; };
    phonetic = { port = 8092; unit = "rt-phonetic"; stateDir = "/var/lib/rt-phonetic"; };
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
      (kv "RT_LISTEN_ADDRESSES" (lib.concatStringsSep "," cfg.listenAddresses))
      (kv "RT_PORT" cfg.port)
      (kv "RT_DEVICE" cfg.device)
      (kv "RT_MODEL_DIR" cfg.modelDir)
      (kv "HF_HOME" "${cfg.modelDir}/hf")
      (kv "RT_LOG_LEVEL" cfg.logLevel)
    ]
    ++ lib.optional ((cfg.configState or null) != null) (kv "RT_CONFIG_STATE" cfg.configState)
    # Runtime-switchable devices; omitted when only `device` itself (service default).
    ++ lib.optional ((cfg.devices or [ cfg.device ]) != [ cfg.device ])
      (kv "RT_DEVICES" (lib.concatStringsSep "," cfg.devices))
    ++ lib.optional (cfg.model != null) (kv "RT_MODEL" cfg.model)
    ++ lib.optional ((cfg.computeType or null) != null) (kv "RT_COMPUTE_TYPE" cfg.computeType)
    ++ lib.optional ((cfg.idleUnloadMinutes or null) != null) (kv "RT_IDLE_UNLOAD_MINUTES" cfg.idleUnloadMinutes)
    ++ lib.optional (lib.elem "rocm" (cfg.devices or [ cfg.device ]) && cfg.rocmGfxVersion != "") (kv "HSA_OVERRIDE_GFX_VERSION" cfg.rocmGfxVersion)
    ++ lib.optionals (service == "asr") (
      lib.optional (cfg.language != null) (kv "RT_ASR_LANGUAGE" cfg.language)
      ++ [ (kv "RT_ASR_LANGUAGES" (lib.concatStringsSep "," cfg.languages)) ]
      ++ lib.optional (cfg.beamSize != null) (kv "RT_ASR_BEAM_SIZE" cfg.beamSize)
      ++ [ (kv "RT_ASR_VAD" (boolStr cfg.vad)) ]
      ++ lib.optional (cfg.chunkSeconds != null) (kv "RT_ASR_CHUNK_SECONDS" cfg.chunkSeconds)
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

  # PipeWire drop-in (pipewire.conf.d) with the edge's virtual devices - the one
  # definition behind homeManagerModules.rt-translator and nixosModules.virtual-sinks.
  # input: null sink other apps play into; the edge captures its monitor.
  # output: loopback - its Audio/Sink side is where the edge plays the
  # translation, its Audio/Source side is a real microphone other apps
  # (browsers, Zoom, Teams hide *.monitor sources) can pick.
  virtualDevicesConf = { inputName, outputName, micName }: ''
    context.objects = [
      { factory = adapter
        args = {
          factory.name = support.null-audio-sink
          node.name = "${inputName}"
          node.description = "RT-Virtual-Input"
          media.class = "Audio/Sink"
          audio.position = [ FL FR ]
          monitor.channel-volumes = true
          monitor.passthrough = true
        }
      }
    ]
    context.modules = [
      { name = libpipewire-module-loopback
        args = {
          node.description = "RT Переклад"
          audio.position = [ FL FR ]
          capture.props = {
            node.name = "${outputName}"
            node.description = "RT Переклад → мікрофон"
            media.class = "Audio/Sink"
          }
          playback.props = {
            node.name = "${micName}"
            node.description = "RT Переклад (мікрофон)"
            media.class = "Audio/Source"
          }
        }
      }
    ]
  '';

  # Probes the first listen address (IPv6 literals in brackets).
  healthCheckCommand = _service: cfg:
    let
      addr = builtins.head cfg.listenAddresses;
      host = if lib.hasInfix ":" addr then "[${addr}]" else addr;
    in
    "curl -sf http://${host}:${toString cfg.port}/health > /dev/null";
}
