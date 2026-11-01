"""Replay a recorded session through the phonetic gate and the sound-mode
("condition F") translation, offline, against real services.

    python -m scripts.replay_session \\
        ~/.local/state/real-time-translator/sessions/20261101-154159 \\
        --phonetic http://127.0.0.1:18092 [--phonetic-token-file F] \\
        [--llm http://127.0.0.1:8080] [--llm-token-file /run/llama/local-token] \\
        [--no-llm] [--timeout 1.5] [--out replay.jsonl] \
        [--sound-system-prompt-file F] [--sound-user-template-file F]

Input: the session's ``seg-<seq>.wav`` (the exact unit audio) and its
``phrases.jsonl`` (whisper text + word times, the live NEXT and the live
translation; recordings of any date - only those fields are read). Per unit,
in order, exactly as the edge does it (``translation_system._gate`` /
``_translate``): the wav -> rt-phonetic (one request, ``--timeout`` budget)
-> ``gate_reason`` -> the LLM with SOUND + TEXT + NEXT and the previous
pairs as context -> ``reject_reason`` (``complete=True``) -> one
whisper-only retry of a rejected sound-mode reply or of a ``too_short`` one
(with ``COMPLETE_RETRY_NOTE``). Printed per unit: gate + similarity, mode,
phonetic latency, the new translation next to the live one; then a summary
(gate drops by reason, modes, retries, latency p50/p90/max, over budget).
``--out`` keeps one JSON line per unit for later comparison. Tokens are read
from files and never printed.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import time
import wave
from collections import Counter, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

from src.clients.base import Credential, ServiceError, read_token
from src.clients.phonetic import PhoneticClient
from src.core.crosscheck import GATE_DROPS, Aligned, gate_reason, similarity
from src.core.output_guard import reject_reason
from src.core.runtime import DEFAULT_LOCAL_TOKEN_FILE, EdgeSettings, LlmSettings
from src.core.simul import Word
from src.translation_system import MODE_RETRY, MODE_SOUND, MODE_WHISPER

DEFAULT_TIMEOUT_S = 1.5


@dataclass
class Segment:
    seq: int
    wav: Path
    whisper: str
    words: List[Word]
    start_s: float
    language: Optional[str]
    next_text: str = ""
    live: Optional[str] = None  # the live translation (last pass of the seq)
    live_gate: Optional[str] = None


@dataclass
class Result:
    seq: int
    whisper: str
    phonetic: Optional[str] = None
    similarity: Optional[float] = None
    gate: Optional[str] = None
    mode: Optional[str] = None
    phonetic_ms: Optional[float] = None
    phonetic_error: Optional[str] = None
    translation: Optional[str] = None
    rejected: List[str] = field(default_factory=list)
    live: Optional[str] = None


def load_session(path: Path) -> List[Segment]:
    segs: Dict[int, Segment] = {}
    for line in (path / "phrases.jsonl").read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        if rec.get("type") == "segment":
            segs[rec["seq"]] = Segment(
                seq=rec["seq"],
                wav=path / rec["wav"],
                whisper=rec["whisper"],
                words=[
                    Word(w["word"], float(w["start"]), float(w["end"]))
                    for w in rec.get("whisper_words") or ()
                ],
                start_s=float(rec.get("start_s") or 0.0),
                language=rec.get("language"),
                live_gate=rec.get("gate"),
            )
        elif rec.get("type") == "translation" and rec["seq"] in segs:
            seg = segs[rec["seq"]]
            seg.next_text = rec.get("next") or ""
            seg.live = rec.get("translation")
    return [segs[k] for k in sorted(segs)]


def read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        if w.getframerate() != 16000 or w.getsampwidth() != 2:
            raise SystemExit(f"{path}: expected 16 kHz PCM16 (the recorded units)")
        return np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").copy()


def _translate(
    llm: Any, seg: Segment, src: str, ctx: deque, sound: Any, insist: bool = False
) -> tuple:
    out = " ".join(
        llm.translate_stream(
            seg.whisper,
            src,
            context=list(ctx),
            sound=sound,
            next_text=seg.next_text,
            insist_complete=insist,
        )
    )
    why = reject_reason(
        seg.whisper,
        out,
        seg.next_text,
        complete=True,
        sound="" if sound is None else sound.sound_text,
        terms=LlmSettings().terms,  # the replay tunes only the prompts
    )
    return out, why


def replay(
    segments: List[Segment],
    phonetic: Any,
    llm: Any = None,
    *,
    language: Optional[str] = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    context_pairs: int = LlmSettings().context_pairs,
) -> List[Result]:
    """The edge's per-unit gate + translation over recorded units;
    ``phonetic`` has ``transcribe``, ``llm`` ``translate_stream`` (None =
    gate only)."""
    ctx: deque = deque(maxlen=context_pairs)
    results = []
    for seg in segments:
        src = language or seg.language or "uk"
        r = Result(seg.seq, seg.whisper, live=seg.live)
        results.append(r)
        t0 = time.monotonic()
        sound = None
        try:
            ph = phonetic.transcribe(read_wav(seg.wav), src, timeout_s=timeout_s)
        except ServiceError as e:
            r.phonetic_error = e.kind
            r.mode = MODE_WHISPER
        else:
            r.phonetic, r.phonetic_ms = ph.text, round((time.monotonic() - t0) * 1e3)
            r.similarity = similarity(seg.whisper, ph.text, src)
            r.gate = gate_reason(seg.whisper, ph.text, src)
            if r.gate in GATE_DROPS:
                continue
            t = seg.start_s
            sound = Aligned.from_words(
                seg.words,
                [Word(w.word, t + w.start, t + w.end) for w in ph.words]
                or [Word(ph.text, t, t)],
            )
            r.mode = MODE_SOUND
        if llm is None:
            continue
        out, why = _translate(llm, seg, src, ctx, sound)
        if why == "too_short" or (why and sound is not None):
            r.rejected.append(why)
            r.mode, sound = MODE_RETRY, None
            out, why = _translate(llm, seg, src, ctx, None, why == "too_short")
        if why:
            r.rejected.append(why)
            continue
        r.translation = out
        ctx.append(
            (seg.whisper, out)
            if sound is None
            else (seg.whisper, out, sound.sound_text)
        )
    return results


def _pct(values: List[float], q: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * len(ordered)))]


def summary(results: List[Result], timeout_s: float) -> Dict[str, Any]:
    ms = [r.phonetic_ms for r in results if r.phonetic_ms is not None]
    return {
        "units": len(results),
        "gate": dict(Counter(r.gate or "no_phonetic" for r in results)),
        "dropped": sum(r.gate in GATE_DROPS for r in results),
        "modes": dict(Counter(r.mode for r in results if r.mode)),
        "rejected": dict(Counter(x for r in results for x in r.rejected)),
        "phonetic_errors": dict(
            Counter(r.phonetic_error for r in results if r.phonetic_error)
        ),
        "phonetic_ms": {
            "p50": _pct(ms, 0.5),
            "p90": _pct(ms, 0.9),
            "max": max(ms) if ms else None,
            "over_budget": sum(m > timeout_s * 1e3 for m in ms),
        },
        "translated": sum(r.translation is not None for r in results),
    }


class _Target:
    """LlmTarget of one worker URL; the token stays in memory only."""

    def __init__(self, url: str, token: str) -> None:
        self._url, self._token = url, token

    def current(self) -> tuple:
        return self._url, self._token

    def invalidate(self, kind: str) -> None:
        pass


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("session", type=Path)
    ap.add_argument("--phonetic", required=True, help="rt-phonetic base URL")
    ap.add_argument("--phonetic-token-file", default=None)
    ap.add_argument("--llm", default="http://127.0.0.1:8080")
    ap.add_argument("--llm-token-file", default=DEFAULT_LOCAL_TOKEN_FILE)
    ap.add_argument("--no-llm", action="store_true", help="gate only")
    ap.add_argument(
        "--sound-system-prompt-file", type=Path, help="default: the built-in one"
    )
    ap.add_argument(
        "--sound-user-template-file", type=Path, help="default: the built-in one"
    )
    ap.add_argument("--language", default=None, help="default: the recorded one")
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S)
    ap.add_argument("--out", type=Path, default=None, help="JSON line per unit")
    args = ap.parse_args()

    from src.clients.llm import LlmClient

    phonetic = PhoneticClient(
        args.phonetic,
        credential=Credential(read_token(args.phonetic_token_file) or ""),
        timeout_s=args.timeout,
    )
    llm = None
    if not args.no_llm:
        token = read_token(args.llm_token_file) or ""
        tuned: Dict[str, Any] = {"timeout_s": 60.0}
        for name in ("sound_system_prompt", "sound_user_template"):
            path = getattr(args, f"{name}_file")
            if path is not None:
                tuned[name] = path.read_text(encoding="utf-8").strip()
        settings = LlmSettings(**tuned)
        errors = dataclasses.replace(EdgeSettings(), llm=settings).validate()
        if errors:
            raise SystemExit("; ".join(errors))
        llm = LlmClient(_Target(args.llm, token), settings)
    segments = load_session(args.session)
    # warm the model up first: a cold load is no per-unit latency
    phonetic.transcribe(np.zeros(16000, dtype=np.int16), args.language or "uk")
    results = replay(
        segments, phonetic, llm, language=args.language, timeout_s=args.timeout
    )
    for r in results:
        sim = "-" if r.similarity is None else f"{r.similarity:.2f}"
        print(
            f"#{r.seq:<3} {r.gate or r.phonetic_error or '-':13} sim {sim:4}"
            f" {r.mode or 'dropped':12} {r.phonetic_ms or '-':>5} ms"
            f" | whisper {r.whisper!r} | sound {r.phonetic!r}"
        )
        if llm is not None and r.mode:
            rej = f" (rejected {', '.join(r.rejected)})" if r.rejected else ""
            print(f"      new{rej}: {r.translation!r}\n      live: {r.live!r}")
    print(json.dumps(summary(results, args.timeout), ensure_ascii=False, indent=1))
    if args.out:
        with args.out.open("w", encoding="utf-8") as f:
            for r in results:
                f.write(json.dumps(r.__dict__, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
