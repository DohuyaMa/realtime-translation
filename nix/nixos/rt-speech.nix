# services.rtSpeech.{asr,tts} — system units modeled on swarm-nix llama-service.nix.
# deploy-rs mode: deployRsPath = "/var/lib/rt-<svc>/current" (symlink managed by the
# infra's activation script), env overrides from "${deployRsPath}/env".
{ config, lib, pkgs, ... }:
let
  inherit (lib) mkOption mkEnableOption types;
  rtLib = import ../lib { inherit lib; };
  rtPkgs = import ../packages { inherit pkgs lib; };

  # Environment= needs quoting (spaces split assignments) and %-escaping (specifiers).
  quoteEnv = line: "\"" + lib.escape [ "\\" "\"" ] (lib.replaceStrings [ "%" ] [ "%%" ] line) + "\"";

  commonOptions = service: svc:
    let cfg = config.services.rtSpeech.${service}; in
    {
      enable = mkEnableOption "rt-${service} speech service";
      package = mkOption {
        type = types.package;
        default = rtPkgs.${service}.${rtLib.variantForDevice.${cfg.device}}
          or (throw "services.rtSpeech.${service}: no prebuilt bundle for device '${cfg.device}' (spec D2: rt-tts has no ROCm variant yet); set `package` or `deployRsPath`");
        defaultText = lib.literalExpression "rtSpeech.${service}.<variant matching device>";
        description = "Bundle providing bin/rt-${service}. Used only when deployRsPath is empty.";
      };
      deployRsPath = mkOption {
        type = types.str;
        default = "";
        example = "${svc.stateDir}/current";
        description = "deploy-rs managed bundle symlink. When set, ExecStart and EnvironmentFile use it and `package` is not built.";
      };
      host = mkOption { type = types.str; default = "127.0.0.1"; description = "Bind address (RT_HOST). Use 0.0.0.0 together with openFirewall for remote edges."; };
      port = mkOption { type = types.port; default = svc.port; description = "Listen port (RT_PORT)."; };
      device = mkOption { type = types.enum [ "cpu" "cuda" "rocm" ]; default = "cpu"; description = "Compute device (RT_DEVICE); also selects the bundle variant and GPU sandbox rules."; };
      model = mkOption { type = types.nullOr types.str; default = null; description = "Model id/path (RT_MODEL). null = service default."; };
      configState = mkOption { type = types.nullOr types.str; default = "${svc.stateDir}/runtime-config.json"; description = "Runtime config state file (RT_CONFIG_STATE); null = not emitted."; };
      modelDir = mkOption { type = types.str; default = "${svc.stateDir}/models"; description = "Model cache (RT_MODEL_DIR; HF_HOME=<modelDir>/hf)."; };
      apiTokenFile = mkOption {
        type = types.nullOr types.str;
        default = null;
        example = "/run/secrets/rt-speech-token";
        description = "Bearer token file (outside the Nix store, may be root 0400). Passed via LoadCredential; RT_API_TOKEN_FILE=/run/credentials/rt-${service}.service/api-token.";
      };
      openFirewall = mkOption { type = types.bool; default = false; description = "Open port on firewallInterfaces only."; };
      firewallInterfaces = mkOption { type = types.listOf types.str; default = [ "tailscale0" ]; description = "Interfaces on which openFirewall opens the port."; };
      idleUnloadMinutes = mkOption {
        type = types.nullOr types.ints.unsigned;
        default = null;
        example = 15;
        description = "RT_IDLE_UNLOAD_MINUTES: load the model on the first request, unload it after this many minutes without requests (0 = load at start, never unload). null = not emitted (service default 15).";
      };
      logLevel = mkOption { type = types.enum [ "debug" "info" "warning" "error" ]; default = "info"; description = "RT_LOG_LEVEL."; };
      rocmGfxVersion = mkOption { type = types.str; default = ""; example = "9.0.0"; description = "HSA_OVERRIDE_GFX_VERSION for device = rocm (empty = unset)."; };
      memoryDenyWriteExecute = mkOption {
        type = types.bool;
        default = cfg.device == "cpu" && service != "tts";
        defaultText = lib.literalExpression ''device == "cpu" && service != "tts"'';
        description = "systemd MemoryDenyWriteExecute. GPU runtimes and torch/oneDNN JIT need W+X memory.";
      };
      extraEnv = mkOption { type = types.attrsOf types.str; default = { }; description = "Extra environment, appended last."; };
    };

  computeTypeOption = mkOption { type = types.nullOr types.str; default = null; example = "int8_float16"; description = "RT_COMPUTE_TYPE. null = service default."; };

  specificOptions = {
    asr = {
      computeType = computeTypeOption;
      language = mkOption { type = types.nullOr types.str; default = null; description = "Default language (RT_ASR_LANGUAGE); null = auto-detect."; };
      beamSize = mkOption { type = types.nullOr types.ints.positive; default = null; description = "RT_ASR_BEAM_SIZE; null = service default (1 on cpu, 3 on cuda — sub-project 5)."; };
      languages = mkOption { type = types.listOf types.str; default = [ "pl" "uk" "ru" "en" ]; description = "Allowed languages (RT_ASR_LANGUAGES, comma-joined)."; };
      vad = mkOption { type = types.bool; default = true; description = "RT_ASR_VAD."; };
      chunkSeconds = mkOption { type = types.nullOr types.float; default = null; description = "RT_ASR_CHUNK_SECONDS; null = not emitted (service default)."; };
    };
    tts = {
      voice = mkOption { type = types.str; default = "af_heart"; description = "RT_TTS_VOICE."; };
      speed = mkOption { type = types.float; default = 1.0; description = "RT_TTS_SPEED."; };
      sampleRate = mkOption { type = types.ints.positive; default = 24000; description = "RT_TTS_SAMPLE_RATE."; };
    };
  };

  mkServiceConfig = service: svc:
    let
      cfg = config.services.rtSpeech.${service};
      unit = svc.unit;
      binRoot = if cfg.deployRsPath != "" then cfg.deployRsPath else cfg.package;
      isGpu = cfg.device != "cpu";
    in
    lib.mkIf cfg.enable {
      assertions = [{
        assertion = cfg.apiTokenFile == null || !(lib.hasPrefix builtins.storeDir cfg.apiTokenFile);
        message = "services.rtSpeech.${service}.apiTokenFile must not point into the Nix store";
      }];
      warnings = lib.optional (cfg.openFirewall && cfg.host == "127.0.0.1")
        "services.rtSpeech.${service}: openFirewall is set but host is 127.0.0.1, so nothing listens on ${lib.concatStringsSep ", " cfg.firewallInterfaces}";

      users.users.${unit} = { isSystemUser = true; group = unit; home = svc.stateDir; description = "rt-speech ${service} service"; };
      users.groups.${unit} = { };

      systemd.tmpfiles.rules = [ "d ${cfg.modelDir} 0750 ${unit} ${unit} - -" ];

      systemd.services.${unit} = {
        description = "rt-speech ${service} service";
        after = [ "network-online.target" ];
        wants = [ "network-online.target" ];
        wantedBy = [ "multi-user.target" ];
        unitConfig = {
          StartLimitIntervalSec = "5min";
          StartLimitBurst = 5;
        } // lib.optionalAttrs (cfg.deployRsPath != "") {
          # Before the first deploy-rs push the unit is skipped instead of restart-looping.
          ConditionPathExists = "${cfg.deployRsPath}/bin/rt-${service}";
        };
        serviceConfig = {
          Type = "simple";
          ExecStart = "${binRoot}/bin/rt-${service}";
          Environment = map quoteEnv (rtLib.envLines service cfg);
          # Later files override Environment= (re-rendered by the infra on every deploy-rs push).
          EnvironmentFile = lib.optional (cfg.deployRsPath != "") "-${cfg.deployRsPath}/env";
          LoadCredential = lib.optional (cfg.apiTokenFile != null) "api-token:${cfg.apiTokenFile}";

          User = unit;
          Group = unit;
          StateDirectory = unit;
          StateDirectoryMode = "0750";
          WorkingDirectory = svc.stateDir;
          ReadWritePaths = lib.optional (!(lib.hasPrefix "${svc.stateDir}/" cfg.modelDir)) cfg.modelDir;

          PrivateTmp = true;
          ProtectSystem = "strict";
          ProtectHome = true;
          NoNewPrivileges = true;
          ProtectKernelTunables = true;
          ProtectKernelModules = true;
          ProtectKernelLogs = true;
          ProtectControlGroups = true;
          ProtectClock = true;
          ProtectHostname = true;
          RestrictNamespaces = true;
          RestrictRealtime = true;
          RestrictSUIDSGID = true;
          LockPersonality = true;
          SystemCallArchitectures = "native";
          CapabilityBoundingSet = "";
          RestrictAddressFamilies = [ "AF_INET" "AF_INET6" "AF_UNIX" "AF_NETLINK" ];
          MemoryDenyWriteExecute = cfg.memoryDenyWriteExecute;
          PrivateDevices = !isGpu;
          SupplementaryGroups = lib.optionals isGpu [ "video" "render" ];
          DeviceAllow =
            lib.optionals (cfg.device == "cuda") [
              "/dev/nvidiactl rw"
              "/dev/nvidia0 rw"
              "/dev/nvidia-modeset rw"
              "/dev/nvidia-uvm rw"
              "/dev/nvidia-uvm-tools rw"
            ]
            ++ lib.optionals (cfg.device == "rocm") [ "/dev/kfd rw" "char-drm rw" ];

          LimitNOFILE = 65536; # no LimitNPROC: it counts threads of the user (CT2/torch pools)
          Restart = "on-failure";
          RestartSec = "5s";
          TimeoutStartSec = "60s";
          TimeoutStopSec = "15s";
          SyslogIdentifier = unit;
        };
      };

      networking.firewall.interfaces = lib.mkIf cfg.openFirewall
        (lib.genAttrs cfg.firewallInterfaces (_: { allowedTCPPorts = [ cfg.port ]; }));
    };
in
{
  options.services.rtSpeech = lib.mapAttrs
    (service: svc: commonOptions service svc // specificOptions.${service})
    rtLib.services;

  config = lib.mkMerge (lib.mapAttrsToList mkServiceConfig rtLib.services);
}
