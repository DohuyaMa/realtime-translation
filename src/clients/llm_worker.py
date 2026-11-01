"""Model administration of a llama worker (settings dialog, «Переклад» tab).

The worker serves ONE model in memory for every client: this app's
translation and the Django queue alike. The ``model`` field of a chat
request is ignored by the worker (it only echoes it back), so the model that
translates is the one loaded here. Routes (worker ``api/routes_models.py``,
``api/routes_status.py``):

- ``GET /models`` - model files in the worker's MODEL_DIR;
- ``POST /models/load`` - ``model_name`` (a file in MODEL_DIR) or ``repo_id``
  (+ optional ``filename``: download from Hugging Face into MODEL_DIR, then
  load). Synchronous: the answer comes after the model is in memory; no
  download progress is published;
- ``DELETE /models/unload``;
- ``GET /status`` - ``model`` (loaded, path, source, load_error) and app
  sessions / Django polling state.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional

from .base import ServiceClient, ServiceError


def model_basename(path: Optional[str]) -> str:
    """GGUF basename of a local name or an ``hf:repo/file`` spec."""
    if not path:
        return ""
    if path.startswith("hf:"):
        path = path[3:]
    return path.rstrip("/").split("/")[-1]


@dataclass(frozen=True)
class WorkerModel:
    """``GET /status`` ``model`` of a llama worker."""

    loaded: bool
    path: Optional[str]  # local file name or "hf:<repo>/<file>"
    source: str  # none | local | huggingface
    error: Optional[str] = None
    app_sessions: int = 0
    django_polling: Optional[str] = None  # active | paused

    @property
    def name(self) -> str:
        return model_basename(self.path)


def parse_worker_status(status: Mapping[str, Any]) -> WorkerModel:
    model = status.get("model")
    if not isinstance(model, Mapping):
        raise ServiceError("llm", "http", f"no model in /status: {status!r:.200}")
    return WorkerModel(
        loaded=bool(model.get("loaded")),
        path=model.get("path") or None,
        source=str(model.get("source_type") or "none"),
        error=model.get("load_error") or None,
        app_sessions=int(status.get("app_sessions") or 0),
        django_polling=status.get("django_polling"),
    )


class LlamaWorkerClient(ServiceClient):
    service = "llm"

    def worker_models(self) -> list:
        """GET /models: ``[{"name", "size_bytes", ...}]`` of MODEL_DIR."""
        data = self._json(self._request("GET", "/models"))
        models = data.get("models")
        if not isinstance(models, list):
            raise ServiceError(
                self.service, "http", f"unexpected /models: {data!r:.200}"
            )
        return models

    def worker_status(self) -> WorkerModel:
        return parse_worker_status(self.get_status())

    def load_worker_model(
        self,
        *,
        model_name: Optional[str] = None,
        repo_id: Optional[str] = None,
        filename: Optional[str] = None,
        timeout_s: Optional[float] = None,
    ) -> dict:
        """POST /models/load: a local file (``model_name``) or a Hugging
        Face ``repo_id`` (+ ``filename``); blocks until the model is loaded."""
        if bool(model_name) == bool(repo_id):
            raise ValueError("exactly one of model_name / repo_id is required")
        params = {"model_name": model_name} if model_name else {"repo_id": repo_id}
        if repo_id and filename:
            params["filename"] = filename
        return self._json(
            self._request("POST", "/models/load", params=params, timeout_s=timeout_s)
        )

    def unload_worker_model(self, *, timeout_s: Optional[float] = None) -> dict:
        """DELETE /models/unload (``status`` = unloaded | no_model_loaded)."""
        return self._json(
            self._request("DELETE", "/models/unload", timeout_s=timeout_s)
        )
