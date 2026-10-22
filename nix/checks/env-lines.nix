{ pkgs, rtLib }:
let
  lib = pkgs.lib;
  base = {
    host = "0.0.0.0";
    port = 8091;
    device = "cpu";
    modelDir = "/var/lib/rt-asr/models";
    logLevel = "info";
    model = null;
    computeType = null;
    rocmGfxVersion = "";
    apiTokenFile = null;
    extraEnv = { };
  };
  # beamSize defaults to null: device-dependent defaults live in Python.
  asr = base // { language = null; beamSize = null; vad = true; };
  tts = (removeAttrs base [ "computeType" ]) // { port = 8093; modelDir = "/var/lib/rt-tts/models"; voice = "af_heart"; speed = 1.0; sampleRate = 24000; };
  common = port: dir: [
    "PYTHONUNBUFFERED=1"
    "RT_HOST=0.0.0.0"
    "RT_PORT=${toString port}"
    "RT_DEVICE=cpu"
    "RT_MODEL_DIR=${dir}"
    "HF_HOME=${dir}/hf"
    "RT_LOG_LEVEL=info"
  ];
  failsToEval = v: !(builtins.tryEval (builtins.deepSeq v true)).success;
  failures = lib.runTests {
    testAsrMinimal = {
      expr = rtLib.envLines "asr" asr;
      expected = common 8091 "/var/lib/rt-asr/models" ++ [ "RT_ASR_VAD=true" ];
    };
    testAsrFull = {
      expr = rtLib.envLines "asr" (asr // { model = "whisper-large-v3-turbo"; computeType = "int8_float16"; language = "uk"; beamSize = 5; vad = false; });
      expected = common 8091 "/var/lib/rt-asr/models" ++ [
        "RT_MODEL=whisper-large-v3-turbo"
        "RT_COMPUTE_TYPE=int8_float16"
        "RT_ASR_LANGUAGE=uk"
        "RT_ASR_BEAM_SIZE=5"
        "RT_ASR_VAD=false"
      ];
    };
    testTtsHasNoComputeType = {
      expr = rtLib.envLines "tts" tts;
      expected = common 8093 "/var/lib/rt-tts/models" ++ [ "RT_TTS_VOICE=af_heart" "RT_TTS_SPEED=1.000000" "RT_TTS_SAMPLE_RATE=24000" ];
    };
    testConfigState = {
      expr = [
        (builtins.filter (lib.hasPrefix "RT_CONFIG_STATE") (rtLib.envLines "asr" (asr // { configState = "/var/lib/rt-asr/runtime-config.json"; })))
        (builtins.filter (lib.hasPrefix "RT_CONFIG_STATE") (rtLib.envLines "asr" (asr // { configState = null; })))
      ];
      expected = [ [ "RT_CONFIG_STATE=/var/lib/rt-asr/runtime-config.json" ] [ ] ];
    };
    testConfigStateAfterLogLevel = {
      expr = builtins.elemAt (rtLib.envLines "asr" (asr // { configState = "/s.json"; })) 7;
      expected = "RT_CONFIG_STATE=/s.json";
    };
    testRocmGfxOnlyOnRocm = {
      expr = builtins.filter (lib.hasPrefix "HSA_") (rtLib.envLines "asr" (asr // { device = "rocm"; rocmGfxVersion = "9.0.0"; }))
        ++ builtins.filter (lib.hasPrefix "HSA_") (rtLib.envLines "asr" (asr // { device = "cuda"; rocmGfxVersion = "9.0.0"; }));
      expected = [ "HSA_OVERRIDE_GFX_VERSION=9.0.0" ];
    };
    testExtraEnvSortedAndLast = {
      expr = lib.takeEnd 2 (rtLib.envLines "asr" (asr // { extraEnv = { ZED = "1"; ALPHA = "a b"; }; }));
      expected = [ "ALPHA=a b" "ZED=1" ];
    };
    testAlwaysHostPortDevice = {
      # infra R2: deploy-rs health check reads RT_PORT from the rendered env file
      expr = map (s: builtins.head (lib.splitString "=" s)) (lib.take 4 (rtLib.envLines "tts" tts));
      expected = [ "PYTHONUNBUFFERED" "RT_HOST" "RT_PORT" "RT_DEVICE" ];
    };
    testTokenPathLiteral = {
      # infra R3: literal credential path (systemd does not expand %d inside EnvironmentFile)
      expr = builtins.filter (lib.hasPrefix "RT_API_TOKEN_FILE") (rtLib.envLines "asr" (asr // { apiTokenFile = "/run/secrets/t"; }));
      expected = [ "RT_API_TOKEN_FILE=/run/credentials/rt-asr.service/api-token" ];
    };
    testNoTokenWithoutFile = {
      expr = builtins.any (lib.hasPrefix "RT_API_TOKEN_FILE") (rtLib.envLines "asr" asr);
      expected = false;
    };
    testNewlineRejected = { expr = failsToEval (rtLib.envLines "asr" (asr // { model = "a\nb"; })); expected = true; };
    testUnknownServiceRejected = { expr = failsToEval (rtLib.envLines "stt" asr); expected = true; };
    testMtServiceRejected = { expr = failsToEval (rtLib.envLines "mt" asr); expected = true; };
    testVariantForDevice = { expr = rtLib.variantForDevice; expected = { cpu = "cpu"; cuda = "gpu"; rocm = "rocm"; }; };
    testHealthCheck = {
      expr = rtLib.healthCheckCommand "asr" asr;
      expected = "curl -sf http://127.0.0.1:8091/health > /dev/null";
    };
  };
in
pkgs.runCommand "rt-speech-env-lines-test" { } (
  if failures == [ ] then "touch $out"
  else ''
    echo "envLines test failures:"
    echo ${lib.escapeShellArg (builtins.toJSON failures)}
    exit 1
  ''
)
