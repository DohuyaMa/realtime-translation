{ config, lib, ... }: 

let
  cfg = config.rt.runtime;
in
{
  options.rt.runtime = {
    baseDir = lib.mkOption {
      type = lib.types.str;
      default = "%t/rt";  # XDG runtime directory
      description = "Base directory for runtime files";
    };
    
    socketDir = lib.mkOption {
      type = lib.types.str;
      default = "${config.rt.runtime.baseDir}";
      description = "Directory for socket files";
    };
  };

  config = {
    # Default runtime configuration
    rt.runtime.baseDir = lib.mkDefault "%t/rt";
  };
}