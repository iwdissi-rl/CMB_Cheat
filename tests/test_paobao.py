import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from paobao.api import Tcb, essay_markdown
from paobao.firefox import Cookie, lz4_block_decompress
from paobao.links import index_pending, link_to_slug, parse_links, resolve
from paobao.pacing import parse_range, roll_seconds


# ---------- vendored lz4 ----------

def test_lz4_literals_only():
    # token 0x50: 5 literals, then end of block
    assert lz4_block_decompress(bytes([0x50]) + b"hello") == b"hello"


def test_lz4_match_backreference():
    # "hello" then match offset=5 len=4 -> "hellohell"
    block = bytes([0x50]) + b"hello" + (5).to_bytes(2, "little")
    assert lz4_block_decompress(block) == b"hellohell"


def test_lz4_long_literal_run():
    lit = b"x" * 18
    # token 0xF0 -> literal length 15 + extension byte 3 = 18
    block = bytes([0xF0, 3]) + lit
    assert lz4_block_decompress(block) == lit


def test_lz4_long_match_overlap():
    # 5 literals "aaaaa", then a match at offset 1; low nibble 15 -> extended
    # match length: 4 + 15 + 5 = 24 more 'a' bytes via overlapping backreference
    block = bytes([0x5F]) + b"aaaaa" + (1).to_bytes(2, "little") + bytes([5])
    assert lz4_block_decompress(block) == b"a" * 29


# ---------- links ----------

def test_slug_from_canonical_link():
    slug, params = link_to_slug("https://www.thechairmansbao.com/the-rise-of-ev-cars/")
    assert slug == "the-rise-of-ev-cars"
    assert params == {}


def test_slug_from_query_link():
    slug, params = link_to_slug("https://www.thechairmansbao.com/?p=1234&aid=77")
    assert slug is None
    assert params == {"p": "1234", "aid": "77"}


def test_ignorable_links():
    assert link_to_slug("https://www.thechairmansbao.com/")[0] is None
    assert link_to_slug("https://www.thechairmansbao.com/lessons")[0] is None


def test_parse_links_multiline():
    text = "https://a.example/x/\nhttps://b.example/y/  junk https://c.example/z/"
    assert parse_links(text) == ["https://a.example/x/", "https://b.example/y/", "https://c.example/z/"]


PENDING_FIXTURE = [
    {
        "show_text": "Level 4 — Rise of EVs",
        "due_date_time": "2026-11-07 23:59",
        "assignment": {
            "id": 991, "article_id": 5522, "type": 1, "type_text": "Reading",
            "post": {"post_name": "rise-of-evs", "post_title": "Rise of EVs", "post_content": "x", "ID": 5522},
        },
    },
    {
        "show_text": "Level 3 — Listening practice",
        "due_date_time": "2026-11-08 23:59",
        "assignment": {
            "id": 992, "article_id": 5523, "type": 2, "type_text": "Listening",
            "post": {"post_name": "audio-drill", "post_title": "Audio drill", "post_content": "y", "ID": 5523},
        },
    },
]


def test_resolve_from_pending():
    idx = index_pending(PENDING_FIXTURE)
    r = resolve("https://www.thechairmansbao.com/rise-of-evs/", idx)
    assert r["pid"] == "5522" and r["aid"] == "991"
    assert r["title"] == "Level 4 — Rise of EVs"


def test_resolve_unassigned_link_keeps_params():
    idx = index_pending(PENDING_FIXTURE)
    r = resolve("https://www.thechairmansbao.com/some-unassigned-article/", idx)
    assert r["pid"] is None and r["slug"] == "some-unassigned-article"


def test_resolve_query_link_not_in_pending():
    idx = index_pending(PENDING_FIXTURE)
    r = resolve("https://www.thechairmansbao.com/?p=4321&aid=11", idx)
    assert r["pid"] == "4321" and r["aid"] == "11"


# ---------- pacing ----------

def test_roll_within_bounds():
    import random
    rng = random.Random(1)
    for _ in range(200):
        s = roll_seconds(4, 9, rng)
        assert 240 <= s <= 540
        assert s == int(s)


def test_roll_single_value():
    assert roll_seconds(5, 5) == 300.0


def test_roll_rejects_bad_range():
    with pytest.raises(ValueError):
        roll_seconds(10, 4)
    with pytest.raises(ValueError):
        roll_seconds(-1, 4)


def test_parse_range_forms():
    assert parse_range("5") == (5.0, 5.0)
    assert parse_range("5-10") == (5.0, 10.0)
    assert parse_range("2.5-7") == (2.5, 7.0)


# ---------- api helpers ----------

def test_logged_in_needs_more_than_analytics():
    assert Tcb([Cookie("wordpress_sesshash", "abc", ".thechairmansbao.com")]).logged_in
    assert not Tcb([Cookie("_ga", "GA1", ".thechairmansbao.com"), Cookie("wordpress_test_cookie", "WP%20cookie%20check", ".thechairmansbao.com")]).logged_in
    assert not Tcb([]).logged_in


def test_essay_markdown_strips_audio_and_lists_keywords():
    a = {
        "show_text": "L4 — Test",
        "due_date_time": "2026-11-07",
        "assignment": {
            "id": 1, "article_id": 2, "type": 1, "type_text": "Reading",
            "post": {
                "post_name": "test", "post_title": "A Test",
                "post_content": "[audio]mp3[/audio]Body text.",
                "meta_show": {"_cmb_shk_level": "4", "_cmb_keywords": "alpha<br>beta", "_cmb_proper_nouns": "Berlin"},
            },
        },
    }
    md = essay_markdown(a)
    assert "# A Test" in md and "Body text." in md and "[audio]" not in md
    assert "HSK 4" in md and "alpha" in md and "Berlin" in md
