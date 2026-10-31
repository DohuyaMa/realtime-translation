{ pkgs, self, home-manager }:
let
  lib = pkgs.lib;
  fakeEdge = pkgs.writeShellScriptBin "translator-ui" "exit 0";
  # Real Home Manager config (throws on failed assertions): used for positive configs.
  hm = extra: home-manager.lib.homeManagerConfiguration {
    inherit pkgs;
    modules = [
      self.homeManagerModules.rt-translator
      { home.username = "tester"; home.homeDirectory = "/srv/tester"; home.stateVersion = "25.11"; }
      extra
    ];
  };
  # Bare module-system eval of the same module with stub options, so failed assertions can be
  # inspected (HM itself throws on `.config` access when any assertion fails).
  stub = extra: lib.evalModules {
    modules = [
      self.homeManagerModules.rt-translator
      ({ lib, ... }: {
        config._module.args.pkgs = pkgs;
        options = {
          assertions = lib.mkOption { type = lib.types.listOf lib.types.raw; default = [ ]; };
          home.packages = lib.mkOption { type = lib.types.listOf lib.types.raw; default = [ ]; };
          home.sessionVariables = lib.mkOption { type = lib.types.attrsOf lib.types.raw; default = { }; };
          systemd.user.services = lib.mkOption { type = lib.types.attrsOf lib.types.raw; default = { }; };
          xdg.configFile = lib.mkOption { type = lib.types.attrsOf lib.types.raw; default = { }; };
        };
      })
      extra
    ];
  };
  translate = hm {
    rt-translator = {
      enable = true;
      package = fakeEdge;
      mode = "translate";
      django = { urls = [ "http://127.0.0.1:8000" "http://dj:8000" ]; tokenFile = "/run/user/1000/dj-token"; };
      local.tts = null;
      allowedServices = [ "llm:cyborg" "tts:cyborg" ];
    };
  };
  transcribe = hm { rt-translator = { enable = true; package = fakeEdge; autostart = false; virtualSinks.enable = false; }; };
  missingLlm = stub { rt-translator = { enable = true; package = fakeEdge; mode = "translate"; local.llm = null; }; };
  missingTts = stub { rt-translator = { enable = true; package = fakeEdge; mode = "translate"; local.tts = null; }; };
  noDjangoToken = stub { rt-translator = { enable = true; package = fakeEdge; django.urls = [ "http://dj:8000" ]; }; };
  storeToken = stub { rt-translator = { enable = true; package = fakeEdge; django = { urls = [ "http://dj:8000" ]; tokenFile = "${fakeEdge}/secret"; }; }; };
  # Names the assertion that must fire: true only if some failing assertion's message contains `sub`.
  failsWith = sub: cfg:
    lib.any (a: !a.assertion && lib.hasInfix sub a.message) cfg.config.assertions;
  # Sanity: no assertion fails for the positive configs.
  noFailures = cfg: lib.all (a: a.assertion) cfg.config.assertions;
  env = cfg: cfg.config.systemd.user.services.rt-app.Service.Environment;
  edgeEnv = cfg: builtins.filter (lib.hasPrefix "RT_EDGE_") (env cfg);

  failures = lib.runTests {
    testTranslateEnv = {
      expr = edgeEnv translate;
      expected = [
        "RT_EDGE_ALLOWED_SERVICES=llm:cyborg,tts:cyborg"
        "RT_EDGE_DJANGO_TOKEN_FILE=/run/user/1000/dj-token"
        "RT_EDGE_DJANGO_URLS=http://127.0.0.1:8000,http://dj:8000"
        "RT_EDGE_LLM_MODEL=local"
        "RT_EDGE_LOCAL_ASR_URL=http://127.0.0.1:8091"
        "RT_EDGE_LOCAL_LLM_URL=http://127.0.0.1:8080"
        "RT_EDGE_LOCAL_TOKEN_FILE=/run/llama/local-token"
        "RT_EDGE_MODE=translate"
      ];
    };
    testSessionVariables = { expr = translate.config.home.sessionVariables.RT_EDGE_MODE; expected = "translate"; };
    testOnlyEdgeUnit = { expr = builtins.attrNames translate.config.systemd.user.services; expected = [ "rt-app" ]; };
    testNoSockets = { expr = translate.config.systemd.user.sockets; expected = { }; };
    testSinksDropIn = { expr = translate.config.xdg.configFile ? "pipewire/pipewire.conf.d/30-rt-virtual-sinks.conf"; expected = true; };
    testTranscribeDefaults = {
      expr = {
        env = edgeEnv transcribe;
        install = transcribe.config.systemd.user.services.rt-app.Install;
        sinks = transcribe.config.xdg.configFile ? "pipewire/pipewire.conf.d/30-rt-virtual-sinks.conf";
      };
      expected = {
        env = [
          "RT_EDGE_LLM_MODEL=local"
          "RT_EDGE_LOCAL_ASR_URL=http://127.0.0.1:8091"
          "RT_EDGE_LOCAL_LLM_URL=http://127.0.0.1:8080"
          "RT_EDGE_LOCAL_TOKEN_FILE=/run/llama/local-token"
          "RT_EDGE_LOCAL_TTS_URL=http://127.0.0.1:8093"
        ];
        install = { };
        sinks = false;
      };
    };
    testPositiveConfigsClean = { expr = noFailures translate && noFailures transcribe; expected = true; };
    testTranslateNeedsLlm = { expr = failsWith "requires local.llm and local.tts" missingLlm; expected = true; };
    testTranslateNeedsTts = { expr = failsWith "mode = \"translate\" requires" missingTts; expected = true; };
    testDjangoNeedsToken = { expr = failsWith "requires django.tokenFile" noDjangoToken; expected = true; };
    testTokenNotInStore = { expr = failsWith "must not point into the Nix store" storeToken; expected = true; };
  };
in
pkgs.runCommand "rt-hm-edge-check" { activation = translate.activationPackage; } (
  if failures == [ ] then "touch $out"
  else ''
    echo "home-manager module failures:"
    echo ${lib.escapeShellArg (builtins.toJSON failures)}
    exit 1
  ''
)
