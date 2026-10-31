{ pkgs, self }:
let
  p = pkgs.extend self.overlays.default;
  info = drv: { inherit (drv.passthru) service variant port; main = drv.meta.mainProgram; };
in
assert info p.rtSpeech.asr.cpu == { service = "asr"; variant = "cpu"; port = 8091; main = "rt-asr"; };
assert info p.rtSpeech.asr.gpu == { service = "asr"; variant = "gpu"; port = 8091; main = "rt-asr"; };
assert info p.rtSpeech.tts.cpu == { service = "tts"; variant = "cpu"; port = 8093; main = "rt-tts"; };
assert !(p.rtSpeech.tts ? rocm);
assert info p.rtSpeech.vosk.cpu == { service = "vosk"; variant = "cpu"; port = 8092; main = "rt-vosk"; };
assert builtins.attrNames p.rtSpeech.vosk == [ "cpu" ];
assert p.rtSpeech.asr.cpu.drvPath == self.packages.x86_64-linux.rt-asr-cpu.drvPath;
# overlay adds only `rtSpeech`; GPU overrides stay scoped
assert builtins.attrNames (self.overlays.default p pkgs) == [ "rtSpeech" ];
assert p.python313.outPath == pkgs.python313.outPath;
assert p.python313Packages.ctranslate2.outPath == pkgs.python313Packages.ctranslate2.outPath;
assert p.ctranslate2.outPath == pkgs.ctranslate2.outPath;
assert builtins.elem "RT_PORT=8091" (p.rtSpeech.lib.envLines "asr" {
  listenAddresses = [ "127.0.0.1" ]; port = 8091; device = "cpu"; modelDir = "/var/lib/rt-asr/models"; logLevel = "info";
  model = null; computeType = null; rocmGfxVersion = ""; apiTokenFile = null; extraEnv = { };
  language = null; languages = [ "uk" ]; beamSize = null; vad = true; chunkSeconds = null;
});
pkgs.runCommand "rt-speech-overlay-check" { } "touch $out"
