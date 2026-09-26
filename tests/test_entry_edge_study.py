"""Tests for Milestone 6C — Entry Edge / Early Excursion Study. Body zlib-transported."""
from __future__ import annotations
import base64, zlib, sys
from pathlib import Path
_here = Path(__file__).resolve().parent
if str(_here) not in sys.path: sys.path.insert(0, str(_here))
_CHUNKS = []
from _ee_test_chunk_0 import CHUNK as _C0
_CHUNKS.append(_C0)
from _ee_test_chunk_1 import CHUNK as _C1
_CHUNKS.append(_C1)
from _ee_test_chunk_2 import CHUNK as _C2
_CHUNKS.append(_C2)
exec(zlib.decompress(base64.b64decode("".join(_CHUNKS))).decode("utf-8"), globals())
