dmaslo@cyborg ~> cd ./real-time-transletor/
dmaslo@cyborg ~/real-time-transletor (main)> ls -laF
загалом 140
drwxr-xr-x 10 dmaslo users  4096 gru 21 20:18  ./
drwx------ 55 dmaslo users  4096 gru 21 19:57  ../
-rw-r--r--  1 dmaslo users  2523 kwi 21  2025  checklist.md
drwxr-xr-x  2 dmaslo users  4096 mar 31  2025  config/
-rw-r--r--  1 dmaslo users  4828 mar 31  2025  CONTRIBUTING.md
drwxr-xr-x  2 dmaslo users  4096 gru 14 17:50  docs/
-rw-r--r--  1 dmaslo users  1494 gru 21 20:14  flake.lock
-rw-r--r--  1 dmaslo users 10650 gru 21 20:16  flake.nix
-rw-r--r--  1 dmaslo users  4171 gru 14 11:07  flake.nix.old
drwxr-xr-x  9 dmaslo users  4096 gru 21 20:14  .git/
-rw-r--r--  1 dmaslo users   486 mar 31  2025  .gitignore
-rw-r--r--  1 dmaslo users  4002 gru 14 18:43  install_pipewire_config.py
drwxr-xr-x  3 dmaslo users  4096 gru 14 16:43  .pytest_cache/
-rw-r--r--  1 dmaslo users  8591 gru 14 18:43  README.md
-rw-r--r--  1 dmaslo users  3395 gru 14 10:10  README_NIX.md
-rw-r--r--  1 dmaslo users   480 gru 14 09:48  requirements.txt
lrwxrwxrwx  1 dmaslo users    81 gru 14 19:49  result -> /nix/store/w97197hbwxaqrgiin9gsq15y3c4dx039-python3.12-real-time-translator-0.1.0/
-rw-r--r--  1 dmaslo users  3103 mar 31  2025  .roomodes
-rw-r--r--  1 dmaslo users  7420 gru 21 20:40  RUNNING_AND_TESTING.md
drwxr-xr-x  4 dmaslo users  4096 gru 14 09:44  scripts/
drwxr-xr-x 13 dmaslo users  4096 gru 14 17:39  src/
-rw-r--r--  1 dmaslo users  3823 gru 14 09:58  test_pipeline.py
drwxr-xr-x  4 dmaslo users  4096 gru 21 20:57  tests/
-rw-r--r--  1 dmaslo users  9027 gru 21 20:40  TRANSFORMATION_SUMMARY.md
drwxr-xr-x  2 dmaslo users  4096 gru  7 14:14  whisper_real_time_translation-main/
-rw-r--r--  1 dmaslo users   229 gru 14 21:08 'додати тести.md'
dmaslo@cyborg ~/real-time-transletor (main)> nix develop
Real-time Translator development environment ready!
Use 'python3 -m src.main' to start the application

Note: Make sure your PipeWire virtual sinks are set up.
Run this once to set up virtual sinks if not already done:
  python install_pipewire_config.py
  # Or manually: systemctl --user restart pipewire pipewire-pulse

[dmaslo@cyborg:~/real-time-transletor]$ python install_pipewire_config.py
Error: Source service file systemd/rt-virtual-sinks.service not found.
Make sure you're running this script from the project root directory.

[dmaslo@cyborg:~/real-time-transletor]$ systemctl --user start rt-virtual-sinks
systemctl --user start rt-capture rt-whisper rt-translate rt-tts rt-playback
Failed to start rt-virtual-sinks.service: Unit rt-virtual-sinks.service not found.
Failed to start rt-capture.service: Unit rt-capture.service not found.
Failed to start rt-whisper.service: Unit rt-whisper.service not found.
Failed to start rt-translate.service: Unit rt-translate.service not found.
Failed to start rt-tts.service: Unit rt-tts.service not found.
Failed to start rt-playback.service: Unit rt-playback.service not found.

[dmaslo@cyborg:~/real-time-transletor]$ nix run
error: Cannot build '/nix/store/wf7sxjrpmlr4vby15rkhgv7d0qyzz52p-python3.12-real-time-translator-0.1.0.drv'.
       Reason: builder failed with exit code 1.
       Output paths:
         /nix/store/3wbxspi6c55jngf1dlg2vjjnlcmfkfpq-python3.12-real-time-translator-0.1.0
       Last 25 log lines:
       > Sourcing python-catch-conflicts-hook.sh
       > Sourcing python-remove-bin-bytecode-hook.sh
       > Sourcing python-imports-check-hook.sh
       > Using pythonImportsCheckPhase
       > Sourcing python-namespaces-hook
       > Running phase: unpackPhase
       > unpacking source archive /nix/store/sird5x32gci3a5k8pwpx236zcdwkv1zc-zikxmbmmrkmw9yc89a6pkbvhan471nhx-source
       > source root is zikxmbmmrkmw9yc89a6pkbvhan471nhx-source
       > setting SOURCE_DATE_EPOCH to timestamp 315619200 of file "zikxmbmmrkmw9yc89a6pkbvhan471nhx-source/додати
       > Running phase: patchPhase
       > Running phase: updateAutotoolsGnuConfigScriptsPhase
       > Running phase: configurePhase
       > no configure script, doing nothing
       > Running phase: buildPhase
       > Running phase: installPhase
       > Running phase: fixupPhase
       > shrinking RPATHs of ELF executables and libraries in /nix/store/3wbxspi6c55jngf1dlg2vjjnlcmfkfpq-python3.12-real-time-translator-0.1.0
       > checking for references to /build/ in /nix/store/3wbxspi6c55jngf1dlg2vjjnlcmfkfpq-python3.12-real-time-translator-0.1.0...
       > patching script interpreter paths in /nix/store/3wbxspi6c55jngf1dlg2vjjnlcmfkfpq-python3.12-real-time-translator-0.1.0
       > stripping (with command strip and flags -S -p) in  /nix/store/3wbxspi6c55jngf1dlg2vjjnlcmfkfpq-python3.12-real-time-translator-0.1.0/bin
       > Rewriting #! /nix/store/rlq03x4cwf8zn73hxaxnx0zn5q9kifls-bash-5.3p3/bin/bash -e to #!/nix/store/fdibxyh7xcmqrc172y78awzhxs292gq1-python3-3.12.12
       > Executing pythonRemoveTestsDir
       > Finished executing pythonRemoveTestsDir
       > ERROR: noBrokenSymlinks: the symlink /nix/store/3wbxspi6c55jngf1dlg2vjjnlcmfkfpq-python3.12-real-time-translator-0.1.0/share/real-time-translator/result points to a missing target: /nix/store/w97197hbwxaqrgiin9gsq15y3c4dx039-python3.12-real-time-translator-0.1.0
       > ERROR: noBrokenSymlinks: found 1 dangling symlinks, 0 reflexive symlinks and 0 unreadable symlinks
       For full logs, run:
         nix log /nix/store/wf7sxjrpmlr4vby15rkhgv7d0qyzz52p-python3.12-real-time-translator-0.1.0.drv

[dmaslo@cyborg:~/real-time-transletor]$ nix develop --command systemctl --user enable rt-virtual-sinks
Real-time Translator development environment ready!
Use 'python3 -m src.main' to start the application

Note: Make sure your PipeWire virtual sinks are set up.
Run this once to set up virtual sinks if not already done:
  python install_pipewire_config.py
  # Or manually: systemctl --user restart pipewire pipewire-pulse
Failed to enable unit: Unit rt-virtual-sinks.service does not exist

[dmaslo@cyborg:~/real-time-transletor]$ exit
exit
dmaslo@cyborg ~/real-time-transletor (main) [1]> nix develop --command systemctl --user enable rt-virtual-sinks
Real-time Translator development environment ready!
Use 'python3 -m src.main' to start the application

Note: Make sure your PipeWire virtual sinks are set up.
Run this once to set up virtual sinks if not already done:
  python install_pipewire_config.py
  # Or manually: systemctl --user restart pipewire pipewire-pulse
Failed to enable unit: Unit rt-virtual-sinks.service does not exist
dmaslo@cyborg ~/real-time-transletor (main) [1]> nix develop --command systemctl --user enable rt-capture.socket rt-whisper.socket rt-translate.socket 
Real-time Translator development environment ready!
Use 'python3 -m src.main' to start the application

Note: Make sure your PipeWire virtual sinks are set up.
Run this once to set up virtual sinks if not already done:
  python install_pipewire_config.py
  # Or manually: systemctl --user restart pipewire pipewire-pulse
Failed to enable unit: Unit rt-capture.socket does not exist
dmaslo@cyborg ~/real-time-transletor (main) [1]> nix develop --command systemctl --user enable rt-capture.socket rt-whisper.socket rt-translate.socket rt-tts.socket rt-playback.socket
Real-time Translator development environment ready!
Use 'python3 -m src.main' to start the application

Note: Make sure your PipeWire virtual sinks are set up.
Run this once to set up virtual sinks if not already done:
  python install_pipewire_config.py
  # Or manually: systemctl --user restart pipewire pipewire-pulse
Failed to enable unit: Unit rt-capture.socket does not exist
dmaslo@cyborg ~/real-time-transletor (main) [1]> nix develop --command systemctl --user start rt-virtual-sinks
Real-time Translator development environment ready!
Use 'python3 -m src.main' to start the application

Note: Make sure your PipeWire virtual sinks are set up.
Run this once to set up virtual sinks if not already done:
  python install_pipewire_config.py
  # Or manually: systemctl --user restart pipewire pipewire-pulse
Failed to start rt-virtual-sinks.service: Unit rt-virtual-sinks.service not found.
dmaslo@cyborg ~/real-time-transletor (main) [5]> nix run
error: Cannot build '/nix/store/wf7sxjrpmlr4vby15rkhgv7d0qyzz52p-python3.12-real-time-translator-0.1.0.drv'.
       Reason: builder failed with exit code 1.
       Output paths:
         /nix/store/3wbxspi6c55jngf1dlg2vjjnlcmfkfpq-python3.12-real-time-translator-0.1.0
       Last 25 log lines:
       > Sourcing python-catch-conflicts-hook.sh
       > Sourcing python-remove-bin-bytecode-hook.sh
       > Sourcing python-imports-check-hook.sh
       > Using pythonImportsCheckPhase
       > Sourcing python-namespaces-hook
       > Running phase: unpackPhase
       > unpacking source archive /nix/store/sird5x32gci3a5k8pwpx236zcdwkv1zc-zikxmbmmrkmw9yc89a6pkbvhan471nhx-source
       > source root is zikxmbmmrkmw9yc89a6pkbvhan471nhx-source
       > setting SOURCE_DATE_EPOCH to timestamp 315619200 of file "zikxmbmmrkmw9yc89a6pkbvhan471nhx-source/додати
       > Running phase: patchPhase
       > Running phase: updateAutotoolsGnuConfigScriptsPhase
       > Running phase: configurePhase
       > no configure script, doing nothing
       > Running phase: buildPhase
       > Running phase: installPhase
       > Running phase: fixupPhase
       > shrinking RPATHs of ELF executables and libraries in /nix/store/3wbxspi6c55jngf1dlg2vjjnlcmfkfpq-python3.12-real-time-translator-0.1.0
       > checking for references to /build/ in /nix/store/3wbxspi6c55jngf1dlg2vjjnlcmfkfpq-python3.12-real-time-translator-0.1.0...
       > patching script interpreter paths in /nix/store/3wbxspi6c55jngf1dlg2vjjnlcmfkfpq-python3.12-real-time-translator-0.1.0
       > stripping (with command strip and flags -S -p) in  /nix/store/3wbxspi6c55jngf1dlg2vjjnlcmfkfpq-python3.12-real-time-translator-0.1.0/bin
       > Rewriting #! /nix/store/rlq03x4cwf8zn73hxaxnx0zn5q9kifls-bash-5.3p3/bin/bash -e to #!/nix/store/fdibxyh7xcmqrc172y78awzhxs292gq1-python3-3.12.12
       > Executing pythonRemoveTestsDir
       > Finished executing pythonRemoveTestsDir
       > ERROR: noBrokenSymlinks: the symlink /nix/store/3wbxspi6c55jngf1dlg2vjjnlcmfkfpq-python3.12-real-time-translator-0.1.0/share/real-time-translator/result points to a missing target: /nix/store/w97197hbwxaqrgiin9gsq15y3c4dx039-python3.12-real-time-translator-0.1.0
       > ERROR: noBrokenSymlinks: found 1 dangling symlinks, 0 reflexive symlinks and 0 unreadable symlinks
       For full logs, run:
         nix log /nix/store/wf7sxjrpmlr4vby15rkhgv7d0qyzz52p-python3.12-real-time-translator-0.1.0.drv
dmaslo@cyborg ~/real-time-transletor (main) [1]> 
