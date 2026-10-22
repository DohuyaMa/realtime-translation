# src/services/common/models_cli.py
"""rt-models: list / fetch / verify / path for pinned models (spec §4.7)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Callable

from .hf_env import configure_hf_home
from .model_registry import REGISTRY as DEFAULT_REGISTRY
from .model_registry import ModelSpec, UnknownModel
from .model_store import (
    ModelIntegrityError,
    ModelNotProvisioned,
    ModelSettings,
    ModelStore,
)


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="rt-models", description=__doc__)
    p.add_argument(
        "--model-dir", help="default: $RT_MODEL_DIR or ~/.cache/rt-speech/models"
    )
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    fetch = sub.add_parser("fetch")
    fetch.add_argument("aliases", nargs="+")
    verify = sub.add_parser("verify")
    verify.add_argument("--full", action="store_true")
    verify.add_argument("aliases", nargs="*")
    path = sub.add_parser("path")
    path.add_argument("alias")
    return p


def main(
    argv: list[str] | None = None,
    *,
    registry: dict[str, ModelSpec] | None = None,
    store_factory: Callable[[Path], ModelStore] | None = None,
) -> int:
    args = _parser().parse_args(argv)
    registry = DEFAULT_REGISTRY if registry is None else registry
    settings = ModelSettings.from_env()
    model_dir = Path(args.model_dir) if args.model_dir else settings.model_dir
    configure_hf_home(model_dir)
    store = (store_factory or ModelStore)(model_dir)

    def spec_of(alias: str) -> ModelSpec:
        if alias not in registry:
            raise UnknownModel(
                f"unknown model alias {alias!r}; known: {', '.join(sorted(registry))}"
            )
        return registry[alias]

    try:
        if args.cmd == "list":
            for spec in registry.values():
                status = "ok" if not store.verify(spec) else "missing"
                print(f"{spec.alias}\t{spec.service}\t{spec.license}\t{status}")
            return 0
        if args.cmd == "path":
            print(store.model_path(spec_of(args.alias).alias))
            return 0
        if args.cmd == "fetch":
            for alias in args.aliases:
                path = store.ensure(
                    spec_of(alias),
                    download=True,
                    token=settings.token(),
                    full_verify=True,
                )
                print(f"{alias}: ok {path}")
            return 0
        if args.cmd == "verify":
            worst = 0
            for alias in args.aliases or list(registry):
                problems = store.verify(spec_of(alias), full=args.full)
                for kind, message in problems:
                    print(f"{alias}: {kind}: {message}", file=sys.stderr)
                    worst = max(worst, 2 if kind == "missing" else 3)
                if not problems:
                    print(f"{alias}: ok")
            return worst
    except (UnknownModel, ModelNotProvisioned) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except ModelIntegrityError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
