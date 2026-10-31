{ pkgs, self, nixpkgs }:
let
  lib = pkgs.lib;
  fake = name: pkgs.writeShellScriptBin name "exit 0";
  evalWith = rtSpeech: (nixpkgs.lib.nixosSystem {
    modules = [
      self.nixosModules.rt-speech
      {
        nixpkgs.hostPlatform = "x86_64-linux";
        boot.isContainer = true;
        system.stateVersion = "26.05";
        services.rtSpeech = rtSpeech;
      }
    ];
  }).config;
  sc = config: unit: config.systemd.services.${unit}.serviceConfig;

  cpu = evalWith { asr = { enable = true; package = fake "rt-asr"; extraEnv.NOTE = ''say "hi" 100%''; apiTokenFile = "/run/secrets/rt"; }; };
  cuda = evalWith { asr = { enable = true; device = "cuda"; package = fake "rt-asr"; }; };
  # Runtime cpu<->cuda switch: starts on cpu, GPU bundle + GPU sandbox.
  switchable = evalWith { asr = { enable = true; device = "cpu"; devices = [ "cpu" "cuda" ]; }; };
  deviceNotListed = evalWith { asr = { enable = true; device = "cuda"; devices = [ "cpu" ]; package = fake "rt-asr"; }; };
  rocm = evalWith { asr = { enable = true; device = "rocm"; rocmGfxVersion = "9.0.0"; package = fake "rt-asr"; }; };
  deployed = evalWith { asr = { enable = true; deployRsPath = "/var/lib/rt-asr/current"; }; };
  ttsCpuNoMdwe = evalWith { tts = { enable = true; package = fake "rt-tts"; }; };
  ttsDefault = evalWith { tts = { enable = true; package = fake "rt-tts"; }; };
  ttsRocm = evalWith { tts = { enable = true; device = "rocm"; }; };
  storeToken = evalWith { asr = { enable = true; package = fake "rt-asr"; apiTokenFile = "${builtins.storeDir}/x-token"; }; };
  firewall = evalWith { asr = { enable = true; package = fake "rt-asr"; listenAddresses = [ "127.0.0.1" "100.64.0.10" ]; openFirewall = true; }; };
  closed = evalWith { asr = { enable = true; package = fake "rt-asr"; listenAddresses = [ "127.0.0.1" "100.64.0.10" ]; }; };

  failedAssertions = config: map (a: a.message) (builtins.filter (a: !a.assertion) config.assertions);

  failures = lib.runTests {
    testCpuHardening = {
      expr = { inherit (sc cpu "rt-asr") User MemoryDenyWriteExecute PrivateDevices ProtectSystem StateDirectory DeviceAllow; };
      expected = { User = "rt-asr"; MemoryDenyWriteExecute = true; PrivateDevices = true; ProtectSystem = "strict"; StateDirectory = "rt-asr"; DeviceAllow = [ ]; };
    };
    testQuotedEnvironment = {
      expr = builtins.filter (lib.hasInfix "NOTE") (sc cpu "rt-asr").Environment;
      expected = [ ''"NOTE=say \"hi\" 100%%"'' ];
    };
    testTokenViaCredential = {
      expr = {
        inherit (sc cpu "rt-asr") LoadCredential;
        env = builtins.filter (lib.hasInfix "RT_API_TOKEN_FILE") (sc cpu "rt-asr").Environment;
      };
      expected = {
        LoadCredential = [ "api-token:/run/secrets/rt" ];
        env = [ ''"RT_API_TOKEN_FILE=/run/credentials/rt-asr.service/api-token"'' ];
      };
    };
    testCudaDevices = {
      expr = { inherit (sc cuda "rt-asr") DeviceAllow SupplementaryGroups MemoryDenyWriteExecute PrivateDevices; };
      expected = {
        DeviceAllow = [ "/dev/nvidiactl rw" "/dev/nvidia0 rw" "/dev/nvidia-modeset rw" "/dev/nvidia-uvm rw" "/dev/nvidia-uvm-tools rw" ];
        SupplementaryGroups = [ "video" "render" ];
        MemoryDenyWriteExecute = false;
        PrivateDevices = false;
      };
    };
    testSwitchableCpuCuda = {
      expr = {
        inherit (sc switchable "rt-asr") DeviceAllow MemoryDenyWriteExecute PrivateDevices;
        package = switchable.services.rtSpeech.asr.package.name;
        env = builtins.filter (s: lib.hasInfix "RT_DEVICE" s) (sc switchable "rt-asr").Environment;
      };
      expected = {
        DeviceAllow = [ "/dev/nvidiactl rw" "/dev/nvidia0 rw" "/dev/nvidia-modeset rw" "/dev/nvidia-uvm rw" "/dev/nvidia-uvm-tools rw" ];
        MemoryDenyWriteExecute = false;
        PrivateDevices = false;
        package = (import ../packages { inherit pkgs lib; }).asr.gpu.name;
        env = [ ''"RT_DEVICE=cpu"'' ''"RT_DEVICES=cpu,cuda"'' ];
      };
    };
    testDeviceMustBeListed = {
      expr = failedAssertions deviceNotListed;
      expected = [ "services.rtSpeech.asr.device 'cuda' is not in devices [\"cpu\"]" ];
    };
    testRocmDevicesAndGfx = {
      expr = {
        inherit (sc rocm "rt-asr") DeviceAllow;
        gfx = builtins.elem ''"HSA_OVERRIDE_GFX_VERSION=9.0.0"'' (sc rocm "rt-asr").Environment;
      };
      expected = { DeviceAllow = [ "/dev/kfd rw" "char-drm rw" ]; gfx = true; };
    };
    testDeployRsPath = {
      expr = {
        inherit (sc deployed "rt-asr") ExecStart EnvironmentFile;
        cond = deployed.systemd.services.rt-asr.unitConfig.ConditionPathExists;
      };
      expected = {
        ExecStart = "/var/lib/rt-asr/current/bin/rt-asr";
        EnvironmentFile = [ "-/var/lib/rt-asr/current/env" ];
        cond = "/var/lib/rt-asr/current/bin/rt-asr";
      };
    };
    testConfigStateDefault = {
      expr = builtins.elem ''"RT_CONFIG_STATE=/var/lib/rt-tts/runtime-config.json"'' (sc ttsDefault "rt-tts").Environment;
      expected = true;
    };
    testAsrLanguages = {
      expr = builtins.elem ''"RT_ASR_LANGUAGES=pl,uk,ru,en"'' (sc cpu "rt-asr").Environment;
      expected = true;
    };
    testTtsCpuAllowsJit = { expr = (sc ttsCpuNoMdwe "rt-tts").MemoryDenyWriteExecute; expected = false; };
    testTtsRocmRejected = {
      expr = (builtins.tryEval (builtins.deepSeq (sc ttsRocm "rt-tts").ExecStart true)).success;
      expected = false;
    };
    testStoreTokenRejected = {
      expr = failedAssertions storeToken;
      expected = [ "services.rtSpeech.asr.apiTokenFile must not point into the Nix store" ];
    };
    testFirewallOnlyOnTailscale = {
      expr = {
        iface = firewall.networking.firewall.interfaces.tailscale0.allowedTCPPorts;
        global = firewall.networking.firewall.allowedTCPPorts;
      };
      expected = { iface = [ 8091 ]; global = [ ]; };
    };
    testFirewallClosedByDefault = {
      # infra R5: openFirewall = false never opens the port, whatever listenAddresses are
      expr = {
        global = closed.networking.firewall.allowedTCPPorts;
        ifaces = lib.concatMap (i: i.allowedTCPPorts or [ ]) (builtins.attrValues closed.networking.firewall.interfaces);
      };
      expected = { global = [ ]; ifaces = [ ]; };
    };
  };
in
pkgs.runCommand "rt-speech-nixos-eval-gpu" { } (
  if failures == [ ] then "touch $out"
  else ''
    echo "nixos module eval failures:"
    echo ${lib.escapeShellArg (builtins.toJSON failures)}
    exit 1
  ''
)
