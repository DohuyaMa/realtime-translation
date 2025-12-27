{ self, packages }:
{
  apps = {
    default = {
      type = "app";
      program = "${packages.default}/bin/translator-ui";
    };
  };
}