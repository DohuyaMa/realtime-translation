{ config, lib, pkgs, ... }:

with lib;

let
  cfg = config.rt.audio.virtualSinks;
in
{
  options.rt.audio.virtualSinks = {
    enable = mkEnableOption "RT virtual PipeWire sinks";

    inputName = mkOption {
      type = types.str;
      default = "rt_virtual_input";
    };

    outputName = mkOption {
      type = types.str;
      default = "rt_virtual_output";
    };
  };

  config = mkIf cfg.enable {
    services.pipewire = {
      enable = true;
      pulse.enable = true;
      alsa.support32Bit = true;

      configPackages = [
        (pkgs.writeTextDir
          "share/pipewire/pipewire.conf.d/30-rt-virtual-sinks.conf"
          ''
            context.modules = [
              {
                name = libpipewire-module-null-sink
                args = {
                  node.name = "${cfg.inputName}"
                  node.description = "RT Virtual Input"
                  media.class = "Audio/Sink"
                  stream.props = { audio.position = [ FL FR ]; }
                }
              }
              {
                name = libpipewire-module-null-sink
                args = {
                  node.name = "${cfg.outputName}"
                  node.description = "RT Virtual Output"
                  media.class = "Audio/Sink"
                  stream.props = { audio.position = [ FL FR ]; }
                }
              }
            ]
          '')
      ];
    };
  };
}