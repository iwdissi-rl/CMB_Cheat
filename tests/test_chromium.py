import base64
import hashlib
import json
import os
import sqlite3
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from paobao.chromium import (_unpad, aes256_cbc_decrypt, default_profile,
                             find_config_root, site_cookies)
from paobao.firefox import Cookie


# ---------- vendored AES-256-CBC: known-answer vectors ----------
# Reference output produced with the openssl CLI, e.g.
#   printf "The Chairman's Bao cookie payload 123" \
#     | /usr/bin/openssl enc -aes-256-cbc -K $K1 -iv $IV1
# (openssl applies PKCS7 padding; we unpad after decrypting).

_K1 = bytes.fromhex("000102030405060708090a0b0c0d0e0f"
                    "101112131415161718191a1b1c1d1e1f")
_IV1 = bytes.fromhex("00112233445566778899aabbccddeeff")
_CT_TEXT = bytes.fromhex("fc00db555a2f4803b26580ffe2bc4a7d"
                         "237399f9b2c115c7d201aacc4554c98a"
                         "b1a458880344376a0c2070e84b1721e9")
# exactly one full block -> openssl appends a whole block of 0x10 padding
_CT_BLOCK = bytes.fromhex("f7d51463d592cb3fce5d71460cac1cb7"
                          "4986673da794255c53cd8afbf6733cf1")


def test_aes256_cbc_known_answer_text():
    out = aes256_cbc_decrypt(_K1, _IV1, _CT_TEXT)
    assert _unpad(out) == b"The Chairman's Bao cookie payload 123"


def test_aes256_cbc_known_answer_full_block():
    out = aes256_cbc_decrypt(_K1, _IV1, _CT_BLOCK)
    assert _unpad(out) == b"xxxxxxxxxxxxxxxx"


def test_aes256_cbc_rejects_bad_length():
    with pytest.raises(ValueError):
        aes256_cbc_decrypt(_K1, _IV1, b"short")


def test_unpad_leaves_non_pkcs7_alone():
    from paobao.chromium import _unpad
    assert _unpad(b"abc\x05\x05\x05\x05\x05") == b"abc"
    assert _unpad(b"abc") == b"abc"          # last byte 0x63: not valid pad
    assert _unpad(b"cookie\x00\x00") == b"cookie"  # Chromium NULs
    assert _unpad(b"") == b""


# ---------- synthetic Chromium profile fixtures ----------

_TEST_KEY = b"unit-test-passphrase"  # DPAPI payload after the "DPAPI" tag
_TEST_KDF = hashlib.pbkdf2_hmac("sha1", _TEST_KEY, b"saltysalt", 1, 32)
_PEANUTS_KDF = hashlib.pbkdf2_hmac("sha1", b"peanuts", b"saltysalt", 1, 32)
_ZERO_IV = b"\x00" * 16


def _encrypt_with(openssl_key_hex: str, plaintext: bytes) -> bytes:
    """AES-256-CBC encrypt with zero IV via the openssl CLI (fixtures only)."""
    out = subprocess.run(
        ["/usr/bin/openssl", "enc", "-aes-256-cbc",
         "-K", openssl_key_hex, "-iv", "00" * 16],
        input=plaintext, stdout=subprocess.PIPE, check=True).stdout
    return b"v10" + _ZERO_IV + out


def _make_profile(tmp_path, *, rows, local_state=True):
    """Build <root>/Default/{Local State is at <root>, Cookies} on the fly."""
    root = tmp_path / "config-root"
    prof = root / "Default"
    prof.mkdir(parents=True)
    if local_state:
        (root / "Local State").write_text(json.dumps(
            {"os_crypt": {"encrypted_key":
                          base64.b64encode(b"DPAPI" + _TEST_KEY).decode()}}))
    db = sqlite3.connect(prof / "Cookies")
    db.execute("CREATE TABLE cookies(host_key TEXT, name TEXT, value TEXT,"
               " path TEXT, encrypted_value BLOB, is_secure INTEGER)")
    db.executemany("INSERT INTO cookies VALUES (?,?,?,?,?,?)", rows)
    db.commit()
    db.close()
    return root, prof


def test_site_cookies_decrypts_dpapi_and_plaintext(tmp_path):
    rows = [
        ("thechairmansbao.com", "plain", "hi", "/", b"", 0),
        (".thechairmansbao.com", "dpapi", "", "/",
         _encrypt_with(_TEST_KDF.hex(), b"deadbeef"), 1),
    ]
    root, prof = _make_profile(tmp_path, rows=rows)
    cks = site_cookies(str(prof), config_root=str(root))
    got = {c.name: c for c in cks}
    assert got["plain"].value == "hi"
    assert got["dpapi"].value == "deadbeef"
    assert got["dpapi"].domain == ".thechairmansbao.com"  # leading-dot variant
    assert got["dpapi"].secure is True
    assert isinstance(cks[0], Cookie)


def test_site_cookies_falls_back_to_peanuts(tmp_path):
    """No Local State at all -> the peanuts-derived key must still work."""
    rows = [
        ("www.thechairmansbao.com", "legacy", "", "/",
         _encrypt_with(_PEANUTS_KDF.hex(), b"fallback-value"), 0),
    ]
    root, prof = _make_profile(tmp_path, rows=rows, local_state=False)
    cks = site_cookies(str(prof), config_root=str(root))
    assert cks[0].value == "fallback-value"


def test_site_cookies_unknown_key_yields_empty_value(tmp_path):
    """Wrong key -> junk plaintext -> value '' but the row is still returned."""
    rows = [
        ("thechairmansbao.com", "junk", "", "/",
         _encrypt_with(_TEST_KDF.hex(), b"wrongkey-value"), 0),
    ]
    root, prof = _make_profile(tmp_path, rows=rows, local_state=False)
    cks = site_cookies(str(prof), config_root=str(root))
    assert len(cks) == 1
    assert cks[0].value == ""
    assert cks[0].name == "junk"


def test_site_cookies_ignores_other_hosts(tmp_path):
    rows = [("example.com", "x", "1", "/", b"", 0)]
    root, prof = _make_profile(tmp_path, rows=rows)
    assert site_cookies(str(prof), config_root=str(root)) == []


def test_site_cookies_copies_wal_and_leaves_original(tmp_path):
    """A browser that holds the db open must not block (or be blocked by) us."""
    rows = [("thechairmansbao.com", "a", "1", "/", b"", 0)]
    root, prof = _make_profile(tmp_path, rows=rows)
    (prof / "Cookies-wal").write_bytes(b"")
    (prof / "Cookies-shm").write_bytes(b"")
    db = sqlite3.connect(prof / "Cookies")  # simulate the running browser
    try:
        cks = site_cookies(str(prof), config_root=str(root))
        assert [c.value for c in cks] == ["1"]
    finally:
        db.close()
    assert sorted(p.name for p in prof.iterdir()) == \
        ["Cookies", "Cookies-shm", "Cookies-wal"]


def test_site_cookies_missing_db(tmp_path):
    assert site_cookies(str(tmp_path / "nope"), config_root=str(tmp_path)) == []


# ---------- discovery ----------

def test_find_config_root_and_default_profile(tmp_path, monkeypatch):
    root = tmp_path / ".config" / "google-chrome"
    (root / "Default").mkdir(parents=True)
    (root / "Local State").write_text("{}")
    (root / "Default" / "Cookies").write_bytes(b"")
    monkeypatch.setenv("HOME", str(tmp_path))
    assert find_config_root("chrome") == str(root)
    assert default_profile("chrome") == str(root / "Default")
    assert find_config_root("brave") is None
    assert default_profile("brave") is None


def test_default_profile_prefers_named_profiles(tmp_path, monkeypatch):
    root = tmp_path / ".config" / "chromium"
    p1 = root / "Profile 1"
    p1.mkdir(parents=True)
    (p1 / "Cookies").write_bytes(b"")
    (root / "Local State").write_text("{}")
    monkeypatch.setenv("HOME", str(tmp_path))
    assert find_config_root("chromium") == str(root)
    assert default_profile("chromium") == str(p1)
