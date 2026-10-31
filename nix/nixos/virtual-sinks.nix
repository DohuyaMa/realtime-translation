{ config, lib, pkgs, ... }:

with lib;

let
  cfg = config.rt.audio.virtualSinks;
  rtLib = import ../lib { inherit lib; };
in
{
  options.rt.audio.virtualSinks = {
    enable = mkEnableOption "RT virtual PipeWire devices (input sink, output sink -> virtual microphone)";

    inputName = mkOption {
      type = types.str;
      default = "rt_virtual_input";
    };

    outputName = mkOption {
      type = types.str;
      default = "rt_virtual_output";
    };

    micName = mkOption {
      type = types.str;
      default = "rt_virtual_mic";
      description = "Audio/Source carrying what is played into outputName (the translation), for other apps.";
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
          (rtLib.virtualDevicesConf { inherit (cfg) inputName outputName micName; }))
      ];
    };
  };
}
