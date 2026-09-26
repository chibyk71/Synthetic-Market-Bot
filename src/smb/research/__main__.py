"""CLI: python -m smb.research."""
from __future__ import annotations
import base64, sys, zlib
from pathlib import Path
from types import ModuleType

def _load() -> ModuleType:
    parts = []
    parts.append(Path(__file__).with_name("__main___data_0.b64").read_text().strip())
    parts.append(Path(__file__).with_name("__main___data_1.b64").read_text().strip())
    parts.append(Path(__file__).with_name("__main___data_2.b64").read_text().strip())
    parts.append(Path(__file__).with_name("__main___data_3.b64").read_text().strip())
    parts.append(Path(__file__).with_name("__main___data_4.b64").read_text().strip())
    parts.append(Path(__file__).with_name("__main___data_5.b64").read_text().strip())
    blob = "".join(parts)
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
