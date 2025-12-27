{ config, lib, ... }: 

let
  cfg = config.rt.runtime;
in
{
  options.rt.runtime.env = {
    runtimeDir = lib.mkOption {
      type = lib.types.str;
      default = cfg.baseDir;
      description = "Environment variable for runtime directory";
    };
    
    socketDir = lib.mkOption {
      type = lib.types.str;
      default = cfg.socketDir;
      description = "Environment variable for socket directory";
    };
  };

  config = {
    # Environment variables for runtime configuration
    rt.runtime.env = {
      runtimeDir = lib.mkDefault cfg.baseDir;
      socketDir = lib.mkDefault cfg.socketDir;
    };
  };
}