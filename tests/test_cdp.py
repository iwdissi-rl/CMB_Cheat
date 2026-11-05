import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cmb.cdp import CdpRunner, chromium_binary
from cmb.driver import WALK_JS
from cmb.firefox import Cookie

CHROME = chromium_binary()
needs_chrome = pytest.mark.skipif(CHROME is None, reason="no chromium binary on this box")

DATA_URL = ("data:text/html,<html><head><title>cdp-test-page</title></head>"
            "<body><p id='x'>hello</p></body></html>")


# ---------- unit (no browser) ----------

def test_module_imports_do_not_cycle():
    """driver must not import cdp; cdp imports WALK_JS from driver."""
    import cmb.driver as driver
    assert "cdp" not in sys.modules or not hasattr(driver, "cdp")
    src = open(driver.__file__).read()
    assert "import cdp" not in src.replace("CdpRunner", "")
    from cmb import cdp
    assert cdp.WALK_JS is driver.WALK_JS


def test_chromium_binary_returns_executable_or_none():
    b = chromium_binary()
    if b is not None:
        assert os.path.isfile(b) and os.access(b, os.X_OK)


def test_constructor_without_binary_fails_fast(monkeypatch):
    monkeypatch.setattr("cmb.cdp.chromium_binary", lambda: None)
    with pytest.raises(RuntimeError, match="[Nn]o Chromium"):
        CdpRunner()


# ---------- live smoke ----------

@pytest.fixture(scope="module")
def runner():
    r = CdpRunner(cookies=[Cookie("paobao_test", "yes", "thechairmansbao.com", "/")])
    yield r
    r.close()


@needs_chrome
class TestLive:
    def test_js_by_value_and_args(self, runner):
        assert runner.js("return 1+arguments[0]", 2) == 3
        assert runner.js("return 'ok'") == "ok"
        assert runner.js("return null") is None
        assert runner.js("return arguments[0]+arguments[1]", "a", "b") == "ab"
        assert runner.js("return [1,{a:2},true]") == [1, {"a": 2}, True]

    def test_js_exception_raises_runtime_error(self, runner):
        with pytest.raises(RuntimeError):
            runner.js("throw new Error('boom')")
        with pytest.raises(RuntimeError):
            runner.js("no_such_function_xyz()")

    def test_go_waits_for_load_and_title(self, runner):
        runner.go(DATA_URL)
        assert runner.js("return document.title") == "cdp-test-page"
        assert runner.js("return document.getElementById('x').textContent") == "hello"

    def test_set_cookie_visible(self, runner):
        """Cookie set pre-navigation on the https site is visible via Network.getCookies."""
        runner.set_cookie(Cookie("paobao_live", "abc123", "thechairmansbao.com", "/"))
        # document.cookie on a data: origin won't show it; check the browser jar
        cks = runner._rpc("Network.getCookies", {"urls": ["https://www.thechairmansbao.com"]})["cookies"]
        got = {c["name"]: c["value"] for c in cks}
        assert got.get("paobao_live") == "abc123"
        assert got.get("paobao_test") == "yes"
        # and on a real https navigation document.cookie sees it (skip if proxy blocks)
        try:
            runner.go("https://www.thechairmansbao.com/robots.txt")
            assert "paobao_live=abc123" in runner.js("return document.cookie")
        except RuntimeError:
            pass  # no network route to the site from this box

    def test_J_preamble_on_vueless_page(self, runner):
        """J wires cs/R/C/P from __walk(); needs a #app Vue tree — check error path here."""
        runner.go(DATA_URL)  # no #app → __walk throws → RuntimeError
        with pytest.raises(RuntimeError):
            runner.J("return R.active")

    def test_shot_writes_png(self, runner, tmp_path):
        runner.go(DATA_URL)
        fn = str(tmp_path / "shot.png")
        runner.shot(fn)
        data = open(fn, "rb").read()
        assert len(data) > 100
        assert data[:8] == b"\x89PNG\r\n\x1a\n"


@needs_chrome
def test_close_leaves_no_process(tmp_path):
    r = CdpRunner(binary=CHROME, headless=True)
    pid = r.proc.pid
    profile = r.profile
    r.close()
    assert r.proc.poll() is not None  # reaped, no zombie
    assert not os.path.exists(profile)
    # port is free again: a fresh runner on the same port starts cleanly
    r2 = CdpRunner(port=r.port, headless=True)
    try:
        assert r2.proc.pid != pid
        assert r2.js("return 6*7") == 42
    finally:
        r2.close()
