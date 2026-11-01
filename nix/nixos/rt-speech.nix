# services.rtSpeech.{asr,phonetic,tts} — system units modeled on swarm-nix llama-service.nix.
# deploy-rs mode: deployRsPath = "/var/lib/rt-<svc>/current" (symlink managed by the
# infra's activation script), env overrides from "${deployRsPath}/env".
{ config, lib, pkgs, ... }:
let
  inherit (lib) mkOption mkEnableOption types;
  rtLib = import ../lib { inherit lib; };
  rtPkgs = import ../packages { inherit pkgs lib; };

  # Environment= needs quoting (spaces split assignments) and %-escaping (specifiers).
  quoteEnv = line: "\"" + lib.escape [ "\\" "\"" ] (lib.replaceStrings [ "%" ] [ "%%" ] line) + "\"";

  # The single GPU kind of `devices` ("cpu" when there is none): bundle variant + sandbox.
  gpuDevice = cfg: lib.findFirst (d: d != "cpu") "cpu" cfg.devices;
  hasGpu = cfg: gpuDevice cfg != "cpu";

  commonOptions = service: svc:
    let cfg = config.services.rtSpeech.${service}; in
    {
      enable = mkEnableOption "rt-${service} speech service";
      package = mkOption {
        type = types.package;
        default = rtPkgs.${service}.${rtLib.variantForDevice.${gpuDevice cfg}}
          or (throw "services.rtSpeech.${service}: no prebuilt bundle for device '${gpuDevice cfg}' (spec D2: rt-tts has no ROCm variant yet); set `package` or `deployRsPath`");
        defaultText = lib.literalExpression "rtSpeech.${service}.<variant matching the GPU entry of devices, else cpu>";
        description = "Bundle providing bin/rt-${service}. Used only when deployRsPath is empty.";
      };
      deployRsPath = mkOption {
        type = types.str;
        default = "";
        example = "${svc.stateDir}/current";
        description = "deploy-rs managed bundle symlink. When set, ExecStart and EnvironmentFile use it and `package` is not built.";
      };
      listenAddresses = mkOption {
        type = types.nonEmptyListOf types.str;
        default = [ "127.0.0.1" ];
        example = [ "127.0.0.1" "100.64.0.10" ];
        description = "IP addresses to listen on, one socket each (RT_LISTEN_ADDRESSES). Keep 127.0.0.1 for clients on this host and add the cluster (e.g. tailscale) address for remote edges; addresses not configured yet are bound anyway (IP_FREEBIND).";
      };
      port = mkOption { type = types.port; default = svc.port; description = "Listen port (RT_PORT)."; };
      device = mkOption { type = types.enum [ "cpu" "cuda" "rocm" ]; default = "cpu"; description = "Compute device at start (RT_DEVICE); must be one of `devices`."; };
      devices = mkOption {
        type = types.nonEmptyListOf (types.enum [ "cpu" "cuda" "rocm" ]);
        default = [ cfg.device ];
        defaultText = lib.literalExpression "[ device ]";
        example = [ "cpu" "cuda" ];
        description = "Devices the service may switch between at runtime (RT_DEVICES; the `device` choices of GET /config, POST /config reloads the model). At most one GPU kind; it selects the bundle variant (the GPU bundle also runs on cpu) and opens the GPU sandbox even while running on cpu.";
      };
      model = mkOption { type = types.nullOr types.str; default = null; description = "Model id/path (RT_MODEL; rt-phonetic: one CTC model alias, its language is the only one served). null = service default."; };
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
        default = !(hasGpu cfg) && !(lib.elem service [ "tts" "phonetic" ]);
        defaultText = lib.literalExpression ''devices == [ "cpu" ] && !(elem service [ "tts" "phonetic" ])'';
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
    # torch wav2vec2 CTC on cpu or cuda (no rocm bundle); computeType:
    # cpu float32 only, cuda float16 or float32; null = per device (keep null
    # when the device switches at runtime: a fixed float16 cannot start on cpu)
    phonetic = { computeType = computeTypeOption; };
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
      isGpu = hasGpu cfg;
      gpu = gpuDevice cfg;
    in
    lib.mkIf cfg.enable {
      assertions = [
        {
          assertion = cfg.apiTokenFile == null || !(lib.hasPrefix builtins.storeDir cfg.apiTokenFile);
          message = "services.rtSpeech.${service}.apiTokenFile must not point into the Nix store";
        }
        {
          assertion = lib.elem cfg.device cfg.devices;
          message = "services.rtSpeech.${service}.device '${cfg.device}' is not in devices ${builtins.toJSON cfg.devices}";
        }
        {
          assertion = service != "phonetic" || !(lib.elem "rocm" cfg.devices);
          message = "services.rtSpeech.phonetic runs on cpu or cuda (no ROCm torch bundle); devices = ${builtins.toJSON cfg.devices}";
        }
        {
          assertion = !(lib.elem "cuda" cfg.devices && lib.elem "rocm" cfg.devices);
          message = "services.rtSpeech.${service}.devices: cuda and rocm need different bundles; list at most one GPU kind";
        }
      ];
      warnings = lib.optional (cfg.openFirewall && lib.all (a: a == "127.0.0.1" || a == "::1") cfg.listenAddresses)
        "services.rtSpeech.${service}: openFirewall is set but listenAddresses are loopback only, so nothing listens on ${lib.concatStringsSep ", " cfg.firewallInterfaces}";

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
            lib.optionals (gpu == "cuda") [
              "/dev/nvidiactl rw"
              "/dev/nvidia0 rw"
              "/dev/nvidia-modeset rw"
              "/dev/nvidia-uvm rw"
              "/dev/nvidia-uvm-tools rw"
            ]
            ++ lib.optionals (gpu == "rocm") [ "/dev/kfd rw" "char-drm rw" ];

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
