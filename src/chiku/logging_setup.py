"""Logging: rotating file in LOCALAPPDATA. Transcripts and replies are never logged."""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path


def setup_logging(logs_dir: Path, console: bool = False, level: int = logging.INFO) -> Path:
    logs_dir.mkdir(parents=True, exist_ok=True)
    path = logs_dir / "chiku.log"
    root = logging.getLogger()
    root.setLevel(level)
    for h in list(root.handlers):
        root.removeHandler(h)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(threadName)s %(name)s: %(message)s")
    fh = logging.handlers.RotatingFileHandler(path, maxBytes=1_000_000, backupCount=3,
                                              encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)
    if console:
        ch = logging.StreamHandler()
        ch.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
        ch.setLevel(logging.WARNING)
        root.addHandler(ch)
    for noisy in ("httpx", "httpcore", "faster_whisper", "kokoro_onnx"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    # espeak's "words count mismatch" warning fires on most sentences and is harmless.
    logging.getLogger("phonemizer").setLevel(logging.ERROR)
    return path
