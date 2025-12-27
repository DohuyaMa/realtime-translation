{ config, lib, pkgs, ... }:

let
  cfg = config.services.realtime-translator;
in
{
  options = {
    services.realtime-translator.pipewire = {
      enable = lib.mkEnableOption "Real-time Translator PipeWire Configuration";
    };
  };

  config = lib.mkIf cfg.pipewire.enable {
    # This module handles PipeWire configuration for the real-time translator
    # It may include virtual sink configurations and audio routing
    environment.etc."pipewire/pipewire.conf.d/99-realtime-translator.conf".text = ''
      {
        "context.modules" : [
          {
            "name" : "libpipewire-module-virtual-sink",
            "args" : {
              "node.description" : "Real-time Translator Virtual Sink",
              "master" : "auto"
            },
            "flags" : [ "ifexists" ]
          }
        ]
      }
    '';
  };
}