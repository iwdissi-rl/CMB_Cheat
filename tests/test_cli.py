"""End-to-end CLI tests with the network and browser mocked out."""

import io
import os
import sys
import contextlib
from unittest import mock
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from paobao import cli
from paobao.api import Tcb

PENDING = [
    {
        "show_text": "Level 4 — Rise of EVs",
        "due_date_time": "2026-11-07 23:59",
        "assignment": {
            "id": 991, "article_id": 5522, "type": 1, "type_text": "Reading",
            "post": {"post_name": "rise-of-evs", "post_title": "Rise of EVs", "post_content": "body", "ID": 5522, "meta_show": {}},
        },
    },
]

LINK = "https://www.thechairmansbao.com/rise-of-evs/"


def run_cli(argv, pending=PENDING, scores=None):
    calls = {"quiz": []}

    def fake_run_quiz(w, site, pid, aid, debug_dir, sleep=lambda s: None):
        calls["quiz"].append((pid, aid))
        return scores or {"_comprahension_CTS": 100}

    with mock.patch("paobao.browsers.detect", return_value=("firefox", [])), \
         mock.patch.object(Tcb, "logged_in", property(lambda self: True)), \
         mock.patch.object(Tcb, "pending", return_value=pending), \
         mock.patch("paobao.browsers.make_runner", return_value=MagicMock()), \
         mock.patch("paobao.cli.run_quiz", side_effect=fake_run_quiz), \
         mock.patch("paobao.cli.time.sleep"):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = cli.main(argv)
    return code, buf.getvalue(), calls


def test_status_logged_in_lists_pending():
    code, out, _ = run_cli(["status"])
    assert code == 0
    assert "1 outstanding" in out and "Rise of EVs" in out


def test_run_dry_run_plans_without_browser():
    code, out, calls = run_cli(["run", LINK, "-n"])
    assert code == 0
    assert "1 link(s) to complete" in out and "Dry run" in out
    assert calls["quiz"] == []


def test_run_now_takes_the_quiz_with_resolved_ids():
    code, out, calls = run_cli(["run", LINK, "--now"])
    assert code == 0
    assert calls["quiz"] == [("5522", "991")]
    assert "DONE" in out and "CTS=100" in out


def test_run_bare_link_defaults_to_run():
    code, out, calls = run_cli([LINK, "--now"])
    assert code == 0 and calls["quiz"] == [("5522", "991")]


def test_run_pacing_range_is_respected(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "ESSAYS_DIR", str(tmp_path))
    slept = []
    code, out, calls = None, None, None

    def fake_run_quiz(w, site, pid, aid, debug_dir, sleep=lambda s: None):
        return {}

    with mock.patch("paobao.browsers.detect", return_value=("firefox", [])), \
         mock.patch.object(Tcb, "logged_in", property(lambda self: True)), \
         mock.patch.object(Tcb, "pending", return_value=PENDING), \
         mock.patch("paobao.browsers.make_runner", return_value=MagicMock()), \
         mock.patch("paobao.cli.run_quiz", side_effect=fake_run_quiz), \
         mock.patch("paobao.cli.time.sleep", side_effect=slept.append):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = cli.main(["run", LINK, "-r", "1-2"])
    assert code == 0
    assert len(slept) == 1 and 60 <= slept[0] <= 120


def test_run_unrecognised_link_is_skipped():
    code, out, calls = run_cli(["run", "https://www.thechairmansbao.com/brand-new-story/", "--now"])
    assert code == 0
    assert "SKIP" in out and calls["quiz"] == []


def test_save_writes_essays(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "ESSAYS_DIR", str(tmp_path))
    code, out, _ = run_cli(["save"])
    assert code == 0
    assert os.path.exists(tmp_path / "rise-of-evs.md")
