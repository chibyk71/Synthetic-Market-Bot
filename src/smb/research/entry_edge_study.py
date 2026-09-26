"""Milestone 6C — Entry Edge / Early Excursion Study.

Research-only observational diagnostic of the frozen 6B entry cohort.
Body is zlib-compressed across transport chunks and expanded at import.
"""
from __future__ import annotations

import base64
import zlib

_CHUNKS = []
from smb.research._ee_chunk_0 import CHUNK as _C0
_CHUNKS.append(_C0)
from smb.research._ee_chunk_1 import CHUNK as _C1
_CHUNKS.append(_C1)
from smb.research._ee_chunk_2 import CHUNK as _C2
_CHUNKS.append(_C2)
from smb.research._ee_chunk_3 import CHUNK as _C3
_CHUNKS.append(_C3)
from smb.research._ee_chunk_4 import CHUNK as _C4
_CHUNKS.append(_C4)
from smb.research._ee_chunk_5 import CHUNK as _C5
_CHUNKS.append(_C5)
from smb.research._ee_chunk_6 import CHUNK as _C6
_CHUNKS.append(_C6)

exec(zlib.decompress(base64.b64decode("".join(_CHUNKS))).decode("utf-8"), globals())
