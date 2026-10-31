{ pkgs, self }:
let
  lib = pkgs.lib;
  fake = service: pkgs.writers.writePython3Bin "rt-${service}" { flakeIgnore = [ "E501" ]; }
    (builtins.readFile ./fake-service.py);
  fakes = lib.genAttrs [ "asr" "tts" ] fake;
  # What the infra's mkDeployProfiles would produce: bundle + per-host env file.
  ttsDeployed = pkgs.symlinkJoin {
    name = "rt-tts-fake-deployed";
    paths = [ fakes.tts (pkgs.writeTextDir "env" "RT_TTS_VOICE=am_adam\n") ];
  };
  # 0.1 s of 16 kHz silence for the real-package decode check
  sampleWav = pkgs.runCommand "rt-sample.wav" { nativeBuildInputs = [ pkgs.python3 ]; } ''
    python3 -c "import wave; w = wave.open('$out', 'wb'); w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(bytes(3200)); w.close()"
  '';
in
pkgs.testers.runNixOSTest {
  name = "rt-speech";

  nodes.server = { config, ... }: {
    imports = [ self.nixosModules.rt-speech ];
    networking.firewall.enable = true;
    # root-only secret, like a sops-nix file (infra R3: one 0400 root file serves all system users)
    systemd.tmpfiles.rules = [ "f+ /run/secrets/rt-token 0400 root root - test-token" ];
    environment.systemPackages = [ pkgs.curl ];
    system.extraDependencies = [ ttsDeployed ];

    services.rtSpeech.asr = {
      enable = true;
      package = fakes.asr;
      # loopback for local clients + the test VLAN address for the client node
      listenAddresses = [ "127.0.0.1" config.networking.primaryIPAddress ];
      openFirewall = true;
      firewallInterfaces = [ "eth1" ];
      apiTokenFile = "/run/secrets/rt-token";
      extraEnv.RT_FAKE_NOTE = "hello world";
    };
    services.rtSpeech.tts = {
      enable = true;
      deployRsPath = "/var/lib/rt-tts/current";
      listenAddresses = [ "127.0.0.1" config.networking.primaryIPAddress ];
      openFirewall = true;
      firewallInterfaces = [ "eth9" ]; # not the test VLAN: must stay closed from the client
    };
  };

  # Real rt-asr/rt-vosk/rt-tts CPU bundles under the module's real hardening
  # (rt-asr, rt-vosk: MemoryDenyWriteExecute=yes); fake backends, so no model
  # download.
  nodes.real = { ... }: {
    imports = [ self.nixosModules.rt-speech ];
    virtualisation.memorySize = 2560;
    environment.systemPackages = [ pkgs.curl ];
    services.rtSpeech.asr = {
      enable = true;
      package = self.packages.x86_64-linux.rt-asr-cpu;
      extraEnv.RT_ASR_BACKEND = "fake";
    };
    services.rtSpeech.tts = {
      enable = true;
      package = self.packages.x86_64-linux.rt-tts-cpu;
      extraEnv.RT_TTS_BACKEND = "fake";
    };
    services.rtSpeech.vosk = {
      enable = true; # default package: the cpu bundle
      extraEnv.RT_VOSK_BACKEND = "fake";
    };
  };

  nodes.client = { pkgs, ... }: {
    environment.systemPackages = [ pkgs.curl ];
  };

  testScript = ''
    start_all()
    server.wait_for_unit("multi-user.target")
    server.wait_for_unit("rt-asr.service")
    server.wait_for_open_port(8091)

    with subtest("static user and hardening"):
        server.succeed("systemctl show rt-asr -p User --value | grep -qx rt-asr")
        server.succeed("systemctl show rt-asr -p ProtectSystem --value | grep -qx strict")
        server.succeed("systemctl show rt-asr -p MemoryDenyWriteExecute --value | grep -qx yes")
        server.succeed("test \"$(stat -c %U /var/lib/rt-asr)\" = rt-asr")
        server.succeed("test -d /var/lib/rt-asr/models")

    with subtest("environment from the module, value with a space survives"):
        status = server.succeed("curl -sf http://127.0.0.1:8091/status")
        assert '"RT_PORT": "8091"' in status, status
        assert '"RT_FAKE_NOTE": "hello world"' in status, status
        assert '"HF_HOME": "/var/lib/rt-asr/models/hf"' in status, status
        assert '"RT_CONFIG_STATE": "/var/lib/rt-asr/runtime-config.json"' in status, status
        assert '"RT_API_TOKEN_FILE": "/run/credentials/rt-asr.service/api-token"' in status, status

    with subtest("api token through LoadCredential"):
        server.fail("curl -sf http://127.0.0.1:8091/v1/ping")
        server.succeed("curl -sf -H 'Authorization: Bearer test-token' http://127.0.0.1:8091/v1/ping")
        server.succeed("curl -sf http://127.0.0.1:8091/health")
        server.succeed("test \"$(stat -c '%a %U' /run/secrets/rt-token)\" = '400 root'")

    with subtest("deploy-rs flow (tts)"):
        server.succeed("systemctl show rt-tts -p ConditionResult --value | grep -qx no")
        server.succeed("systemctl show rt-tts -p NRestarts --value | grep -qx 0")
        server.succeed("ln -sfn ${ttsDeployed} /var/lib/rt-tts/current")
        server.succeed("systemctl restart rt-tts")
        server.wait_for_open_port(8093)
        status = server.succeed("curl -sf http://127.0.0.1:8093/status")
        assert '"default_voice": "am_adam"' in status, status
        assert '"RT_TTS_VOICE": "am_adam"' in status, status

    with subtest("runtime config persists across restart"):
        server.succeed("curl -sf -X POST -d '{\"voice\": \"bf_emma\"}' http://127.0.0.1:8093/config")
        before = server.succeed("curl -sf http://127.0.0.1:8093/config")
        assert '"voice": "bf_emma"' in before, before
        server.succeed("systemctl restart rt-tts")
        server.wait_for_open_port(8093)
        after = server.succeed("curl -sf http://127.0.0.1:8093/config")
        assert after == before, (before, after)
        server.succeed("test \"$(stat -c %U /var/lib/rt-tts/runtime-config.json)\" = rt-tts")

    with subtest("real cpu bundles decode/encode audio under MemoryDenyWriteExecute"):
        real.wait_for_unit("rt-asr.service")
        real.wait_for_open_port(8091)
        real.succeed("systemctl show rt-asr -p MemoryDenyWriteExecute --value | grep -qx yes")
        out = real.succeed("curl -sf -F file=@${sampleWav} -F response_format=json http://127.0.0.1:8091/v1/audio/transcriptions")
        assert '"text"' in out, out
        real.wait_for_unit("rt-tts.service")
        real.wait_for_open_port(8093)
        real.succeed("curl -sf -o /tmp/s.wav -H 'Content-Type: application/json' -d '{\"input\": \"Hello.\"}' http://127.0.0.1:8093/v1/audio/speech")
        real.succeed("test $(stat -c %s /tmp/s.wav) -gt 44")
        real.wait_for_unit("rt-vosk.service")
        real.wait_for_open_port(8092)
        real.succeed("systemctl show rt-vosk -p MemoryDenyWriteExecute --value | grep -qx yes")
        out = real.succeed("curl -sf -F file=@${sampleWav} -F language=uk http://127.0.0.1:8092/v1/audio/transcriptions")
        assert '"words"' in out and '"language":"uk"' in out, out
        status = real.succeed("curl -sf http://127.0.0.1:8092/status")
        assert '"service":"rt-vosk"' in status, status

    with subtest("firewall opens only the configured interface"):
        client.wait_for_unit("multi-user.target")
        client.succeed("curl -sf --max-time 5 http://server:8091/health")
        client.fail("curl -sf --max-time 5 http://server:8093/health")
  '';
}
