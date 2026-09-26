"""Milestone 6C entry edge / early excursion study."""
from __future__ import annotations

import base64
import zlib
import sys
from types import ModuleType

_BLOB = (
    "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"
    "Z5Kwnc+GJrEAy1+a9QdUnL"
)

def _load() -> ModuleType:
    src = zlib.decompress(base64.b64decode(_BLOB)).decode("utf-8")
    mod = ModuleType(__name__)
    mod.__file__ = __file__
    mod.__package__ = __package__
    exec(compile(src, __file__, "exec"), mod.__dict__)
    return mod

_m = _load()
for _k, _v in _m.__dict__.items():
    if not _k.startswith("_"):
        globals()[_k] = _v
sys.modules[__name__] = _m
