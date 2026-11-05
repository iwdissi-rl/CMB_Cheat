"""Find Chromium-family browser profiles (Chrome/Chromium/Brave/Edge) and
pull The Chairman's Bao cookies out of them.

All four share Chromium's on-disk layout: a config root holding ``Local
State`` plus one or more profile directories (``Default``, ``Profile 1``,
…), each with a sqlite ``Cookies`` table. While the browser runs it holds
that file open — we copy it (plus ``-wal``/``-shm``) before reading, same
as firefox.py does.

On Linux cookie values are AES-256-CBC encrypted: an ``encrypted_value``
begins ``v10``/``v11``, then a 16-byte IV, then the ciphertext. The key is
``Local State`` -> ``os_crypt.encrypted_key`` -> base64 -> strip ``DPAPI``
-> PBKDF2-HMAC-SHA1(key, b"saltysalt", 1, 32); the historical default
passphrase ``b"peanuts"`` is tried when that yields non-printable junk.

Rather than depend on a package (or shelling out to openssl) we vendor a
compact pure-Python AES-256-CBC *decrypt* (``aes256_cbc_decrypt``), tables
computed at import. No third-party imports anywhere in this package.
"""

from __future__ import annotations

import base64
import glob
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile

from .firefox import SITE_HOST, Cookie

# per-browser config-root candidates, first valid one wins
_CONFIG_DIRS = {
    "chrome": ["~/.config/google-chrome",
               "~/snap/chromium/common/chromium",
               "~/snap/chrome/.config/google-chrome"],
    "chromium": ["~/.config/chromium",
                 "~/snap/chromium/common/.config/chromium"],
    "brave": ["~/.config/BraveSoftware/Brave-Browser",
              "~/snap/brave/common/.config/BraveSoftware/Brave-Browser"],
    "edge": ["~/.config/microsoft-edge",
             "~/snap/edge/common/.config/microsoft-edge"],
}

_PREFIXES = (b"v10", b"v11")
_SALT = b"saltysalt"
_FALLBACK_PASSPHRASE = b"peanuts"


# ---------- vendored AES-256-CBC decryption ----------

def _build_tables() -> tuple[bytes, bytes, list[int], list[int], list[int], list[int]]:
    """S-box, inverse S-box and the InvMixColumns multiplier tables.

    Built from first principles (GF(2^8) arithmetic) so there is no
    512-byte literal to mistype; import cost is negligible.
    """
    def mul(a: int, b: int) -> int:
        p = 0
        for _ in range(8):
            if b & 1:
                p ^= a
            a <<= 1
            if a & 0x100:
                a ^= 0x11B
            b >>= 1
        return p

    def ginv(a: int) -> int:
        """a^254 in GF(2^8) — the multiplicative inverse (0 stays 0)."""
        if a == 0:
            return 0
        p2 = mul(a, a)  # a^2
        acc = p2
        for _ in range(6):  # acc = a^2 * a^4 * ... * a^128 = a^254
            p2 = mul(p2, p2)
            acc = mul(acc, p2)
        return acc

    def rotl(x: int, n: int) -> int:
        return ((x << n) | (x >> (8 - n))) & 0xFF

    sbox = [0] * 256
    for x in range(256):
        xi = ginv(x)
        sbox[x] = xi ^ rotl(xi, 1) ^ rotl(xi, 2) ^ rotl(xi, 3) ^ rotl(xi, 4) ^ 0x63
    inv_sbox = [0] * 256
    for x, s in enumerate(sbox):
        inv_sbox[s] = x
    return (bytes(sbox), bytes(inv_sbox),
            [mul(9, x) for x in range(256)], [mul(11, x) for x in range(256)],
            [mul(13, x) for x in range(256)], [mul(14, x) for x in range(256)])


_SBOX, _INV_SBOX, _M9, _M11, _M13, _M14 = _build_tables()

# InvShiftRows as an index permutation over the column-major block
# (index i = 4*col + row): out[i] = src[_INV_SHIFT_ROWS[i]].
_INV_SHIFT_ROWS = [4 * ((c - r) % 4) + r for c in range(4) for r in range(4)]

_RCON = (0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40)  # Rcon[1..7], all AES-256 needs


def _sub_word(w: int) -> int:
    return int.from_bytes(w.to_bytes(4, "big").translate(_SBOX), "big")


def _expand_key(key: bytes) -> list[bytes]:
    """AES-256 key schedule -> 15 round keys of 16 bytes each."""
    w = [int.from_bytes(key[i:i + 4], "big") for i in range(0, 32, 4)]  # Nk = 8
    for i in range(8, 60):  # 4 * (Nr + 1), Nr = 14
        t = w[i - 1]
        if i % 8 == 0:
            t = _sub_word(((t << 8) | (t >> 24)) & 0xFFFFFFFF) ^ (_RCON[i // 8 - 1] << 24)
        elif i % 8 == 4:
            t = _sub_word(t)
        w.append(w[i - 8] ^ t)
    return [b"".join(x.to_bytes(4, "big") for x in w[4 * r:4 * r + 4])
            for r in range(15)]


def _inv_mix_columns(s: bytes) -> bytes:
    out = bytearray(16)
    for c in range(4):
        a0, a1, a2, a3 = s[4 * c:4 * c + 4]
        out[4 * c] = _M14[a0] ^ _M11[a1] ^ _M13[a2] ^ _M9[a3]
        out[4 * c + 1] = _M9[a0] ^ _M14[a1] ^ _M11[a2] ^ _M13[a3]
        out[4 * c + 2] = _M13[a0] ^ _M9[a1] ^ _M14[a2] ^ _M11[a3]
        out[4 * c + 3] = _M11[a0] ^ _M13[a1] ^ _M9[a2] ^ _M14[a3]
    return bytes(out)


def _xor16(a: bytes, b: bytes) -> bytes:
    return bytes(x ^ y for x, y in zip(a, b))


def _decrypt_block(rk: list[bytes], blk: bytes) -> bytes:
    s = _xor16(blk, rk[14])
    for r in range(13, 0, -1):
        s = s.translate(_INV_SBOX)
        s = bytes(s[i] for i in _INV_SHIFT_ROWS)
        s = _xor16(s, rk[r])
        s = _inv_mix_columns(s)
    s = s.translate(_INV_SBOX)
    s = bytes(s[i] for i in _INV_SHIFT_ROWS)
    return _xor16(s, rk[0])


def aes256_cbc_decrypt(key: bytes, iv: bytes, data: bytes) -> bytes:
    """Raw AES-256-CBC decrypt; ``data`` must be a multiple of 16 bytes.

    Padding (PKCS7 or otherwise) is left on — callers strip it.
    """
    if len(key) != 32:
        raise ValueError("aes256: key must be 32 bytes")
    if len(data) % 16:
        raise ValueError("aes256-cbc: data must be a multiple of 16 bytes")
    rk = _expand_key(key)
    out = bytearray()
    prev = iv
    for i in range(0, len(data), 16):
        blk = _xor16(_decrypt_block(rk, data[i:i + 16]), prev)
        out += blk
        prev = data[i:i + 16]
    return bytes(out)


def _unpad(data: bytes) -> bytes:
    """Strip PKCS7 padding, then any trailing NULs Chromium leaves."""
    if not data:
        return data
    n = data[-1]
    if 1 <= n <= 16 and data[-n:] == bytes([n]) * n:
        data = data[:-n]
    return data.rstrip(b"\x00")


# ---------- key derivation ----------

def _pbkdf2(passphrase: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha1", passphrase, _SALT, 1, 32)


def _dpapi_key(config_root: str) -> bytes | None:
    """Raw key material from ``<config_root>/Local State``, or None."""
    path = os.path.join(config_root, "Local State")
    try:
        with open(path, encoding="utf-8") as fh:
            enc = json.load(fh)["os_crypt"]["encrypted_key"]
        raw = base64.b64decode(enc)
    except (OSError, ValueError, KeyError):  # binascii.Error subclasses ValueError
        return None
    if raw.startswith(b"DPAPI"):
        raw = raw[5:]
    return raw or None


# ---------- profile discovery ----------

def find_config_root(name: str) -> str | None:
    """Config root for chrome/chromium/brave/edge holding a cookie store.

    Valid means: contains a ``Local State`` file or a ``Default/Cookies``
    file. Snap layouts are checked alongside the plain ``~/.config`` one.
    """
    for candidate in _CONFIG_DIRS.get(name, []):
        root = os.path.expanduser(candidate)
        if os.path.isfile(os.path.join(root, "Local State")) or \
                os.path.isfile(os.path.join(root, "Default", "Cookies")):
            return root
    return None


def default_profile(name: str) -> str | None:
    """The profile directory for ``name`` that holds the ``Cookies`` db."""
    root = find_config_root(name)
    if root is None:
        return None
    default = os.path.join(root, "Default")
    if os.path.isfile(os.path.join(default, "Cookies")):
        return default
    for prof in sorted(glob.glob(os.path.join(root, "Profile *"))):
        if os.path.isfile(os.path.join(prof, "Cookies")):
            return prof
    return None


# ---------- cookie reading ----------

def _decrypt_value(encrypted: bytes, keys: list[bytes]) -> str:
    """Decode one v10/v11 encrypted_value; '' when no key yields text."""
    payload = encrypted[3:]
    if len(payload) < 32 or (len(payload) - 16) % 16:
        return ""
    iv, ct = payload[:16], payload[16:]
    for key in keys:
        raw = _unpad(aes256_cbc_decrypt(key, iv, ct))
        if raw and all(32 <= b < 127 for b in raw):
            return raw.decode("ascii")
    return ""


def site_cookies(profile: str, host: str = SITE_HOST,
                 config_root: str | None = None) -> list[Cookie]:
    """Chromium-family cookies for ``host`` in ``profile`` (decrypted).

    The db is copied to a temp dir first — the browser may be running and
    we never want to lock (or block on) the original. ``config_root``
    overrides the ``Local State`` lookup (tests); by default the profile's
    parent ("<root>/Default" -> "<root>") is used.
    """
    src = os.path.join(profile, "Cookies")
    if not os.path.isfile(src):
        return []
    if config_root is None:
        config_root = os.path.dirname(os.path.abspath(profile))
    tmp = tempfile.mkdtemp(prefix="paobao_chromium_")
    try:
        for suffix in ("", "-wal", "-shm"):
            part = src + suffix
            if os.path.exists(part):
                shutil.copy(part, tmp)
        db = sqlite3.connect(os.path.join(tmp, "Cookies"))
        try:
            rows = db.execute(
                "select host_key, name, value, path, is_secure, encrypted_value"
                " from cookies where host_key like ?",
                (f"%{host}%",),
            ).fetchall()
        finally:
            db.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    keys: list[bytes] = []
    dpapi = _dpapi_key(config_root)
    if dpapi is not None:
        keys.append(_pbkdf2(dpapi))
    keys.append(_pbkdf2(_FALLBACK_PASSPHRASE))

    out: list[Cookie] = []
    for host_key, name, value, path, is_secure, enc in rows:
        if not value and enc and bytes(enc[:3]) in _PREFIXES:
            value = _decrypt_value(bytes(enc), keys)
        out.append(Cookie(name, value or "", host_key, path or "/", bool(is_secure)))
    return out
