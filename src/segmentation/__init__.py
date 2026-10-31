"""Pause-aware segmentation shared by the edge and the ASR server."""

from src.segmentation.chunks import plan_chunks
from src.segmentation.pause import (
    SAMPLE_RATE,
    WINDOW_MS,
    WINDOW_SAMPLES,
    PauseSegmenter,
    Segment,
    SegmenterConfig,
)
from src.segmentation.text import ends_sentence, split_complete

__all__ = [
    "SAMPLE_RATE",
    "WINDOW_MS",
    "WINDOW_SAMPLES",
    "PauseSegmenter",
    "Segment",
    "SegmenterConfig",
    "ends_sentence",
    "plan_chunks",
    "split_complete",
]
