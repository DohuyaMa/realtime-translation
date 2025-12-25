{ packages }:
{
  apps = {
    default = {
      type = "app";
      program = "${packages.default}/bin/real-time-translator";
    };
  };
}