"""Auto-generated zlib loader for src/smb/research/entry_edge_study.py."""
from __future__ import annotations
import base64, sys, zlib
from pathlib import Path
from types import ModuleType

_DATA = Path(__file__).with_name(Path(__file__).name.replace(".py", "_data.b64"))

def _load() -> ModuleType:
    blob = _DATA.read_text().strip()
    src = zlib.decompress(base64.b64decode(blob)).decode("utf-8")
    mod = ModuleType(__name__)
    mod.__file__ = __file__
    mod.__package__ = __package__
    exec(compile(src, __file__, "exec"), mod.__dict__)
    return mod

_m = _load()
for _k, _v in list(_m.__dict__.items()):
    if not _k.startswith("_"):
        globals()[_k] = _v
sys.modules[__name__] = _m
