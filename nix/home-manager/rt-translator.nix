# Edge only: UI + capture + VAD + playback + PipeWire virtual devices.
# Speech services run as NixOS system units (nixosModules.rt-speech), possibly on other hosts.
{ config, lib, pkgs, ... }:
let
  inherit (lib) mkOption mkEnableOption types;
  cfg = config.rt-translator;
  rtPkgs = import ../packages { inherit pkgs lib; };
  rtLib = import ../lib { inherit lib; };
  inStore = p: p != null && lib.hasPrefix builtins.storeDir p;

  csv = lib.concatStringsSep ",";
  edgeEnv =
    lib.optionalAttrs (cfg.llm.model != null) { RT_EDGE_LLM_MODEL = cfg.llm.model; }
    // lib.optionalAttrs (cfg.mode != null) { RT_EDGE_MODE = cfg.mode; }
    // lib.optionalAttrs (cfg.django.urls != [ ]) { RT_EDGE_DJANGO_URLS = csv cfg.django.urls; }
    // lib.optionalAttrs (cfg.django.tokenFile != null) { RT_EDGE_DJANGO_TOKEN_FILE = cfg.django.tokenFile; }
    // lib.optionalAttrs (cfg.local.llm != null) { RT_EDGE_LOCAL_LLM_URL = cfg.local.llm; }
    // lib.optionalAttrs (cfg.local.asr != null) { RT_EDGE_LOCAL_ASR_URL = cfg.local.asr; }
    // lib.optionalAttrs (cfg.local.tts != null) { RT_EDGE_LOCAL_TTS_URL = cfg.local.tts; }
    // lib.optionalAttrs (cfg.localTokenFile != null) { RT_EDGE_LOCAL_TOKEN_FILE = cfg.localTokenFile; }
    // lib.optionalAttrs (cfg.allowedServices != null) { RT_EDGE_ALLOWED_SERVICES = csv cfg.allowedServices; };

  hasSource = kind: cfg.local.${kind} != null || cfg.django.urls != [ ];
in
{
  options.rt-translator = {
    enable = mkEnableOption "real-time translator edge app";
    package = mkOption {
      type = types.package;
      default = rtPkgs.edge;
      defaultText = lib.literalExpression "rtSpeech.edge";
      description = "Edge app package providing translator-ui.";
    };
    mode = mkOption {
      type = types.nullOr (types.enum [ "transcribe" "translate" ]);
      default = null;
      description = "null = auto; transcribe = ASR only; translate = ASR -> LLM -> TTS.";
    };
    django = {
      urls = mkOption {
        type = types.listOf types.str;
        default = [ ];
        example = [ "http://127.0.0.1:8000" "http://cyborg:8000" ];
        description = "Django instances for service discovery (GET /api/llm/app-services/), own host first.";
      };
      tokenFile = mkOption { type = types.nullOr types.str; default = null; description = "File with the user's DRF token for django.urls (outside the Nix store)."; };
    };
    local = {
      llm = mkOption { type = types.nullOr types.str; default = "http://127.0.0.1:8080"; description = "Local llama worker (tried first; null = none on this host)."; };
      asr = mkOption { type = types.nullOr types.str; default = "http://127.0.0.1:8091"; description = "Local rt-asr (tried first; null = none on this host)."; };
      tts = mkOption { type = types.nullOr types.str; default = "http://127.0.0.1:8093"; description = "Local rt-tts (tried first; null = none on this host)."; };
    };
    localTokenFile = mkOption {
      type = types.nullOr types.str;
      default = "/run/llama/local-token";
      description = "Host-local worker token (rewritten on every worker start; user must be in group llama-clients). Used for the local worker when Django is unreachable.";
    };
    allowedServices = mkOption {
      type = types.nullOr (types.listOf types.str);
      default = null;
      example = [ "llm:cyborg" "asr:cyborg" "tts:sktop" ];
      description = "Discovered services the app may use, as <kind>:<node>; null = all (changeable in the UI unless set here).";
    };
    llm.model = mkOption { type = types.nullOr types.str; default = null; description = "LLM model name; null = chosen in the UI (Переклад tab, default \"local\"). Set here it is read-only in the UI."; };
    autostart = mkOption { type = types.bool; default = true; description = "Start translator-ui with the graphical session."; };
    virtualSinks.enable = mkOption { type = types.bool; default = true; description = "Create the PipeWire devices rt_virtual_input (sink), rt_virtual_output (sink) and rt_virtual_mic (microphone carrying rt_virtual_output, for other apps)."; };
  };

  config = lib.mkIf cfg.enable {
    assertions = [
      {
        assertion = cfg.mode != "translate" || (hasSource "llm" && hasSource "tts");
        message = ''rt-translator: mode = "translate" requires local.llm and local.tts, or django.urls'';
      }
      {
        assertion = cfg.django.urls == [ ] || cfg.django.tokenFile != null;
        message = "rt-translator: django.urls requires django.tokenFile";
      }
      {
        assertion = !(lib.any inStore [ cfg.django.tokenFile cfg.localTokenFile ]);
        message = "rt-translator: token files must not point into the Nix store";
      }
    ];

    home.packages = [ cfg.package pkgs.pulseaudio ];
    home.sessionVariables = edgeEnv;

    systemd.user.services.rt-app = {
      Unit = {
        Description = "Real-time Translator (edge UI)";
        After = [ "graphical-session.target" "pipewire.service" ];
        PartOf = [ "graphical-session.target" ];
      };
      Service = {
        Type = "simple";
        ExecStart = lib.getExe cfg.package;
        Restart = "on-failure";
        RestartSec = 5;
        Environment = [
          "PATH=${lib.makeBinPath [ pkgs.pulseaudio ]}:/run/wrappers/bin:/etc/profiles/per-user/%u/bin:/run/current-system/sw/bin"
        ] ++ lib.mapAttrsToList (n: v: "${n}=${v}") edgeEnv;
        PassEnvironment = "WAYLAND_DISPLAY DISPLAY XDG_RUNTIME_DIR DBUS_SESSION_BUS_ADDRESS QT_QPA_PLATFORM";
      };
      Install = lib.optionalAttrs cfg.autostart { WantedBy = [ "graphical-session.target" ]; };
    };

    xdg.configFile."pipewire/pipewire.conf.d/30-rt-virtual-sinks.conf" = lib.mkIf cfg.virtualSinks.enable {
      text = rtLib.virtualDevicesConf {
        inputName = "rt_virtual_input";
        outputName = "rt_virtual_output";
        micName = "rt_virtual_mic";
      };
    };
  };
}
