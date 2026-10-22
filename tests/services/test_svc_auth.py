import pytest
from fastapi import APIRouter, Depends, FastAPI, WebSocket
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from src.services.common.auth import load_token, make_bearer_dependency, ws_authorized
from src.services.common.env import SettingsError
from src.services.common.errors import install_error_handlers


def _app(token):
    app = FastAPI()
    install_error_handlers(app)
    v1 = APIRouter(prefix="/v1", dependencies=[Depends(make_bearer_dependency(token))])

    @v1.get("/ping")
    async def ping():
        return {"pong": True}

    app.include_router(v1)

    @app.websocket("/v1/ws")
    async def ws(websocket: WebSocket):
        if not ws_authorized(websocket, token):
            await websocket.close(code=1008)
            return
        await websocket.accept()
        await websocket.send_json({"ok": True})
        await websocket.close()

    return app


def test_load_token(tmp_path):
    assert load_token(None) is None
    f = tmp_path / "token"
    f.write_text("  s3cret\n")
    assert load_token(str(f)) == "s3cret"


@pytest.mark.parametrize("content", [None, "   \n"])
def test_load_token_fails_closed(tmp_path, content):
    f = tmp_path / "token"
    if content is not None:
        f.write_text(content)
    with pytest.raises(SettingsError):
        load_token(str(f))


def test_no_token_means_open():
    assert TestClient(_app(None)).get("/v1/ping").status_code == 200


def test_bearer_required():
    client = TestClient(_app("s3cret"))
    r = client.get("/v1/ping")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthorized"
    assert r.headers["www-authenticate"] == "Bearer"
    wrong = {"Authorization": "Bearer wrong"}
    assert client.get("/v1/ping", headers=wrong).status_code == 401
    ok = {"Authorization": "Bearer s3cret"}
    assert client.get("/v1/ping", headers=ok).status_code == 200
    lower = {"Authorization": "bearer s3cret"}
    assert client.get("/v1/ping", headers=lower).status_code == 200


def test_ws_token():
    client = TestClient(_app("s3cret"))
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/v1/ws"):
            pass
    assert exc.value.code == 1008
    headers = {"Authorization": "Bearer s3cret"}
    with client.websocket_connect("/v1/ws", headers=headers) as ws:
        assert ws.receive_json() == {"ok": True}
