import json
import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.services.common.app import create_service_app
from src.services.common.runtime import ServiceRuntime
from src.services.common.runtime_config import RuntimeConfig, build_config_router
from src.services.common.settings import BaseSettings, base_from_env


class AsrSettings(BaseSettings):
    BACKEND_ENV = "RT_ASR_BACKEND"
    TUNABLE_FIELDS = frozenset({"log_level", "max_pending"})

    @classmethod
    def from_env(cls, env):
        return cls(
            **base_from_env(
                env,
                settings_cls=cls,
                service="rt-asr",
                backends=("fake", "other"),
                default_backend="fake",
                default_model="whisper-base",
                compute_cpu="float32",
                compute_gpu="float16",
            )
        )


def _cfg(state_path=None, env=None):
    return RuntimeConfig(AsrSettings, env or {}, state_path)


def test_effective_shape_bootstrap_only():
    eff = _cfg().effective()
    assert set(eff) == {"values", "source", "bootstrap", "version"}
    assert eff["version"] == 0
    assert eff["values"] == eff["bootstrap"]
    assert set(eff["values"]) == {
        "log_level",
        "max_pending",
        "backend",
        "model",
        "device",
        "compute_type",
    }
    assert set(eff["source"].values()) == {"bootstrap"}


def test_apply_tunable_changes_current_and_source():
    cfg = _cfg()
    assert cfg.apply({"log_level": "debug"}, 1) == []
    assert cfg.current().log_level == "debug"
    eff = cfg.effective()
    assert eff["source"]["log_level"] == "override"
    assert eff["source"]["model"] == "bootstrap"
    assert eff["version"] == 1
    assert eff["bootstrap"]["log_level"] == "info"


def test_apply_reload_field_reported():
    cfg = _cfg()
    assert cfg.apply({"model": "whisper-small"}, 2) == ["model"]
    assert cfg.current().model == "whisper-small"
    assert cfg.apply({"model": "whisper-small"}, 3) == []


def test_apply_invalid_is_all_or_nothing():
    cfg = _cfg()
    with pytest.raises(ValueError):
        cfg.apply({"log_level": "debug", "max_pending": -1}, 3)
    assert cfg.current().log_level == "info"
    assert cfg.effective()["version"] == 0


def test_bootstrap_field_rejected():
    with pytest.raises(ValueError, match="port is not runtime-configurable"):
        _cfg().apply({"port": 1}, 1)


def test_unknown_key_ignored():
    cfg = _cfg()
    cfg.apply({"nope": 1}, 1)
    assert cfg.effective()["version"] == 1
    assert set(cfg.effective()["source"].values()) == {"bootstrap"}


def test_reset_restores_bootstrap():
    cfg = _cfg(env={"RT_LOG_LEVEL": "warning"})
    cfg.apply({"log_level": "debug"}, 1)
    assert cfg.current().log_level == "debug"
    cfg.apply({}, 2, reset=["log_level"])
    assert cfg.current().log_level == "warning"
    assert cfg.effective()["source"]["log_level"] == "bootstrap"


def test_state_persisted_and_restored(tmp_path):
    path = tmp_path / "state.json"
    cfg = _cfg(path)
    cfg.apply({"log_level": "debug", "model": "m2"}, 5)
    assert json.loads(path.read_text()) == {
        "version": 5,
        "overrides": {"log_level": "debug", "model": "m2"},
    }
    assert [p.name for p in tmp_path.iterdir()] == ["state.json"]
    fresh = _cfg(path)
    fresh.restore()
    assert fresh.current().model == "m2"
    assert fresh.effective()["version"] == 5
    assert fresh.effective()["source"]["log_level"] == "override"


def test_restore_missing_file_is_silent(tmp_path, caplog):
    cfg = _cfg(tmp_path / "none.json")
    cfg.restore()
    assert cfg.effective()["version"] == 0
    assert not caplog.records


@pytest.mark.parametrize(
    "content",
    [
        "{not json",
        '{"version": 1}',
        '{"version": 1, "overrides": {"port": 9}}',
        '{"version": 1, "overrides": {"max_pending": -3}}',
    ],
)
def test_corrupt_state_file_keeps_bootstrap(tmp_path, caplog, content):
    path = tmp_path / "state.json"
    path.write_text(content)
    cfg = _cfg(path)
    with caplog.at_level(logging.WARNING):
        cfg.restore()
    assert any(r.levelno == logging.WARNING for r in caplog.records)
    assert cfg.effective()["version"] == 0
    assert cfg.current().max_pending == 16


def _client(cfg, on_reload):
    app = FastAPI()
    app.include_router(build_config_router(cfg, on_reload))
    return TestClient(app)


def test_router_get_post_and_422():
    cfg = _cfg()
    calls = []
    client = _client(cfg, calls.append)
    assert client.get("/config").json()["version"] == 0
    r = client.post("/config", json={"version": 1, "values": {"max_pending": 0}})
    assert r.status_code == 422 and "RT_MAX_PENDING" in r.json()["detail"]
    r = client.post("/config", json={"version": 1, "values": {"port": 1}})
    assert r.status_code == 422
    r = client.post("/config", json={"version": 1, "values": {"log_level": "debug"}})
    assert r.status_code == 200
    assert r.json()["reloaded"] is False and r.json()["version"] == 1
    assert calls == []
    r = client.post("/config", json={"version": 2, "values": {"model": "x"}})
    assert r.json()["reloaded"] is True
    assert [c.model for c in calls] == ["x"]


def test_router_reload_failure_rolls_back(tmp_path):
    cfg = _cfg(tmp_path / "s.json")
    cfg.apply({"log_level": "debug"}, 1)

    def boom(_):
        raise RuntimeError("load failed")

    client = _client(cfg, boom)
    r = client.post("/config", json={"version": 2, "values": {"model": "x"}})
    assert r.status_code == 500 and r.json()["detail"] == "load failed"
    eff = client.get("/config").json()
    assert eff["values"]["model"] == "whisper-base"
    assert eff["values"]["log_level"] == "debug"
    assert eff["version"] == 1
    assert json.loads((tmp_path / "s.json").read_text())["version"] == 1


def test_router_reload_runs_off_event_loop_thread():
    import threading

    seen = []
    client = _client(_cfg(), lambda _: seen.append(threading.current_thread().name))
    client.post("/config", json={"version": 1, "values": {"model": "x"}})
    assert seen and seen[0] != threading.current_thread().name


def test_app_mounts_config_behind_bearer():
    cfg = _cfg()
    settings = cfg.current()
    runtime = ServiceRuntime("rt-asr", lambda: object(), settings.max_pending)
    reloads = []
    app = create_service_app(
        settings=settings,
        runtime=runtime,
        token="sekret",
        config=cfg,
        on_reload=reloads.append,
    )
    client = TestClient(app)
    assert client.get("/config").status_code == 401
    assert client.post("/config", json={"version": 1, "values": {}}).status_code == 401
    auth = {"Authorization": "Bearer sekret"}
    assert client.get("/config", headers=auth).status_code == 200
    r = client.post(
        "/config", json={"version": 1, "values": {"model": "x"}}, headers=auth
    )
    assert r.status_code == 200 and len(reloads) == 1
