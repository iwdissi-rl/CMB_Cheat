"""Find the user's Firefox profile and pull The Chairman's Bao cookies out of it.

Firefox keeps cookies in two places: the on-disk ``cookies.sqlite`` (kept
current for the session cookie too on the snap build) and, for non-session
cookies, ``sessionstore-backups/recovery.jsonlz4``. We read both and merge,
because Firefox holds both files open while it runs — we copy them first.

The jsonlz4 payload is LZ4 *block* data with an 8-byte magic header. Rather
than depend on a package we cannot assume the user has installed, we vendor a
~40-line pure-Python LZ4 block decompressor (``lz4_block_decompress``).

No third-party imports anywhere in this package.
"""

from __future__ import annotations

import glob
import json
import os
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass

SITE_HOST = "thechairmansbao.com"

_PROFILE_GLOBS = [
    "~/snap/firefox/common/.mozilla/firefox",
    "~/.mozilla/firefox",
    "~/snap/firefox/common/.mozilla/firefox",  # snap again: first match wins
]


@dataclass
class Cookie:
    name: str
    value: str
    domain: str
    path: str = "/"
    secure: bool = False

    def header_pair(self) -> str:
        return f"{self.name}={self.value}"


def candidate_profiles() -> list[str]:
    """Directories that contain a Firefox sessionstore backup, newest first."""
    found: list[str] = []
    seen: set[str] = set()
    for base in _PROFILE_GLOBS:
        pat = os.path.join(os.path.expanduser(base), "*", "sessionstore-backups", "recovery.jsonlz4")
        for hit in sorted(glob.glob(pat), key=os.path.getmtime, reverse=True):
            prof = os.path.dirname(os.path.dirname(hit))
            if prof not in seen:
                seen.add(prof)
                found.append(prof)
    return found


# ---------- vendored LZ4 block decompression ----------

def _read_varlen(src: bytes, pos: int) -> tuple[int, int]:
    """LZ4 run-length extension: sum 255-bytes until a byte < 255."""
    total = 0
    while True:
        b = src[pos]
        pos += 1
        total += b
        if b != 255:
            return total, pos


def lz4_block_decompress(src: bytes) -> bytes:
    """Decode a raw LZ4 block. Pure stdlib; enough for Firefox sessionstores.

    Token layout: high nibble = literal length, low nibble = match length-4.
    A nibble of 15 means "keep reading 255-extended bytes". Matches copy
    byte-by-byte so overlapping back-references work.
    """
    out = bytearray()
    pos = 0
    n = len(src)
    while pos < n:
        token = src[pos]
        pos += 1
        lit = token >> 4
        if lit == 15:
            extra, pos = _read_varlen(src, pos)
            lit += extra
        out += src[pos:pos + lit]
        pos += lit
        if pos >= n:
            break  # stream ends with a literal run
        offset = int.from_bytes(src[pos:pos + 2], "little")
        pos += 2
        mlen = token & 0x0F
        if mlen == 15:
            extra, pos = _read_varlen(src, pos)
            mlen += extra
        mlen += 4
        if offset > len(out):
            raise ValueError("lz4: match distance beyond start of output")
        start = len(out) - offset
        for i in range(mlen):
            out.append(out[start + i])
    return bytes(out)


def decompress_jsonlz4(path: str) -> dict:
    """Read a Firefox sessionstore jsonlz4 file and return the parsed JSON.

    Layout: ``mozLz40\\0`` magic (8 bytes) + 32-bit LE uncompressed size +
    raw LZ4 block.
    """
    raw = open(path, "rb").read()
    if raw[:8] != b"mozLz40\x00":
        raise ValueError(f"{path}: not a jsonlz4 file")
    body = lz4_block_decompress(raw[12:])
    return json.loads(body)


def _cookies_from_sessionstore(profile: str) -> list[Cookie]:
    ss = os.path.join(profile, "sessionstore-backups", "recovery.jsonlz4")
    if not os.path.exists(ss):
        return []
    try:
        data = decompress_jsonlz4(ss)
    except Exception:
        return []
    out = []
    for c in data.get("cookies", []):
        if SITE_HOST in c.get("host", ""):
            out.append(Cookie(c["name"], c["value"], c["host"], c.get("path", "/"), bool(c.get("secure"))))
    return out


def _cookies_from_sqlite(profile: str) -> list[Cookie]:
    src = os.path.join(profile, "cookies.sqlite")
    if not os.path.exists(src):
        return []
    tmp = tempfile.mkdtemp(prefix="paobao_ck_")
    try:
        copied = shutil.copy(src, tmp)
        db = sqlite3.connect(copied)
        try:
            rows = db.execute(
                "select host, name, value, path, isSecure from moz_cookies where host like ?",
                (f"%{SITE_HOST}%",),
            ).fetchall()
        finally:
            db.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return [Cookie(n, v, h, p, bool(sec)) for (h, n, v, p, sec) in rows]


def site_cookies(profile: str | None = None) -> list[Cookie]:
    """Merge sqlite + sessionstore cookies for thechairmansbao.com.

    Sessionstore wins on conflicts (it is fresher while Firefox runs).
    """
    profiles = [profile] if profile else candidate_profiles()
    merged: dict[tuple[str, str, str], Cookie] = {}
    for prof in profiles:
        for c in _cookies_from_sqlite(prof):
            merged[(c.domain, c.name, c.path)] = c
        for c in _cookies_from_sessionstore(prof):
            merged[(c.domain, c.name, c.path)] = c
    return sorted(merged.values(), key=lambda c: (c.domain, c.path, c.name))
