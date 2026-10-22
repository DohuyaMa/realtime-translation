{ pkgs, self, home-manager }:
let
  lib = pkgs.lib;
  fakeEdge = pkgs.writeShellScriptBin "translator-ui" "exit 0";
  hm = extra: home-manager.lib.homeManagerConfiguration {
    inherit pkgs;
    modules = [
      self.homeManagerModules.rt-translator
      { home.username = "tester"; home.homeDirectory = "/srv/tester"; home.stateVersion = "25.11"; }
      extra
    ];
  };
  translate = hm {
    rt-translator = {
      enable = true;
      package = fakeEdge;
      mode = "translate";
      endpoints = { asr = [ "http://gpu:8091" "http://gpu2:8091" ]; tts = [ "http://gpu:8093" ]; };
      llm = { djangoEndpoints = [ "http://dj:8000" ]; djangoTokenFile = "/run/user/1000/dj-token"; service = "llm-a"; };
      apiTokenFile = "/run/user/1000/rt-token";
    };
  };
  transcribe = hm { rt-translator = { enable = true; package = fakeEdge; autostart = false; virtualSinks.enable = false; }; };
  missingLlm = hm { rt-translator = { enable = true; package = fakeEdge; mode = "translate"; endpoints.tts = [ "http://gpu:8093" ]; }; };
  missingTts = hm { rt-translator = { enable = true; package = fakeEdge; mode = "translate"; llm.directUrl = "http://llm:1"; }; };
  noDjangoToken = hm { rt-translator = { enable = true; package = fakeEdge; llm.djangoEndpoints = [ "http://dj:8000" ]; }; };
  storeToken = hm { rt-translator = { enable = true; package = fakeEdge; apiTokenFile = "${fakeEdge}/secret"; }; };
  evalFails = cfg: !(builtins.tryEval (builtins.deepSeq cfg.activationPackage.drvPath true)).success;
  env = cfg: cfg.config.systemd.user.services.rt-app.Service.Environment;
  edgeEnv = cfg: builtins.filter (lib.hasPrefix "RT_EDGE_") (env cfg);

  failures = lib.runTests {
    testTranslateEnv = {
      expr = edgeEnv translate;
      expected = [
        "RT_EDGE_API_TOKEN_FILE=/run/user/1000/rt-token"
        "RT_EDGE_ASR_URLS=http://gpu:8091,http://gpu2:8091"
        "RT_EDGE_DJANGO_TOKEN_FILE=/run/user/1000/dj-token"
        "RT_EDGE_DJANGO_URLS=http://dj:8000"
        "RT_EDGE_LLM_MODEL=local"
        "RT_EDGE_LLM_SERVICE=llm-a"
        "RT_EDGE_MODE=translate"
        "RT_EDGE_TTS_URLS=http://gpu:8093"
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
      expected = { env = [ "RT_EDGE_ASR_URLS=http://127.0.0.1:8091" "RT_EDGE_LLM_MODEL=local" ]; install = { }; sinks = false; };
    };
    testTranslateNeedsLlm = { expr = evalFails missingLlm; expected = true; };
    testTranslateNeedsTts = { expr = evalFails missingTts; expected = true; };
    testDjangoNeedsToken = { expr = evalFails noDjangoToken; expected = true; };
    testTokenNotInStore = { expr = evalFails storeToken; expected = true; };
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
