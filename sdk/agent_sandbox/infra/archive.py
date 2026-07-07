"""Fast, standard-``.zip`` archiving for guest images.

The AWS ``lambda-microvms`` service consumes the guest image as a Lambda-style
S3 code package, so the artifact must stay a plain ``.zip``. Speed therefore
comes from a faster DEFLATE backend (Intel ISA-L via ``python-isal``) plus a
lower default level and streaming to a spooled temp file, rather than from a
different container format.

``python-isal`` is optional: when it is unavailable (or missing constants
``zipfile`` relies on) the helper transparently falls back to stdlib ``zlib``,
producing an identical, standard zip.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
import zipfile
from collections.abc import Iterator
from typing import IO

# Spill to disk past this many bytes; small guest dirs stay entirely in memory.
_SPOOL_MAX_BYTES = 32 * 1024 * 1024

# ISA-L exposes compression levels 0..3; stdlib zlib uses 0..9. Level 2 is a
# good speed/ratio default for ISA-L; 6 mirrors zlib's historical default.
_ISAL_DEFAULT_LEVEL = 2
_ISAL_MAX_LEVEL = 3
_ZLIB_DEFAULT_LEVEL = 6
_ZLIB_MAX_LEVEL = 9

# Constants that ``zipfile`` reads off the ``zlib`` module; a drop-in must
# provide all of them for the monkeypatch to be safe.
_REQUIRED_ZLIB_ATTRS = (
    "compressobj",
    "decompressobj",
    "crc32",
    "DEFLATED",
    "DEF_MEM_LEVEL",
    "Z_DEFAULT_STRATEGY",
)


def _load_isal_zlib():
    """Return an ISA-L ``zlib`` drop-in if usable, else ``None``."""
    try:
        from isal import isal_zlib
    except ImportError:
        return None
    if all(hasattr(isal_zlib, attr) for attr in _REQUIRED_ZLIB_ATTRS):
        return isal_zlib
    return None


@contextlib.contextmanager
def _deflate_backend() -> Iterator[bool]:
    """Temporarily route ``zipfile``'s DEFLATE through ISA-L when available.

    Yields ``True`` if the ISA-L backend is active, ``False`` for stdlib zlib.
    The original ``zipfile.zlib`` is always restored.
    """
    isal_zlib = _load_isal_zlib()
    if isal_zlib is None:
        yield False
        return
    original = zipfile.zlib
    zipfile.zlib = isal_zlib
    try:
        yield True
    finally:
        zipfile.zlib = original


def _resolve_level(compresslevel: int | None, *, isal: bool) -> int:
    """Pick and clamp a compression level valid for the active backend."""
    max_level = _ISAL_MAX_LEVEL if isal else _ZLIB_MAX_LEVEL
    if compresslevel is None:
        return _ISAL_DEFAULT_LEVEL if isal else _ZLIB_DEFAULT_LEVEL
    return max(0, min(compresslevel, max_level))


def zip_dir(guest_dir: str, *, compresslevel: int | None = None) -> IO[bytes]:
    """Zip ``guest_dir`` into a rewound, standard ``.zip`` file object.

    Uses ISA-L accelerated DEFLATE when ``python-isal`` is installed, otherwise
    stdlib ``zlib``. The archive is written to a ``SpooledTemporaryFile`` so
    small directories stay in memory while large ones spill to disk, bounding
    peak memory. The returned object is seeked to 0 and ready to stream/upload;
    the caller owns closing it.
    """
    buf: IO[bytes] = tempfile.SpooledTemporaryFile(max_size=_SPOOL_MAX_BYTES, mode="w+b")
    try:
        with _deflate_backend() as isal_active:
            level = _resolve_level(compresslevel, isal=isal_active)
            with zipfile.ZipFile(
                buf, "w", zipfile.ZIP_DEFLATED, compresslevel=level
            ) as zf:
                for root, _dirs, files in os.walk(guest_dir):
                    for fname in files:
                        full = os.path.join(root, fname)
                        zf.write(full, os.path.relpath(full, guest_dir))
    except BaseException:
        buf.close()
        raise
    buf.seek(0)
    return buf
