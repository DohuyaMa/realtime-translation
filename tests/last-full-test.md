Running direct adapter tests...
Real-time Translator development environment ready!
Use 'python3 -m src.main' to start the application

PipeWire virtual sinks have been created:
  - rt_virtual_input (RT-Virtual-Input)
  - rt_virtual_output (RT-Virtual-Output)
These are available for audio routing in the development environment.
============================= test session starts ==============================
platform linux -- Python 3.12.12, pytest-8.4.2, pluggy-1.6.0 -- /nix/store/n34ywginm00ql3l97zxcr7zk3kq6z9v3-python3-3.12.12/bin/python
cachedir: .pytest_cache
hypothesis profile 'ci' -> database=None, deadline=None, print_blob=True, derandomize=True, suppress_health_check=(HealthCheck.too_slow,)
rootdir: /home/dmaslo/real-time-transletor
plugins: hypothesis-6.136.9, typeguard-4.4.4
collecting ... collected 11 items

tests/test_direct_adapter.py::test_direct_adapter_creation PASSED        [  9%]
tests/test_direct_adapter.py::test_direct_adapter_get_audio_devices PASSED [ 18%]
tests/test_direct_adapter.py::test_direct_adapter_device_selection_in_dev_mode PASSED [ 27%]
tests/test_direct_adapter.py::test_direct_adapter_language_setting_in_dev_mode PASSED [ 36%]
tests/test_direct_adapter.py::test_direct_adapter_service_control_in_dev_mode PASSED [ 45%]
tests/test_direct_adapter.py::test_direct_adapter_pipeline_control PASSED [ 54%]
tests/test_direct_adapter.py::test_direct_adapter_status PASSED          [ 63%]
tests/test_direct_adapter.py::test_direct_adapter_audio_levels PASSED    [ 72%]
tests/test_direct_adapter.py::test_direct_adapter_translation_toggle PASSED [ 81%]
tests/test_direct_adapter.py::test_direct_adapter_cleanup PASSED         [ 90%]
tests/test_direct_adapter.py::test_direct_adapter_virtual_device_functionality PASSED [100%]

============================== 11 passed in 1.64s ==============================
Running IPC communication tests...
Real-time Translator development environment ready!
Use 'python3 -m src.main' to start the application

PipeWire virtual sinks have been created:
  - rt_virtual_input (RT-Virtual-Input)
  - rt_virtual_output (RT-Virtual-Output)
These are available for audio routing in the development environment.
============================= test session starts ==============================
platform linux -- Python 3.12.12, pytest-8.4.2, pluggy-1.6.0 -- /nix/store/n34ywginm00ql3l97zxcr7zk3kq6z9v3-python3-3.12.12/bin/python
cachedir: .pytest_cache
hypothesis profile 'ci' -> database=None, deadline=None, print_blob=True, derandomize=True, suppress_health_check=(HealthCheck.too_slow,)
rootdir: /home/dmaslo/real-time-transletor
plugins: hypothesis-6.136.9, typeguard-4.4.4
collecting ... collected 13 items

tests/test_ipc_communication.py::TestIPCCommunication::test_ipc_server_initialization PASSED [  7%]
tests/test_ipc_communication.py::TestIPCCommunication::test_ipc_client_initialization PASSED [ 15%]
tests/test_ipc_communication.py::TestIPCCommunication::test_ipc_server_start_stop PASSED [ 23%]
tests/test_ipc_communication.py::TestIPCCommunication::test_ipc_server_handler_registration PASSED [ 30%]
tests/test_ipc_communication.py::TestIPCCommunication::test_ipc_basic_message_flow PASSED [ 38%]
tests/test_ipc_communication.py::TestIPCCommunication::test_ipc_server_multiple_clients PASSED [ 46%]
tests/test_ipc_communication.py::TestIPCCommunication::test_ipc_client_disconnect_reconnect PASSED [ 53%]
tests/test_ipc_communication.py::TestIPCCommunication::test_ipc_message_format PASSED [ 61%]
tests/test_ipc_communication.py::TestIPCCommunication::test_ipc_server_unknown_message_type IPC tests completed or timed out
Running TTS engine tests...
Real-time Translator development environment ready!
Use 'python3 -m src.main' to start the application

PipeWire virtual sinks have been created:
  - rt_virtual_input (RT-Virtual-Input)
  - rt_virtual_output (RT-Virtual-Output)
These are available for audio routing in the development environment.
============================= test session starts ==============================
platform linux -- Python 3.12.12, pytest-8.4.2, pluggy-1.6.0 -- /nix/store/n34ywginm00ql3l97zxcr7zk3kq6z9v3-python3-3.12.12/bin/python
cachedir: .pytest_cache
hypothesis profile 'ci' -> database=None, deadline=None, print_blob=True, derandomize=True, suppress_health_check=(HealthCheck.too_slow,)
rootdir: /home/dmaslo/real-time-transletor
plugins: hypothesis-6.136.9, typeguard-4.4.4
collecting ... collected 7 items

tests/test_tts_engine.py::TestTTSEngine::test_synthesize_method PASSED   [ 14%]
tests/test_tts_engine.py::TestTTSEngine::test_synthesize_queue_full PASSED [ 28%]
tests/test_tts_engine.py::TestTTSEngine::test_tts_engine_destructor_no_error PASSED [ 42%]
tests/test_tts_engine.py::TestTTSEngine::test_tts_engine_destructor_with_synthesis_thread PASSED [ 57%]
tests/test_tts_engine.py::TestTTSEngine::test_tts_engine_initialization PASSED [ 71%]
tests/test_tts_engine.py::TestTTSEngine::test_tts_engine_stop_method PASSED [ 85%]
tests/test_tts_engine.py::TestTTSEngine::test_tts_engine_stop_when_not_running PASSED [100%]

=============================== warnings summary ===============================
../../../nix/store/qr9w9xklgb5ddaqwc4fbqm0lkm8nzk2f-python3.12-transformers-4.57.1/lib/python3.12/site-packages/transformers/utils/hub.py:110
  /nix/store/qr9w9xklgb5ddaqwc4fbqm0lkm8nzk2f-python3.12-transformers-4.57.1/lib/python3.12/site-packages/transformers/utils/hub.py:110: FutureWarning: Using `TRANSFORMERS_CACHE` is deprecated and will be removed in v5 of Transformers. Use `HF_HOME` instead.
    warnings.warn(

../../../nix/store/mcshwhiak3cmj60ajpzx5i4889hjjlrw-python3.12-spacy-3.8.7/lib/python3.12/site-packages/spacy/cli/_util.py:23
  /nix/store/mcshwhiak3cmj60ajpzx5i4889hjjlrw-python3.12-spacy-3.8.7/lib/python3.12/site-packages/spacy/cli/_util.py:23: DeprecationWarning: Importing 'parser.split_arg_string' is deprecated, it will only be available in 'shell_completion' in Click 9.0.
    from click.parser import split_arg_string

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
======================== 7 passed, 2 warnings in 8.43s =========================
Running AI models tests (these are expected to have failures due to SpaCy issue)...
Real-time Translator development environment ready!
Use 'python3 -m src.main' to start the application

PipeWire virtual sinks have been created:
  - rt_virtual_input (RT-Virtual-Input)
  - rt_virtual_output (RT-Virtual-Output)
These are available for audio routing in the development environment.
============================= test session starts ==============================
platform linux -- Python 3.12.12, pytest-8.4.2, pluggy-1.6.0 -- /nix/store/n34ywginm00ql3l97zxcr7zk3kq6z9v3-python3-3.12.12/bin/python
cachedir: .pytest_cache
hypothesis profile 'ci' -> database=None, deadline=None, print_blob=True, derandomize=True, suppress_health_check=(HealthCheck.too_slow,)
rootdir: /home/dmaslo/real-time-transletor
plugins: hypothesis-6.136.9, typeguard-4.4.4
collecting ... collected 11 items

tests/test_ai_models.py::test_whisper_ukrainian_recognition FAILED       [  9%]
tests/test_ai_models.py::test_whisper_polish_recognition FAILED          [ 18%]
tests/test_ai_models.py::test_whisper_auto_language_detection FAILED     [ 27%]
tests/test_ai_models.py::test_tts_synthesis ERROR                        [ 36%]
tests/test_ai_models.py::test_tts_duration[Hello, how are you?-2.0] ERROR [ 45%]
tests/test_ai_models.py::test_tts_duration[This is a longer text that should take more time.-4.0] ERROR [ 54%]
tests/test_ai_models.py::test_translation_pipeline ERROR                 [ 63%]
tests/test_ai_models.py::test_model_error_handling ERROR                 [ 72%]
tests/test_ai_models.py::test_gpu_support SKIPPED (GPU not available)    [ 81%]
tests/test_ai_models.py::test_performance_metrics FAILED                 [ 90%]
tests/test_ai_models.py::test_language_confidence PASSED                 [100%]

==================================== ERRORS ====================================
_____________________ ERROR at setup of test_tts_synthesis _____________________

    @pytest.fixture
    def tts_engine():
        """Create TTS engine instance."""
>       return TTSEngine(
            use_gpu=False  # Use CPU for testing
        )

tests/test_ai_models.py:22: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 
src/models/tts_engine.py:60: in __init__
    self.tts = TextToSpeech(
src/models/tts_engine.py:15: in __init__
    self.pipeline = KPipeline(lang_code=lang_code, device=device)
                    ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
/nix/store/a0c5008yydajwk21p00npcq13z73zfiw-python3.12-kokoro-0-unstable-2025-06-16/lib/python3.12/site-packages/kokoro/pipeline.py:123: in __init__
    self.g2p = en.G2P(trf=trf, british=lang_code=='b', fallback=fallback, unk='')
               ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
/nix/store/g8sm5vq513ywjdd3g6fm4mms7ff8mwzj-python3.12-misaki-0-unstable-2025-06-16/lib/python3.12/site-packages/misaki/en.py:527: in __init__
    spacy.cli.download(name)
/nix/store/mcshwhiak3cmj60ajpzx5i4889hjjlrw-python3.12-spacy-3.8.7/lib/python3.12/site-packages/spacy/cli/download.py:90: in download
    download_model(filename, pip_args)
/nix/store/mcshwhiak3cmj60ajpzx5i4889hjjlrw-python3.12-spacy-3.8.7/lib/python3.12/site-packages/spacy/cli/download.py:176: in download_model
    run_command(cmd)
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 

command = ['/nix/store/n34ywginm00ql3l97zxcr7zk3kq6z9v3-python3-3.12.12/bin/python', '-m', 'pip', 'install', 'https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl']

    def run_command(
        command: Union[str, List[str]],
        *,
        stdin: Optional[Any] = None,
        capture: bool = False,
    ) -> subprocess.CompletedProcess:
        """Run a command on the command line as a subprocess. If the subprocess
        returns a non-zero exit code, a system exit is performed.
        command (str / List[str]): The command. If provided as a string, the
            string will be split using shlex.split.
        stdin (Optional[Any]): stdin to read from or None.
        capture (bool): Whether to capture the output and errors. If False,
            the stdout and stderr will not be redirected, and if there's an error,
            sys.exit will be called with the return code. You should use capture=False
            when you want to turn over execution to the command, and capture=True
            when you want to run the command more like a function.
        RETURNS (Optional[CompletedProcess]): The process object.
        """
        if isinstance(command, str):
            cmd_list = split_command(command)
            cmd_str = command
        else:
            cmd_list = command
            cmd_str = " ".join(command)
        try:
            ret = subprocess.run(
                cmd_list,
                env=os.environ.copy(),
                input=stdin,
                encoding="utf8",
                check=False,
                stdout=subprocess.PIPE if capture else None,
                stderr=subprocess.STDOUT if capture else None,
            )
        except FileNotFoundError:
            # Indicates the *command* wasn't found, it's an error before the command
            # is run.
            raise FileNotFoundError(
                Errors.E970.format(str_command=cmd_str, tool=cmd_list[0])
            ) from None
        if ret.returncode != 0 and capture:
            message = f"Error running command:\n\n{cmd_str}\n\n"
            message += f"Subprocess exited with status {ret.returncode}"
            if ret.stdout is not None:
                message += f"\n\nProcess log (stdout and stderr):\n\n"
                message += ret.stdout
            error = subprocess.SubprocessError(message)
            error.ret = ret  # type: ignore[attr-defined]
            error.command = cmd_str  # type: ignore[attr-defined]
            raise error
        elif ret.returncode != 0:
>           sys.exit(ret.returncode)
E           SystemExit: 1

/nix/store/mcshwhiak3cmj60ajpzx5i4889hjjlrw-python3.12-spacy-3.8.7/lib/python3.12/site-packages/spacy/util.py:1046: SystemExit
---------------------------- Captured stdout setup -----------------------------
WARNING: Defaulting repo_id to hexgrad/Kokoro-82M. Pass repo_id='hexgrad/Kokoro-82M' to suppress this warning.
---------------------------- Captured stderr setup -----------------------------
2025-12-23 16:00:37,523 - urllib3.connectionpool - DEBUG - Starting new HTTPS connection (1): huggingface.co:443
2025-12-23 16:00:37,844 - urllib3.connectionpool - DEBUG - https://huggingface.co:443 "HEAD /hexgrad/Kokoro-82M/resolve/main/config.json HTTP/1.1" 307 0
2025-12-23 16:00:37,886 - urllib3.connectionpool - DEBUG - https://huggingface.co:443 "HEAD /api/resolve-cache/models/hexgrad/Kokoro-82M/f3ff3571791e39611d31c381e3a41a3af07b4987/config.json HTTP/1.1" 200 0
2025-12-23 16:00:38,682 - urllib3.connectionpool - DEBUG - https://huggingface.co:443 "HEAD /hexgrad/Kokoro-82M/resolve/main/kokoro-v1_0.pth HTTP/1.1" 302 0
2025-12-23 16:00:38,925 - urllib3.connectionpool - DEBUG - Starting new HTTPS connection (1): raw.githubusercontent.com:443
2025-12-23 16:00:39,339 - urllib3.connectionpool - DEBUG - https://raw.githubusercontent.com:443 "GET /explosion/spacy-models/master/compatibility.json HTTP/1.1" 200 4351
/nix/store/n34ywginm00ql3l97zxcr7zk3kq6z9v3-python3-3.12.12/bin/python: No module named pip
------------------------------ Captured log setup ------------------------------
DEBUG    urllib3.connectionpool:connectionpool.py:1049 Starting new HTTPS connection (1): huggingface.co:443
DEBUG    urllib3.connectionpool:connectionpool.py:544 https://huggingface.co:443 "HEAD /hexgrad/Kokoro-82M/resolve/main/config.json HTTP/1.1" 307 0
DEBUG    urllib3.connectionpool:connectionpool.py:544 https://huggingface.co:443 "HEAD /api/resolve-cache/models/hexgrad/Kokoro-82M/f3ff3571791e39611d31c381e3a41a3af07b4987/config.json HTTP/1.1" 200 0
DEBUG    urllib3.connectionpool:connectionpool.py:544 https://huggingface.co:443 "HEAD /hexgrad/Kokoro-82M/resolve/main/kokoro-v1_0.pth HTTP/1.1" 302 0
DEBUG    urllib3.connectionpool:connectionpool.py:1049 Starting new HTTPS connection (1): raw.githubusercontent.com:443
DEBUG    urllib3.connectionpool:connectionpool.py:544 https://raw.githubusercontent.com:443 "GET /explosion/spacy-models/master/compatibility.json HTTP/1.1" 200 4351
_________ ERROR at setup of test_tts_duration[Hello, how are you?-2.0] _________

    @pytest.fixture
    def tts_engine():
        """Create TTS engine instance."""
>       return TTSEngine(
            use_gpu=False  # Use CPU for testing
        )

tests/test_ai_models.py:22: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 
src/models/tts_engine.py:60: in __init__
    self.tts = TextToSpeech(
src/models/tts_engine.py:15: in __init__
    self.pipeline = KPipeline(lang_code=lang_code, device=device)
                    ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
/nix/store/a0c5008yydajwk21p00npcq13z73zfiw-python3.12-kokoro-0-unstable-2025-06-16/lib/python3.12/site-packages/kokoro/pipeline.py:123: in __init__
    self.g2p = en.G2P(trf=trf, british=lang_code=='b', fallback=fallback, unk='')
               ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
/nix/store/g8sm5vq513ywjdd3g6fm4mms7ff8mwzj-python3.12-misaki-0-unstable-2025-06-16/lib/python3.12/site-packages/misaki/en.py:527: in __init__
    spacy.cli.download(name)
/nix/store/mcshwhiak3cmj60ajpzx5i4889hjjlrw-python3.12-spacy-3.8.7/lib/python3.12/site-packages/spacy/cli/download.py:90: in download
    download_model(filename, pip_args)
/nix/store/mcshwhiak3cmj60ajpzx5i4889hjjlrw-python3.12-spacy-3.8.7/lib/python3.12/site-packages/spacy/cli/download.py:176: in download_model
    run_command(cmd)
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 

command = ['/nix/store/n34ywginm00ql3l97zxcr7zk3kq6z9v3-python3-3.12.12/bin/python', '-m', 'pip', 'install', 'https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl']

    def run_command(
        command: Union[str, List[str]],
        *,
        stdin: Optional[Any] = None,
        capture: bool = False,
    ) -> subprocess.CompletedProcess:
        """Run a command on the command line as a subprocess. If the subprocess
        returns a non-zero exit code, a system exit is performed.
        command (str / List[str]): The command. If provided as a string, the
            string will be split using shlex.split.
        stdin (Optional[Any]): stdin to read from or None.
        capture (bool): Whether to capture the output and errors. If False,
            the stdout and stderr will not be redirected, and if there's an error,
            sys.exit will be called with the return code. You should use capture=False
            when you want to turn over execution to the command, and capture=True
            when you want to run the command more like a function.
        RETURNS (Optional[CompletedProcess]): The process object.
        """
        if isinstance(command, str):
            cmd_list = split_command(command)
            cmd_str = command
        else:
            cmd_list = command
            cmd_str = " ".join(command)
        try:
            ret = subprocess.run(
                cmd_list,
                env=os.environ.copy(),
                input=stdin,
                encoding="utf8",
                check=False,
                stdout=subprocess.PIPE if capture else None,
                stderr=subprocess.STDOUT if capture else None,
            )
        except FileNotFoundError:
            # Indicates the *command* wasn't found, it's an error before the command
            # is run.
            raise FileNotFoundError(
                Errors.E970.format(str_command=cmd_str, tool=cmd_list[0])
            ) from None
        if ret.returncode != 0 and capture:
            message = f"Error running command:\n\n{cmd_str}\n\n"
            message += f"Subprocess exited with status {ret.returncode}"
            if ret.stdout is not None:
                message += f"\n\nProcess log (stdout and stderr):\n\n"
                message += ret.stdout
            error = subprocess.SubprocessError(message)
            error.ret = ret  # type: ignore[attr-defined]
            error.command = cmd_str  # type: ignore[attr-defined]
            raise error
        elif ret.returncode != 0:
>           sys.exit(ret.returncode)
E           SystemExit: 1

/nix/store/mcshwhiak3cmj60ajpzx5i4889hjjlrw-python3.12-spacy-3.8.7/lib/python3.12/site-packages/spacy/util.py:1046: SystemExit
---------------------------- Captured stdout setup -----------------------------
WARNING: Defaulting repo_id to hexgrad/Kokoro-82M. Pass repo_id='hexgrad/Kokoro-82M' to suppress this warning.
---------------------------- Captured stderr setup -----------------------------
2025-12-23 16:00:39,593 - urllib3.connectionpool - DEBUG - https://huggingface.co:443 "HEAD /hexgrad/Kokoro-82M/resolve/main/config.json HTTP/1.1" 307 0
2025-12-23 16:00:39,627 - urllib3.connectionpool - DEBUG - https://huggingface.co:443 "HEAD /api/resolve-cache/models/hexgrad/Kokoro-82M/f3ff3571791e39611d31c381e3a41a3af07b4987/config.json HTTP/1.1" 200 0
2025-12-23 16:00:40,355 - urllib3.connectionpool - DEBUG - https://huggingface.co:443 "HEAD /hexgrad/Kokoro-82M/resolve/main/kokoro-v1_0.pth HTTP/1.1" 302 0
2025-12-23 16:00:40,575 - urllib3.connectionpool - DEBUG - Starting new HTTPS connection (1): raw.githubusercontent.com:443
2025-12-23 16:00:40,753 - urllib3.connectionpool - DEBUG - https://raw.githubusercontent.com:443 "GET /explosion/spacy-models/master/compatibility.json HTTP/1.1" 200 4351
/nix/store/n34ywginm00ql3l97zxcr7zk3kq6z9v3-python3-3.12.12/bin/python: No module named pip
------------------------------ Captured log setup ------------------------------
DEBUG    urllib3.connectionpool:connectionpool.py:544 https://huggingface.co:443 "HEAD /hexgrad/Kokoro-82M/resolve/main/config.json HTTP/1.1" 307 0
DEBUG    urllib3.connectionpool:connectionpool.py:544 https://huggingface.co:443 "HEAD /api/resolve-cache/models/hexgrad/Kokoro-82M/f3ff3571791e39611d31c381e3a41a3af07b4987/config.json HTTP/1.1" 200 0
DEBUG    urllib3.connectionpool:connectionpool.py:544 https://huggingface.co:443 "HEAD /hexgrad/Kokoro-82M/resolve/main/kokoro-v1_0.pth HTTP/1.1" 302 0
DEBUG    urllib3.connectionpool:connectionpool.py:1049 Starting new HTTPS connection (1): raw.githubusercontent.com:443
DEBUG    urllib3.connectionpool:connectionpool.py:544 https://raw.githubusercontent.com:443 "GET /explosion/spacy-models/master/compatibility.json HTTP/1.1" 200 4351
_ ERROR at setup of test_tts_duration[This is a longer text that should take more time.-4.0] _

    @pytest.fixture
    def tts_engine():
        """Create TTS engine instance."""
>       return TTSEngine(
            use_gpu=False  # Use CPU for testing
        )

tests/test_ai_models.py:22: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 
src/models/tts_engine.py:60: in __init__
    self.tts = TextToSpeech(
src/models/tts_engine.py:15: in __init__
    self.pipeline = KPipeline(lang_code=lang_code, device=device)
                    ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
/nix/store/a0c5008yydajwk21p00npcq13z73zfiw-python3.12-kokoro-0-unstable-2025-06-16/lib/python3.12/site-packages/kokoro/pipeline.py:123: in __init__
    self.g2p = en.G2P(trf=trf, british=lang_code=='b', fallback=fallback, unk='')
               ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
/nix/store/g8sm5vq513ywjdd3g6fm4mms7ff8mwzj-python3.12-misaki-0-unstable-2025-06-16/lib/python3.12/site-packages/misaki/en.py:527: in __init__
    spacy.cli.download(name)
/nix/store/mcshwhiak3cmj60ajpzx5i4889hjjlrw-python3.12-spacy-3.8.7/lib/python3.12/site-packages/spacy/cli/download.py:90: in download
    download_model(filename, pip_args)
/nix/store/mcshwhiak3cmj60ajpzx5i4889hjjlrw-python3.12-spacy-3.8.7/lib/python3.12/site-packages/spacy/cli/download.py:176: in download_model
    run_command(cmd)
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 

command = ['/nix/store/n34ywginm00ql3l97zxcr7zk3kq6z9v3-python3-3.12.12/bin/python', '-m', 'pip', 'install', 'https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl']

    def run_command(
        command: Union[str, List[str]],
        *,
        stdin: Optional[Any] = None,
        capture: bool = False,
    ) -> subprocess.CompletedProcess:
        """Run a command on the command line as a subprocess. If the subprocess
        returns a non-zero exit code, a system exit is performed.
        command (str / List[str]): The command. If provided as a string, the
            string will be split using shlex.split.
        stdin (Optional[Any]): stdin to read from or None.
        capture (bool): Whether to capture the output and errors. If False,
            the stdout and stderr will not be redirected, and if there's an error,
            sys.exit will be called with the return code. You should use capture=False
            when you want to turn over execution to the command, and capture=True
            when you want to run the command more like a function.
        RETURNS (Optional[CompletedProcess]): The process object.
        """
        if isinstance(command, str):
            cmd_list = split_command(command)
            cmd_str = command
        else:
            cmd_list = command
            cmd_str = " ".join(command)
        try:
            ret = subprocess.run(
                cmd_list,
                env=os.environ.copy(),
                input=stdin,
                encoding="utf8",
                check=False,
                stdout=subprocess.PIPE if capture else None,
                stderr=subprocess.STDOUT if capture else None,
            )
        except FileNotFoundError:
            # Indicates the *command* wasn't found, it's an error before the command
            # is run.
            raise FileNotFoundError(
                Errors.E970.format(str_command=cmd_str, tool=cmd_list[0])
            ) from None
        if ret.returncode != 0 and capture:
            message = f"Error running command:\n\n{cmd_str}\n\n"
            message += f"Subprocess exited with status {ret.returncode}"
            if ret.stdout is not None:
                message += f"\n\nProcess log (stdout and stderr):\n\n"
                message += ret.stdout
            error = subprocess.SubprocessError(message)
            error.ret = ret  # type: ignore[attr-defined]
            error.command = cmd_str  # type: ignore[attr-defined]
            raise error
        elif ret.returncode != 0:
>           sys.exit(ret.returncode)
E           SystemExit: 1

/nix/store/mcshwhiak3cmj60ajpzx5i4889hjjlrw-python3.12-spacy-3.8.7/lib/python3.12/site-packages/spacy/util.py:1046: SystemExit
---------------------------- Captured stdout setup -----------------------------
WARNING: Defaulting repo_id to hexgrad/Kokoro-82M. Pass repo_id='hexgrad/Kokoro-82M' to suppress this warning.
---------------------------- Captured stderr setup -----------------------------
2025-12-23 16:00:40,977 - urllib3.connectionpool - DEBUG - https://huggingface.co:443 "HEAD /hexgrad/Kokoro-82M/resolve/main/config.json HTTP/1.1" 307 0
2025-12-23 16:00:41,010 - urllib3.connectionpool - DEBUG - https://huggingface.co:443 "HEAD /api/resolve-cache/models/hexgrad/Kokoro-82M/f3ff3571791e39611d31c381e3a41a3af07b4987/config.json HTTP/1.1" 200 0
[32m16:00:41[0m | [36m      tts_engine:272[0m | [1m    INFO[0m | [1mTTS engine stopped[0m
2025-12-23 16:00:41,630 - urllib3.connectionpool - DEBUG - https://huggingface.co:443 "HEAD /hexgrad/Kokoro-82M/resolve/main/kokoro-v1_0.pth HTTP/1.1" 302 0
2025-12-23 16:00:41,801 - urllib3.connectionpool - DEBUG - Starting new HTTPS connection (1): raw.githubusercontent.com:443
2025-12-23 16:00:42,008 - urllib3.connectionpool - DEBUG - https://raw.githubusercontent.com:443 "GET /explosion/spacy-models/master/compatibility.json HTTP/1.1" 200 4351
/nix/store/n34ywginm00ql3l97zxcr7zk3kq6z9v3-python3-3.12.12/bin/python: No module named pip
------------------------------ Captured log setup ------------------------------
DEBUG    urllib3.connectionpool:connectionpool.py:544 https://huggingface.co:443 "HEAD /hexgrad/Kokoro-82M/resolve/main/config.json HTTP/1.1" 307 0
DEBUG    urllib3.connectionpool:connectionpool.py:544 https://huggingface.co:443 "HEAD /api/resolve-cache/models/hexgrad/Kokoro-82M/f3ff3571791e39611d31c381e3a41a3af07b4987/config.json HTTP/1.1" 200 0
DEBUG    urllib3.connectionpool:connectionpool.py:544 https://huggingface.co:443 "HEAD /hexgrad/Kokoro-82M/resolve/main/kokoro-v1_0.pth HTTP/1.1" 302 0
DEBUG    urllib3.connectionpool:connectionpool.py:1049 Starting new HTTPS connection (1): raw.githubusercontent.com:443
DEBUG    urllib3.connectionpool:connectionpool.py:544 https://raw.githubusercontent.com:443 "GET /explosion/spacy-models/master/compatibility.json HTTP/1.1" 200 4351
_________________ ERROR at setup of test_translation_pipeline __________________

    @pytest.fixture
    def tts_engine():
        """Create TTS engine instance."""
>       return TTSEngine(
            use_gpu=False  # Use CPU for testing
        )

tests/test_ai_models.py:22: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 
src/models/tts_engine.py:60: in __init__
    self.tts = TextToSpeech(
src/models/tts_engine.py:15: in __init__
    self.pipeline = KPipeline(lang_code=lang_code, device=device)
                    ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
/nix/store/a0c5008yydajwk21p00npcq13z73zfiw-python3.12-kokoro-0-unstable-2025-06-16/lib/python3.12/site-packages/kokoro/pipeline.py:123: in __init__
    self.g2p = en.G2P(trf=trf, british=lang_code=='b', fallback=fallback, unk='')
               ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
/nix/store/g8sm5vq513ywjdd3g6fm4mms7ff8mwzj-python3.12-misaki-0-unstable-2025-06-16/lib/python3.12/site-packages/misaki/en.py:527: in __init__
    spacy.cli.download(name)
/nix/store/mcshwhiak3cmj60ajpzx5i4889hjjlrw-python3.12-spacy-3.8.7/lib/python3.12/site-packages/spacy/cli/download.py:90: in download
    download_model(filename, pip_args)
/nix/store/mcshwhiak3cmj60ajpzx5i4889hjjlrw-python3.12-spacy-3.8.7/lib/python3.12/site-packages/spacy/cli/download.py:176: in download_model
    run_command(cmd)
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 

command = ['/nix/store/n34ywginm00ql3l97zxcr7zk3kq6z9v3-python3-3.12.12/bin/python', '-m', 'pip', 'install', 'https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl']

    def run_command(
        command: Union[str, List[str]],
        *,
        stdin: Optional[Any] = None,
        capture: bool = False,
    ) -> subprocess.CompletedProcess:
        """Run a command on the command line as a subprocess. If the subprocess
        returns a non-zero exit code, a system exit is performed.
        command (str / List[str]): The command. If provided as a string, the
            string will be split using shlex.split.
        stdin (Optional[Any]): stdin to read from or None.
        capture (bool): Whether to capture the output and errors. If False,
            the stdout and stderr will not be redirected, and if there's an error,
            sys.exit will be called with the return code. You should use capture=False
            when you want to turn over execution to the command, and capture=True
            when you want to run the command more like a function.
        RETURNS (Optional[CompletedProcess]): The process object.
        """
        if isinstance(command, str):
            cmd_list = split_command(command)
            cmd_str = command
        else:
            cmd_list = command
            cmd_str = " ".join(command)
        try:
            ret = subprocess.run(
                cmd_list,
                env=os.environ.copy(),
                input=stdin,
                encoding="utf8",
                check=False,
                stdout=subprocess.PIPE if capture else None,
                stderr=subprocess.STDOUT if capture else None,
            )
        except FileNotFoundError:
            # Indicates the *command* wasn't found, it's an error before the command
            # is run.
            raise FileNotFoundError(
                Errors.E970.format(str_command=cmd_str, tool=cmd_list[0])
            ) from None
        if ret.returncode != 0 and capture:
            message = f"Error running command:\n\n{cmd_str}\n\n"
            message += f"Subprocess exited with status {ret.returncode}"
            if ret.stdout is not None:
                message += f"\n\nProcess log (stdout and stderr):\n\n"
                message += ret.stdout
            error = subprocess.SubprocessError(message)
            error.ret = ret  # type: ignore[attr-defined]
            error.command = cmd_str  # type: ignore[attr-defined]
            raise error
        elif ret.returncode != 0:
>           sys.exit(ret.returncode)
E           SystemExit: 1

/nix/store/mcshwhiak3cmj60ajpzx5i4889hjjlrw-python3.12-spacy-3.8.7/lib/python3.12/site-packages/spacy/util.py:1046: SystemExit
---------------------------- Captured stdout setup -----------------------------
WARNING: Defaulting repo_id to hexgrad/Kokoro-82M. Pass repo_id='hexgrad/Kokoro-82M' to suppress this warning.
---------------------------- Captured stderr setup -----------------------------
[32m16:00:42[0m | [36mwhisper_recognition:51[0m | [1m    INFO[0m | [1mWhisper recognizer initialized: small model, auto->en[0m
2025-12-23 16:00:42,308 - urllib3.connectionpool - DEBUG - https://huggingface.co:443 "HEAD /hexgrad/Kokoro-82M/resolve/main/config.json HTTP/1.1" 307 0
2025-12-23 16:00:42,350 - urllib3.connectionpool - DEBUG - https://huggingface.co:443 "HEAD /api/resolve-cache/models/hexgrad/Kokoro-82M/f3ff3571791e39611d31c381e3a41a3af07b4987/config.json HTTP/1.1" 200 0
2025-12-23 16:00:42,946 - urllib3.connectionpool - DEBUG - https://huggingface.co:443 "HEAD /hexgrad/Kokoro-82M/resolve/main/kokoro-v1_0.pth HTTP/1.1" 302 0
2025-12-23 16:00:43,236 - urllib3.connectionpool - DEBUG - Starting new HTTPS connection (1): raw.githubusercontent.com:443
2025-12-23 16:00:43,443 - urllib3.connectionpool - DEBUG - https://raw.githubusercontent.com:443 "GET /explosion/spacy-models/master/compatibility.json HTTP/1.1" 200 4351
/nix/store/n34ywginm00ql3l97zxcr7zk3kq6z9v3-python3-3.12.12/bin/python: No module named pip
------------------------------ Captured log setup ------------------------------
DEBUG    urllib3.connectionpool:connectionpool.py:544 https://huggingface.co:443 "HEAD /hexgrad/Kokoro-82M/resolve/main/config.json HTTP/1.1" 307 0
DEBUG    urllib3.connectionpool:connectionpool.py:544 https://huggingface.co:443 "HEAD /api/resolve-cache/models/hexgrad/Kokoro-82M/f3ff3571791e39611d31c381e3a41a3af07b4987/config.json HTTP/1.1" 200 0
DEBUG    urllib3.connectionpool:connectionpool.py:544 https://huggingface.co:443 "HEAD /hexgrad/Kokoro-82M/resolve/main/kokoro-v1_0.pth HTTP/1.1" 302 0
DEBUG    urllib3.connectionpool:connectionpool.py:1049 Starting new HTTPS connection (1): raw.githubusercontent.com:443
DEBUG    urllib3.connectionpool:connectionpool.py:544 https://raw.githubusercontent.com:443 "GET /explosion/spacy-models/master/compatibility.json HTTP/1.1" 200 4351
_________________ ERROR at setup of test_model_error_handling __________________

    @pytest.fixture
    def tts_engine():
        """Create TTS engine instance."""
>       return TTSEngine(
            use_gpu=False  # Use CPU for testing
        )

tests/test_ai_models.py:22: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 
src/models/tts_engine.py:60: in __init__
    self.tts = TextToSpeech(
src/models/tts_engine.py:15: in __init__
    self.pipeline = KPipeline(lang_code=lang_code, device=device)
                    ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
/nix/store/a0c5008yydajwk21p00npcq13z73zfiw-python3.12-kokoro-0-unstable-2025-06-16/lib/python3.12/site-packages/kokoro/pipeline.py:123: in __init__
    self.g2p = en.G2P(trf=trf, british=lang_code=='b', fallback=fallback, unk='')
               ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
/nix/store/g8sm5vq513ywjdd3g6fm4mms7ff8mwzj-python3.12-misaki-0-unstable-2025-06-16/lib/python3.12/site-packages/misaki/en.py:527: in __init__
    spacy.cli.download(name)
/nix/store/mcshwhiak3cmj60ajpzx5i4889hjjlrw-python3.12-spacy-3.8.7/lib/python3.12/site-packages/spacy/cli/download.py:90: in download
    download_model(filename, pip_args)
/nix/store/mcshwhiak3cmj60ajpzx5i4889hjjlrw-python3.12-spacy-3.8.7/lib/python3.12/site-packages/spacy/cli/download.py:176: in download_model
    run_command(cmd)
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 

command = ['/nix/store/n34ywginm00ql3l97zxcr7zk3kq6z9v3-python3-3.12.12/bin/python', '-m', 'pip', 'install', 'https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl']

    def run_command(
        command: Union[str, List[str]],
        *,
        stdin: Optional[Any] = None,
        capture: bool = False,
    ) -> subprocess.CompletedProcess:
        """Run a command on the command line as a subprocess. If the subprocess
        returns a non-zero exit code, a system exit is performed.
        command (str / List[str]): The command. If provided as a string, the
            string will be split using shlex.split.
        stdin (Optional[Any]): stdin to read from or None.
        capture (bool): Whether to capture the output and errors. If False,
            the stdout and stderr will not be redirected, and if there's an error,
            sys.exit will be called with the return code. You should use capture=False
            when you want to turn over execution to the command, and capture=True
            when you want to run the command more like a function.
        RETURNS (Optional[CompletedProcess]): The process object.
        """
        if isinstance(command, str):
            cmd_list = split_command(command)
            cmd_str = command
        else:
            cmd_list = command
            cmd_str = " ".join(command)
        try:
            ret = subprocess.run(
                cmd_list,
                env=os.environ.copy(),
                input=stdin,
                encoding="utf8",
                check=False,
                stdout=subprocess.PIPE if capture else None,
                stderr=subprocess.STDOUT if capture else None,
            )
        except FileNotFoundError:
            # Indicates the *command* wasn't found, it's an error before the command
            # is run.
            raise FileNotFoundError(
                Errors.E970.format(str_command=cmd_str, tool=cmd_list[0])
            ) from None
        if ret.returncode != 0 and capture:
            message = f"Error running command:\n\n{cmd_str}\n\n"
            message += f"Subprocess exited with status {ret.returncode}"
            if ret.stdout is not None:
                message += f"\n\nProcess log (stdout and stderr):\n\n"
                message += ret.stdout
            error = subprocess.SubprocessError(message)
            error.ret = ret  # type: ignore[attr-defined]
            error.command = cmd_str  # type: ignore[attr-defined]
            raise error
        elif ret.returncode != 0:
>           sys.exit(ret.returncode)
E           SystemExit: 1

/nix/store/mcshwhiak3cmj60ajpzx5i4889hjjlrw-python3.12-spacy-3.8.7/lib/python3.12/site-packages/spacy/util.py:1046: SystemExit
---------------------------- Captured stdout setup -----------------------------
WARNING: Defaulting repo_id to hexgrad/Kokoro-82M. Pass repo_id='hexgrad/Kokoro-82M' to suppress this warning.
---------------------------- Captured stderr setup -----------------------------
[32m16:00:43[0m | [36mwhisper_recognition:51[0m | [1m    INFO[0m | [1mWhisper recognizer initialized: small model, auto->en[0m
2025-12-23 16:00:43,742 - urllib3.connectionpool - DEBUG - https://huggingface.co:443 "HEAD /hexgrad/Kokoro-82M/resolve/main/config.json HTTP/1.1" 307 0
2025-12-23 16:00:43,773 - urllib3.connectionpool - DEBUG - https://huggingface.co:443 "HEAD /api/resolve-cache/models/hexgrad/Kokoro-82M/f3ff3571791e39611d31c381e3a41a3af07b4987/config.json HTTP/1.1" 200 0
[32m16:00:44[0m | [36m      tts_engine:272[0m | [1m    INFO[0m | [1mTTS engine stopped[0m
2025-12-23 16:00:44,347 - urllib3.connectionpool - DEBUG - https://huggingface.co:443 "HEAD /hexgrad/Kokoro-82M/resolve/main/kokoro-v1_0.pth HTTP/1.1" 302 0
2025-12-23 16:00:44,563 - urllib3.connectionpool - DEBUG - Starting new HTTPS connection (1): raw.githubusercontent.com:443
2025-12-23 16:00:44,774 - urllib3.connectionpool - DEBUG - https://raw.githubusercontent.com:443 "GET /explosion/spacy-models/master/compatibility.json HTTP/1.1" 200 4351
/nix/store/n34ywginm00ql3l97zxcr7zk3kq6z9v3-python3-3.12.12/bin/python: No module named pip
------------------------------ Captured log setup ------------------------------
DEBUG    urllib3.connectionpool:connectionpool.py:544 https://huggingface.co:443 "HEAD /hexgrad/Kokoro-82M/resolve/main/config.json HTTP/1.1" 307 0
DEBUG    urllib3.connectionpool:connectionpool.py:544 https://huggingface.co:443 "HEAD /api/resolve-cache/models/hexgrad/Kokoro-82M/f3ff3571791e39611d31c381e3a41a3af07b4987/config.json HTTP/1.1" 200 0
DEBUG    urllib3.connectionpool:connectionpool.py:544 https://huggingface.co:443 "HEAD /hexgrad/Kokoro-82M/resolve/main/kokoro-v1_0.pth HTTP/1.1" 302 0
DEBUG    urllib3.connectionpool:connectionpool.py:1049 Starting new HTTPS connection (1): raw.githubusercontent.com:443
DEBUG    urllib3.connectionpool:connectionpool.py:544 https://raw.githubusercontent.com:443 "GET /explosion/spacy-models/master/compatibility.json HTTP/1.1" 200 4351
=================================== FAILURES ===================================
______________________ test_whisper_ukrainian_recognition ______________________

whisper_recognizer = <src.models.whisper_recognition.WhisperRecognizer object at 0x7ffe3798a7e0>

    def test_whisper_ukrainian_recognition(whisper_recognizer):
        """Test Ukrainian speech recognition."""
        # Generate test audio (this is a placeholder - real audio would be needed)
        audio_data = np.zeros(16000)  # 1 second of silence
    
        # Set Ukrainian as source language
        whisper_recognizer.set_languages("uk", "en")
    
        # Process audio
        result = whisper_recognizer._recognize_audio(audio_data)
    
>       assert result is not None[32m16:00:44[0m | [36m      tts_engine:272[0m | [1m    INFO[0m | [1mTTS engine stopped[0m
[32m16:00:44[0m | [36m      tts_engine:272[0m | [1m    INFO[0m | [1mTTS engine stopped[0m
[32m16:00:44[0m | [36m      tts_engine:272[0m | [1m    INFO[0m | [1mTTS engine stopped[0m

E       assert None is not None

tests/test_ai_models.py:37: AssertionError
---------------------------- Captured stderr setup -----------------------------
[32m16:00:37[0m | [36mwhisper_recognition:51[0m | [1m    INFO[0m | [1mWhisper recognizer initialized: small model, auto->en[0m
----------------------------- Captured stderr call -----------------------------
[32m16:00:37[0m | [36mwhisper_recognition:173[0m | [1m    INFO[0m | [1mLanguages updated: uk->en[0m
[32m16:00:37[0m | [36mwhisper_recognition:118[0m | [31m[1m   ERROR[0m | [31m[1mWhisper recognition failed: Error: unknown flag: --language
[0m
_______________________ test_whisper_polish_recognition ________________________

whisper_recognizer = <src.models.whisper_recognition.WhisperRecognizer object at 0x7ffe3785ef90>

    def test_whisper_polish_recognition(whisper_recognizer):
        """Test Polish speech recognition."""
        audio_data = np.zeros(16000)
        whisper_recognizer.set_languages("pl", "en")
    
        result = whisper_recognizer._recognize_audio(audio_data)
    
>       assert result is not None
E       assert None is not None

tests/test_ai_models.py:48: AssertionError
---------------------------- Captured stderr setup -----------------------------
[32m16:00:37[0m | [36mwhisper_recognition:51[0m | [1m    INFO[0m | [1mWhisper recognizer initialized: small model, auto->en[0m
----------------------------- Captured stderr call -----------------------------
[32m16:00:37[0m | [36mwhisper_recognition:173[0m | [1m    INFO[0m | [1mLanguages updated: pl->en[0m
[32m16:00:37[0m | [36mwhisper_recognition:118[0m | [31m[1m   ERROR[0m | [31m[1mWhisper recognition failed: Error: unknown flag: --language
[0m
_____________________ test_whisper_auto_language_detection _____________________

whisper_recognizer = <src.models.whisper_recognition.WhisperRecognizer object at 0x7ffe3785e5a0>

    def test_whisper_auto_language_detection(whisper_recognizer):
        """Test automatic language detection."""
        audio_data = np.zeros(16000)
        whisper_recognizer.set_languages("auto", "en")
    
        result = whisper_recognizer._recognize_audio(audio_data)
    
>       assert result is not None
E       assert None is not None

tests/test_ai_models.py:59: AssertionError
---------------------------- Captured stderr setup -----------------------------
[32m16:00:37[0m | [36mwhisper_recognition:51[0m | [1m    INFO[0m | [1mWhisper recognizer initialized: small model, auto->en[0m
----------------------------- Captured stderr call -----------------------------
[32m16:00:37[0m | [36mwhisper_recognition:173[0m | [1m    INFO[0m | [1mLanguages updated: auto->en[0m
[32m16:00:37[0m | [36mwhisper_recognition:118[0m | [31m[1m   ERROR[0m | [31m[1mWhisper recognition failed: Error: unknown flag: --language
[0m
___________________________ test_performance_metrics ___________________________

whisper_recognizer = <src.models.whisper_recognition.WhisperRecognizer object at 0x7ffde7ff1fd0>

    def test_performance_metrics(whisper_recognizer):
        """Test performance monitoring."""
        audio_data = np.zeros(16000)
        result = whisper_recognizer._recognize_audio(audio_data)
    
>       assert 'processing_time' in result
               ^^^^^^^^^^^^^^^^^^^^^^^^^^^
E       TypeError: argument of type 'NoneType' is not iterable

tests/test_ai_models.py:145: TypeError
---------------------------- Captured stderr setup -----------------------------
[32m16:00:44[0m | [36mwhisper_recognition:51[0m | [1m    INFO[0m | [1mWhisper recognizer initialized: small model, auto->en[0m
----------------------------- Captured stderr call -----------------------------
[32m16:00:44[0m | [36mwhisper_recognition:118[0m | [31m[1m   ERROR[0m | [31m[1mWhisper recognition failed: Error: unknown flag: --language
[0m
=============================== warnings summary ===============================
../../../nix/store/qr9w9xklgb5ddaqwc4fbqm0lkm8nzk2f-python3.12-transformers-4.57.1/lib/python3.12/site-packages/transformers/utils/hub.py:110
  /nix/store/qr9w9xklgb5ddaqwc4fbqm0lkm8nzk2f-python3.12-transformers-4.57.1/lib/python3.12/site-packages/transformers/utils/hub.py:110: FutureWarning: Using `TRANSFORMERS_CACHE` is deprecated and will be removed in v5 of Transformers. Use `HF_HOME` instead.
    warnings.warn(

../../../nix/store/mcshwhiak3cmj60ajpzx5i4889hjjlrw-python3.12-spacy-3.8.7/lib/python3.12/site-packages/spacy/cli/_util.py:23
  /nix/store/mcshwhiak3cmj60ajpzx5i4889hjjlrw-python3.12-spacy-3.8.7/lib/python3.12/site-packages/spacy/cli/_util.py:23: DeprecationWarning: Importing 'parser.split_arg_string' is deprecated, it will only be available in 'shell_completion' in Click 9.0.
    from click.parser import split_arg_string

tests/test_ai_models.py::test_tts_synthesis
tests/test_ai_models.py::test_tts_duration[Hello, how are you?-2.0]
tests/test_ai_models.py::test_tts_duration[This is a longer text that should take more time.-4.0]
tests/test_ai_models.py::test_translation_pipeline
tests/test_ai_models.py::test_model_error_handling
  /nix/store/acziliyvc6aq3k0ryrnilajj7r36g329-python3.12-torch-2.9.1/lib/python3.12/site-packages/torch/nn/modules/rnn.py:123: UserWarning: dropout option adds dropout after all but last recurrent layer, so non-zero dropout expects num_layers greater than 1, but got dropout=0.2 and num_layers=1
    warnings.warn(

tests/test_ai_models.py::test_tts_synthesis
tests/test_ai_models.py::test_tts_duration[Hello, how are you?-2.0]
tests/test_ai_models.py::test_tts_duration[This is a longer text that should take more time.-4.0]
tests/test_ai_models.py::test_translation_pipeline
tests/test_ai_models.py::test_model_error_handling
  /nix/store/acziliyvc6aq3k0ryrnilajj7r36g329-python3.12-torch-2.9.1/lib/python3.12/site-packages/torch/nn/utils/weight_norm.py:144: FutureWarning: `torch.nn.utils.weight_norm` is deprecated in favor of `torch.nn.utils.parametrizations.weight_norm`.
    WeightNorm.apply(module, name, dim)

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
=========================== short test summary info ============================
FAILED tests/test_ai_models.py::test_whisper_ukrainian_recognition - assert None is not None
FAILED tests/test_ai_models.py::test_whisper_polish_recognition - assert None is not None
FAILED tests/test_ai_models.py::test_whisper_auto_language_detection - assert None is not None
FAILED tests/test_ai_models.py::test_performance_metrics - TypeError: argument of type 'NoneType' is not iterable
ERROR tests/test_ai_models.py::test_tts_synthesis - SystemExit: 1
ERROR tests/test_ai_models.py::test_tts_duration[Hello, how are you?-2.0] - SystemExit: 1
ERROR tests/test_ai_models.py::test_tts_duration[This is a longer text that should take more time.-4.0] - SystemExit: 1
ERROR tests/test_ai_models.py::test_translation_pipeline - SystemExit: 1
ERROR tests/test_ai_models.py::test_model_error_handling - SystemExit: 1
======== 4 failed, 1 passed, 1 skipped, 12 warnings, 5 errors in 11.96s ========
Running audio tests (excluding problematic test_audio_levels)...
Real-time Translator development environment ready!
Use 'python3 -m src.main' to start the application

PipeWire virtual sinks have been created:
  - rt_virtual_input (RT-Virtual-Input)
  - rt_virtual_output (RT-Virtual-Output)
These are available for audio routing in the development environment.
============================= test session starts ==============================
platform linux -- Python 3.12.12, pytest-8.4.2, pluggy-1.6.0 -- /nix/store/n34ywginm00ql3l97zxcr7zk3kq6z9v3-python3-3.12.12/bin/python
cachedir: .pytest_cache
hypothesis profile 'ci' -> database=None, deadline=None, print_blob=True, derandomize=True, suppress_health_check=(HealthCheck.too_slow,)
rootdir: /home/dmaslo/real-time-transletor
plugins: hypothesis-6.136.9, typeguard-4.4.4
collecting ... collected 7 items / 1 deselected / 6 selected

tests/test_audio.py::test_audio_capture_initialization PASSED            [ 16%]
tests/test_audio.py::test_audio_device_listing PASSED                    [ 33%]
tests/test_audio.py::test_audio_routing_virtual_devices PASSED           [ 50%]
tests/test_audio.py::test_audio_processor_speech_detection FAILED        [ 66%]
tests/test_audio.py::test_audio_pipeline Audio tests completed or timed out
