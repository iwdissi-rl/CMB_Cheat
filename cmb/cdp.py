"""Headless-Chromium quiz runner over the Chrome DevTools Protocol.

``CdpRunner`` is the duck-type twin of ``driver.WD``: same ``go / js / J /
set_cookie / shot / close`` surface, so ``driver.run_quiz`` and ``driver.solve``
drive it unchanged. Instead of geckodriver it launches a Chromium-family
browser with ``--remote-debugging-port`` and speaks JSON-RPC over the
browser-level WebSocket from ``cmb.ws`` — stdlib only, no selenium.

The page domain is reached through a flattened ``Target.attachToTarget``
session; ``js`` wraps the code in an IIFE with the Python args JSON-inlined so
``arguments[0..]`` works exactly like WebDriver's ``execute/sync``.
"""

from __future__ import annotations

import base64
import glob
import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from collections import deque

from .driver import WALK_JS  # noqa: F401 — driver must never import cdp
from .ws import WS

SITE_URL = "https://www.thechairmansbao.com"

# urllib must not route the local DevTools endpoint through $HTTPS_PROXY
_no_proxy = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def chromium_binary() -> str | None:
    """First Chromium-family browser on PATH, else the newest Playwright download."""
    for name in ("google-chrome", "google-chrome-stable", "chromium",
                 "chromium-browser", "brave-browser", "microsoft-edge"):
        p = shutil.which(name)
        if p:
            return p
    found = []
    for pat in ("~/.cache/ms-playwright/chromium-*/chrome-linux64/chrome",
                "~/.cache/ms-playwright/chromium-*/chrome-linux/chrome"):
        found += [f for f in glob.glob(os.path.expanduser(pat)) if os.path.isfile(f)]
    if not found:
        return None
    return max(found, key=lambda f: (os.path.getmtime(f), f))


class CdpRunner:
    """Minimal CDP client over cmb.ws — duck type of driver.WD."""

    def __init__(self, cookies=(), binary: str | None = None,
                 port: int | None = None, headless: bool = True):
        self.binary = binary or chromium_binary()
        if not self.binary:
            raise RuntimeError(
                "no Chromium-family browser found — install Chrome/Chromium/Brave/Edge "
                "or run `playwright install chromium`")
        if port is None:
            sk = socket.socket()
            sk.bind(("127.0.0.1", 0))
            port = sk.getsockname()[1]
            sk.close()
        self.port = port
        # Chrome's SingletonSocket needs a short path (<108 chars); TMPDIR here
        # can be long, so prefer /tmp and fall back to TMPDIR's shortest form.
        tmp = "/tmp" if os.path.isdir("/tmp") and os.access("/tmp", os.W_OK | os.X_OK) else None
        self.profile = tempfile.mkdtemp(prefix="paobao_cdp_", dir=tmp)
        devnull = subprocess.DEVNULL
        # Chrome builds its SingletonSocket path under TMPDIR; a long TMPDIR
        # (e.g. the agent scratch dir) overruns the 108-char Unix-socket limit.
        env = {**os.environ, "TMPDIR": tmp or "/tmp"}

        def spawn(no_sandbox: bool) -> subprocess.Popen:
            args = [self.binary]
            if headless:
                args.append("--headless=new")
            args += [f"--remote-debugging-port={port}", f"--user-data-dir={self.profile}",
                     "--no-first-run", "--no-default-browser-check",
                     "--remote-allow-origins=*"]  # chrome >=111 rejects WS Origin otherwise
            if no_sandbox:
                args.append("--no-sandbox")
            args.append("about:blank")
            return subprocess.Popen(args, stdout=devnull, stderr=devnull, env=env)

        # --no-sandbox only when asked (env) or root; retried once if chrome
        # comes up and immediately dies (e.g. AppArmor-disabled userns).
        want_nosb = bool(os.environ.get("PAOBAO_NO_SANDBOX")) or os.geteuid() == 0
        self._id = 0
        self._events: deque[tuple[str, str | None, dict]] = deque()
        self.session: str | None = None
        self.ws = None
        self.proc = None
        try:
            for no_sandbox in ((True,) if want_nosb else (False, True)):
                self.proc = spawn(no_sandbox)
                try:
                    self.ws = WS(self._wait_devtools_url(), timeout=120)
                    break
                except RuntimeError:
                    if no_sandbox or self.proc.poll() is None:
                        raise  # real timeout, or already without sandbox
                    self.ws = None
            t = self._rpc("Target.createTarget", {"url": "about:blank"})
            self.target_id = t["targetId"]
            self.session = self._rpc(
                "Target.attachToTarget",
                {"targetId": self.target_id, "flatten": True})["sessionId"]
            self._rpc("Page.enable")
            for c in cookies:
                self.set_cookie(c)
        except Exception:
            self._cleanup()
            raise

    # ---------- plumbing ----------

    def _wait_devtools_url(self) -> str:
        """Poll /json/version for up to ~30 s; return the browser-level ws URL.

        A chrome that exits can be the zygote handing off to the real browser
        process, so a dead process only counts once it stays dead for a few
        polls (~2 s).
        """
        url = f"http://127.0.0.1:{self.port}/json/version"
        deadline = time.monotonic() + 30
        dead_polls = 0
        while time.monotonic() < deadline:
            try:
                with _no_proxy.open(url, timeout=1) as r:
                    return json.loads(r.read().decode())["webSocketDebuggerUrl"]
            except (urllib.error.URLError, OSError, KeyError):
                pass
            if self.proc.poll() is not None:
                dead_polls += 1
                if dead_polls >= 10:
                    raise RuntimeError(
                        f"chrome exited during startup (rc={self.proc.returncode}); "
                        "if the sandbox is unavailable, set PAOBAO_NO_SANDBOX=1")
            else:
                dead_polls = 0
            time.sleep(0.2)
        raise RuntimeError(
            f"chrome devtools did not come up on port {self.port} within 30 s")

    def _send(self, method: str, params: dict | None = None) -> int:
        """One JSON-RPC command; page commands carry sessionId at top level."""
        self._id += 1
        msg = {"id": self._id, "method": method}
        if params:
            msg["params"] = dict(params)
        if self.session:
            msg["sessionId"] = self.session
        self.ws.send_text(json.dumps(msg))
        return self._id

    def _recv_frame(self) -> dict:
        try:
            return json.loads(self.ws.recv_text())
        except TimeoutError as e:
            raise RuntimeError("chrome devtools: timeout reading from websocket") from e

    def _rpc(self, method: str, params: dict | None = None) -> dict:
        """Send a command and return its result dict, silently skipping events."""
        want = self._send(method, params)
        while True:
            frame = self._recv_frame()
            if frame.get("id") == want:
                break
            if frame.get("method"):
                self._events.append((frame["method"], frame.get("sessionId"), frame.get("params") or {}))
        if "error" in frame:
            err = frame["error"]
            raise RuntimeError(f"{method}: {err.get('message', err)}")
        return frame.get("result", {})

    # ---------- WD duck type ----------

    def _loads(self) -> int:
        """Page.loadEventFired count for our session stashed in the event queue."""
        return sum(1 for m, sid, _ in self._events
                   if m == "Page.loadEventFired" and sid == self.session)

    def go(self, url):
        """Navigate and block until the page's load event fires."""
        base = self._loads()  # events already queued belong to the old page
        self._rpc("Page.navigate", {"url": url})
        while self._loads() <= base:
            frame = self._recv_frame()
            if frame.get("method"):
                self._events.append((frame["method"], frame.get("sessionId"), frame.get("params") or {}))

    def js(self, code, *args):
        """Run a JS function body with ``arguments[0..]``; return value by value."""
        inlined = ",".join(json.dumps(a, ensure_ascii=True) for a in args)
        expr = f"(function(){{{code}}})({inlined})"
        r = self._rpc("Runtime.evaluate", {"expression": expr, "returnByValue": True})
        exc = r.get("exceptionDetails")
        if exc:
            desc = exc.get("exception", {}).get("description") or exc.get("text")
            raise RuntimeError(desc)
        return r.get("result", {}).get("value")

    def J(self, code, *args):
        r = self.js(WALK_JS + "let cs=__walk(),R=cs[0].proxy,C=cs[cs.length-1],P=C.proxy;" + code, *args)
        if isinstance(r, dict) and "error" in r:
            raise RuntimeError(r.get("message"))
        return r

    def set_cookie(self, c):
        domain = c.domain
        # Chromium stores a domain verbatim; a bare "example.com" then fails to
        # match "www.example.com". A leading dot means "and subdomains", which
        # is what the firefox.Cookie domains (read for the site) always mean.
        if not domain.startswith("."):
            domain = "." + domain
        return self._rpc("Network.setCookie", {
            "name": c.name, "value": c.value, "domain": domain,
            "path": c.path, "secure": c.secure, "url": SITE_URL})

    def shot(self, fn):
        r = self._rpc("Page.captureScreenshot", {"format": "png"})
        with open(fn, "wb") as f:
            f.write(base64.b64decode(r["data"]))

    # ---------- teardown ----------

    def _cleanup(self):
        if self.ws is not None:
            try:
                self.ws.close()
            except OSError:
                pass
        if self.proc.poll() is None:
            self.proc.terminate()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()
        # a terminating chrome keeps touching its profile dir briefly; retry
        # before giving up so close() really leaves nothing behind
        for i in range(10):
            shutil.rmtree(self.profile, ignore_errors=True)
            if not os.path.exists(self.profile):
                break
            time.sleep(0.2 * (i + 1))

    def close(self):
        self._cleanup()
