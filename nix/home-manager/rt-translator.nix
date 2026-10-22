# Edge only: UI + capture + VAD + playback + PipeWire virtual sinks.
# Speech services run as NixOS system units (nixosModules.rt-speech), possibly on other hosts.
{ config, lib, pkgs, ... }:
let
  inherit (lib) mkOption mkEnableOption types;
  cfg = config.rt-translator;
  rtPkgs = import ../packages { inherit pkgs lib; };
  inStore = p: p != null && lib.hasPrefix builtins.storeDir p;

  csv = lib.concatStringsSep ",";
  edgeEnv =
    {
      RT_EDGE_LLM_MODEL = cfg.llm.model;
    }
    // lib.optionalAttrs (cfg.endpoints.asr != [ ]) { RT_EDGE_ASR_URLS = csv cfg.endpoints.asr; }
    // lib.optionalAttrs (cfg.mode != null) { RT_EDGE_MODE = cfg.mode; }
    // lib.optionalAttrs (cfg.endpoints.tts != [ ]) { RT_EDGE_TTS_URLS = csv cfg.endpoints.tts; }
    // lib.optionalAttrs (cfg.llm.djangoEndpoints != [ ]) { RT_EDGE_DJANGO_URLS = csv cfg.llm.djangoEndpoints; }
    // lib.optionalAttrs (cfg.llm.djangoTokenFile != null) { RT_EDGE_DJANGO_TOKEN_FILE = cfg.llm.djangoTokenFile; }
    // lib.optionalAttrs (cfg.llm.service != null) { RT_EDGE_LLM_SERVICE = cfg.llm.service; }
    // lib.optionalAttrs (cfg.llm.directUrl != null) { RT_EDGE_LLM_DIRECT_URL = cfg.llm.directUrl; }
    // lib.optionalAttrs (cfg.llm.directTokenFile != null) { RT_EDGE_LLM_DIRECT_TOKEN_FILE = cfg.llm.directTokenFile; }
    // lib.optionalAttrs (cfg.apiTokenFile != null) { RT_EDGE_API_TOKEN_FILE = cfg.apiTokenFile; };

  sinkModule = name: description: ''
    {
      name = libpipewire-module-null-sink
      args = {
        node.name = "${name}"
        node.description = "${description}"
        media.class = "Audio/Sink"
        stream.props = { audio.position = [ FL FR ]; }
      }
    }
  '';
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
    endpoints = {
      asr = mkOption { type = types.listOf types.str; default = [ "http://127.0.0.1:8091" ]; description = "rt-asr base URLs."; };
      tts = mkOption { type = types.listOf types.str; default = [ ]; description = "rt-tts base URLs."; };
    };
    llm = {
      djangoEndpoints = mkOption { type = types.listOf types.str; default = [ ]; description = "Django LLM-lease endpoints."; };
      djangoTokenFile = mkOption { type = types.nullOr types.str; default = null; description = "Bearer token file for Django endpoints."; };
      service = mkOption { type = types.nullOr types.str; default = null; description = "LLM service name to lease."; };
      directUrl = mkOption { type = types.nullOr types.str; default = null; description = "Direct OpenAI-compatible LLM URL."; };
      directTokenFile = mkOption { type = types.nullOr types.str; default = null; description = "Bearer token file for the direct LLM."; };
      model = mkOption { type = types.str; default = "local"; description = "LLM model name."; };
    };
    apiTokenFile = mkOption {
      type = types.nullOr types.str;
      default = null;
      description = "File with the bearer token for the speech services (outside the Nix store).";
    };
    autostart = mkOption { type = types.bool; default = true; description = "Start translator-ui with the graphical session."; };
    virtualSinks.enable = mkOption { type = types.bool; default = true; description = "Create rt_virtual_input/rt_virtual_output PipeWire sinks."; };
  };

  config = lib.mkIf cfg.enable {
    assertions = [
      {
        assertion = cfg.mode != "translate"
          || ((cfg.llm.djangoEndpoints != [ ] || cfg.llm.directUrl != null) && cfg.endpoints.tts != [ ]);
        message = ''rt-translator: mode = "translate" requires llm.djangoEndpoints or llm.directUrl, and endpoints.tts'';
      }
      {
        assertion = cfg.llm.djangoEndpoints == [ ] || cfg.llm.djangoTokenFile != null;
        message = "rt-translator: llm.djangoEndpoints requires llm.djangoTokenFile";
      }
      {
        assertion = !(lib.any inStore [ cfg.apiTokenFile cfg.llm.djangoTokenFile cfg.llm.directTokenFile ]);
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
      text = ''
        context.modules = [
        ${sinkModule "rt_virtual_input" "RT-Virtual-Input"}
        ${sinkModule "rt_virtual_output" "RT-Virtual-Output"}
        ]
      '';
    };
  };
}
