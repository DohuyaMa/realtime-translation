import dataclasses

import pytest

from src.services.common.env import (
    SettingsError,
    env_bool,
    env_choice,
    env_float,
    env_int,
    env_str,
)
from src.services.common.settings import (
    DEFAULT_PORTS,
    BaseSettings,
    apply_model_dir,
    base_from_env,
    render_env_value,
)


class AsrSettings(BaseSettings):
    BACKEND_ENV = "RT_ASR_BACKEND"


def _base(env):
    return AsrSettings(
        **base_from_env(
            env,
            service="rt-asr",
            backends=("faster-whisper", "fake"),
            default_backend="faster-whisper",
            default_model="small",
            compute_cpu="int8",
            compute_gpu="float16",
            settings_cls=AsrSettings,
        )
    )


def test_env_helpers_defaults_and_parsing():
    env = {"S": " x ", "I": "7", "F": "0.5", "B": "Yes", "C": "CUDA", "E": "  "}
    assert env_str(env, "S", None) == "x"
    assert env_str(env, "E", "d") == "d"
    assert env_int(env, "I", 1) == 7
    assert env_float(env, "F", 1.0) == 0.5
    assert env_bool(env, "B", False) is True
    assert env_choice(env, "C", "cpu", ("cpu", "cuda")) == "cuda"
    assert env_int(env, "MISSING", 3) == 3


@pytest.mark.parametrize(
    "fn,args",
    [
        (env_int, ({"K": "x"}, "K", 1)),
        (env_float, ({"K": "x"}, "K", 1.0)),
        (env_bool, ({"K": "maybe"}, "K", True)),
        (env_choice, ({"K": "tpu"}, "K", "cpu", ("cpu", "cuda"))),
    ],
)
def test_env_helpers_reject_invalid(fn, args):
    with pytest.raises(SettingsError):
        fn(*args)


def test_env_int_range():
    with pytest.raises(SettingsError):
        env_int({"K": "0"}, "K", 1, min_value=1)


def test_base_defaults():
    s = _base({})
    assert (s.service, s.host, s.port, s.device) == (
        "rt-asr",
        "127.0.0.1",
        8091,
        "cpu",
    )
    assert (s.model, s.compute_type, s.backend, s.max_pending) == (
        "small",
        "int8",
        "faster-whisper",
        16,
    )
    assert s.api_token_file is None and s.log_level == "info"
    assert s.config_state is None
    assert s.torch_device == "cpu" and not s.is_gpu


def test_base_gpu_and_rocm():
    assert _base({"RT_DEVICE": "cuda"}).compute_type == "float16"
    rocm = _base({"RT_DEVICE": "rocm"})
    assert rocm.torch_device == "cuda" and rocm.is_gpu


def test_base_rejects_bad_port():
    with pytest.raises(SettingsError):
        _base({"RT_PORT": "70000"})


def test_apply_model_dir_sets_hf_home_only_when_unset():
    s = _base({"RT_MODEL_DIR": "/var/lib/rt-asr/models"})
    env = {}
    apply_model_dir(s, env)
    assert env["HF_HOME"] == "/var/lib/rt-asr/models/hf"
    env = {"HF_HOME": "/keep"}
    apply_model_dir(s, env)
    assert env["HF_HOME"] == "/keep"


def test_default_ports_have_no_mt():
    assert set(DEFAULT_PORTS) == {"rt-asr", "rt-tts"}


def test_render_env_value():
    assert render_env_value(True) == "true"
    assert render_env_value(False) == "false"
    assert render_env_value(("pl", "uk")) == "pl,uk"
    assert render_env_value(["a", "b"]) == "a,b"
    assert render_env_value(None) == ""
    assert render_env_value(2.5) == "2.5"


def test_config_state_from_env():
    s = _base({"RT_CONFIG_STATE": "/x/rc.json"})
    assert s.config_state == "/x/rc.json"


def test_env_keys_cover_every_env_backed_field():
    fields = {f.name for f in dataclasses.fields(AsrSettings)} - {"service"}
    assert set(AsrSettings.env_keys()) == fields
    assert AsrSettings.env_keys()["backend"] == "RT_ASR_BACKEND"


def test_tunable_and_reload_fields_have_env_keys():
    keys = set(AsrSettings.env_keys())
    assert (AsrSettings.TUNABLE_FIELDS | AsrSettings.RELOAD_FIELDS) <= keys


def test_bootstrap_fields():
    assert AsrSettings.bootstrap_fields() == {
        "host",
        "port",
        "api_token_file",
        "model_dir",
        "config_state",
        "max_pending",
        "idle_unload_minutes",
    }


def test_base_without_backend_env_is_rejected():
    with pytest.raises(TypeError):
        BaseSettings.bootstrap_fields()
    with pytest.raises(TypeError):
        base_from_env(
            {},
            service="rt-asr",
            backends=("fake",),
            default_backend="fake",
            default_model="m",
            compute_cpu="int8",
            compute_gpu="float16",
        )


def test_base_from_env_ignores_os_environ(monkeypatch):
    monkeypatch.setenv("RT_PORT", "1")
    assert _base({}).port == 8091


def test_base_from_env_uses_subclass_env_keys():
    class Custom(AsrSettings):
        ENV_KEYS = {**BaseSettings.ENV_KEYS, "host": "RT_CUSTOM_HOST"}

    kw = dict(
        service="rt-asr",
        backends=("fake",),
        default_backend="fake",
        default_model="m",
        compute_cpu="int8",
        compute_gpu="float16",
        settings_cls=Custom,
    )
    assert base_from_env({"RT_CUSTOM_HOST": "h"}, **kw)["host"] == "h"
    assert base_from_env({"RT_HOST": "x"}, **kw)["host"] == "127.0.0.1"
