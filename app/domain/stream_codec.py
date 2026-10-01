import gzip
import json
from itertools import pairwise
from typing import Any


def encode_stream(channels: dict[str, list[Any]]) -> bytes:
    if len({len(v) for v in channels.values()}) > 1:
        raise ValueError(f"channel length mismatch: { {k: len(v) for k, v in channels.items()} }")
    t = channels.get("time", [])
    if any(b < a for a, b in pairwise(t)):
        raise ValueError("time must be non-decreasing")
    raw = json.dumps(channels, separators=(",", ":")).encode()
    return gzip.compress(raw, compresslevel=6, mtime=0)


def decode_stream(blob: bytes) -> dict[str, list[Any]]:
    return json.loads(gzip.decompress(blob))  # type: ignore[no-any-return]
