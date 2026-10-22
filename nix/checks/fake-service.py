"""Stand-in for rt-asr/rt-tts in the NixOS VM test (stdlib only)."""
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOKEN_FILE = os.environ.get("RT_API_TOKEN_FILE")
TOKEN = open(TOKEN_FILE).read().strip() if TOKEN_FILE else None
SERVICE = os.path.basename(sys.argv[0])
STATE_FILE = os.environ.get("RT_CONFIG_STATE")


def load_config():
    if STATE_FILE and os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"version": 0, "values": {}}


CONFIG = load_config()


def save_config():
    if not STATE_FILE:
        return
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(CONFIG, f, sort_keys=True)
    os.replace(tmp, STATE_FILE)


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, payload):
        body = json.dumps(payload, sort_keys=True).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            return self._send(200, {"status": "ok"})
        if self.path == "/status":
            env = {k: v for k, v in os.environ.items() if k.startswith(("RT_", "HF_"))}
            return self._send(200, {
                "service": SERVICE,
                "ready": True,
                "default_voice": os.environ.get("RT_TTS_VOICE"),
                "env": env,
            })
        if self.path == "/config":
            return self._send(200, CONFIG)
        if self.path.startswith("/v1/"):
            if TOKEN is not None and self.headers.get("Authorization") != f"Bearer {TOKEN}":
                return self._send(401, {"error": "unauthorized"})
            return self._send(200, {"pong": True})
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path == "/config":
            length = int(self.headers.get("Content-Length", "0"))
            try:
                values = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                return self._send(400, {"error": "bad json"})
            CONFIG["version"] += 1
            CONFIG["values"] = values
            save_config()
            return self._send(200, CONFIG)
        return self._send(404, {"error": "not found"})


ThreadingHTTPServer((os.environ.get("RT_HOST", "127.0.0.1"), int(os.environ["RT_PORT"])), Handler).serve_forever()
