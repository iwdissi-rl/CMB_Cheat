"""Headless-Firefox quiz engine for The Chairman's Bao reading quizzes.

A tiny WebDriver client (urllib only — no selenium) that drives the site's
own Vue quiz app, like the original tcb-assignments skill:

1. open the passage with ``?p=<pid>&aid=<aid>``,
2. click through to Reading Exercises,
3. read the md5-hashed answer key out of the Vue component tree,
4. fill in the correct options and call the app's own ``R.next()``,
5. let the site score and submit normally.

Requires: a Firefox binary and ``geckodriver`` on PATH. Everything else is
stdlib, so the module imports cleanly anywhere; only ``QuizRunner`` touches
the browser, and the CLI only builds one when a quiz must actually be taken.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request

md5 = lambda s: hashlib.md5(s.encode()).hexdigest()

WALK_JS = (
    "window.__walk=function(){let out=[];function w(v){if(!v)return;"
    "if(v.component){out.push(v.component);w(v.component.subTree)}"
    "if(Array.isArray(v.children))v.children.forEach(x=>{if(x&&typeof x=='object')w(x)});"
    "if(v.dynamicChildren)v.dynamicChildren.forEach(w);"
    "if(v.suspense)w(v.suspense.activeBranch)}"
    "w(document.querySelector('#app')._vnode);return [...new Set(out)]};"
)


class Unknown(Exception):
    """A quiz shape the solver doesn't know how to answer."""


class WD:
    """Minimal WebDriver client over urllib."""

    def __init__(self, binary: str | None = None, headless: bool = True):
        sk = socket.socket()
        sk.bind(("127.0.0.1", 0))
        self.port = sk.getsockname()[1]
        sk.close()
        devnull = subprocess.DEVNULL
        self.proc = subprocess.Popen(["geckodriver", "--port", str(self.port)], stdout=devnull, stderr=devnull)
        self.B = f"http://127.0.0.1:{self.port}"
        for _ in range(50):
            try:
                urllib.request.urlopen(self.B + "/status", timeout=1)
                break
            except (urllib.error.URLError, OSError):
                time.sleep(0.2)
        else:
            raise RuntimeError("geckodriver did not come up (is it installed?)")
        caps = {
            "alwaysMatch": {
                "browserName": "firefox",
                "moz:firefoxOptions": {
                    "args": (["-headless"] if headless else []) + (["--binary", binary] if binary else []),
                    "prefs": {"media.autoplay.default": 5},
                },
            }
        }
        r = self._req("POST", "/session", {"capabilities": caps})
        self.sid = r["value"]["sessionId"]

    def _req(self, method: str, path: str, body=None):
        url = f"{self.B}/session/{self.sid}{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            return json.loads(e.read().decode())

    def go(self, url):
        return self._req("POST", "/url", {"url": url})

    def js(self, code, *args):
        return self._req("POST", "/execute/sync", {"script": code, "args": list(args)})["value"]

    def J(self, code, *args):
        r = self.js(WALK_JS + "let cs=__walk(),R=cs[0].proxy,C=cs[cs.length-1],P=C.proxy;" + code, *args)
        if isinstance(r, dict) and "error" in r:
            raise RuntimeError(r.get("message"))
        return r

    def set_cookie(self, c):
        return self._req("POST", "/cookie", {"cookie": {"name": c.name, "value": c.value, "domain": c.domain, "path": c.path, "secure": c.secure}})

    def shot(self, fn):
        with urllib.request.urlopen(f"{self.B}/session/{self.sid}/screenshot", timeout=60) as r:
            open(fn, "wb").write(base64.b64decode(json.loads(r.read().decode())["value"]))

    def close(self):
        try:
            self._req("DELETE", "")
        except Exception:
            pass
        self.proc.terminate()


def quiz_state(w: WD) -> dict:
    return json.loads(w.J("return JSON.stringify({active:R.active,code:R.quiz[R.active]&&R.quiz[R.active].code,data:C.data,quiz:C.props.quiz,methods:Object.keys(C.type.methods||{})})"))


def solve(w: WD, st: dict) -> None:
    """Fill the current quiz part with the correct answers (md5-matched)."""
    q = st["quiz"]
    if isinstance(q, dict) and isinstance(q.get("q"), dict) and "simp" in q["q"]:  # keyword match
        Q = q["q"]
        for c in q["ca"]:
            s = next(k for k, v in enumerate(Q["simp"]) if md5(v) == c["s"])
            p = next(k for k, v in enumerate(Q["pin"]) if md5(v) == c["p"])
            e = next(k for k, v in enumerate(Q["eng"]) if md5(v) == c["e"])
            w.J("P.setAnswer('s',arguments[0],arguments[1]);P.setAnswer('p',arguments[2],arguments[3]);P.setAnswer('e',arguments[4],arguments[5])",
                s, Q["simp"][s], p, Q["pin"][p], e, Q["eng"][e])
    elif isinstance(q, dict) and isinstance(q.get("ca"), dict) and "c" in q["ca"]:  # true / false
        for i, x in enumerate(q["q"]):
            h = md5(x["text"])
            if h not in q["ca"]["c"] and h not in q["ca"]["w"]:
                raise Unknown("TF text hash mismatch")
            w.J("P.quiz.q[arguments[0]].ua=arguments[1]", i, h in q["ca"]["c"])
    elif isinstance(q, dict) and isinstance(q.get("q"), list) and q["q"] and isinstance(q["q"][0], list):  # fill blanks
        opts = q.get("a") or []
        for i, row in enumerate(q["q"]):
            for j, part in enumerate(row):
                if part.get("type") != "f":
                    continue
                a = next((x for x in opts if md5(x) == part["ca"]), None)
                if a is None:
                    raise Unknown("blank answer not in options")
                if "selectAnswer" in st["methods"] and "setAnswer" in st["methods"]:
                    w.J("P.selectAnswer(arguments[2]);P.setAnswer(arguments[0],arguments[1])", i, j, a)
                else:
                    w.J("P.quiz.q[arguments[0]][arguments[1]].ua=arguments[2]", i, j, a)
    elif isinstance(q, list) and q and isinstance(q[0].get("ca"), list):  # multi-select
        for i, x in enumerate(q):
            good = [o for o in x["a"] if md5(o) in x["ca"]]
            if len(good) != len(x["ca"]):
                raise Unknown("multi-select mismatch")
            w.J("P.answers[arguments[0]]=arguments[1]", i, good)
    elif isinstance(q, list) and q and "a" in q[0] and "ca" in q[0]:  # single choice
        for i, x in enumerate(q):
            a = next((o for o in x["a"] if md5(o) == x["ca"]), None)
            if a is None:
                raise Unknown("single choice answer not found")
            w.J("P.quiz[arguments[0]].ua=arguments[1]", i, a)
    else:
        raise Unknown("unrecognised quiz structure")


def run_quiz(w: WD, site: str, pid, aid, debug_dir: str, sleep=time.sleep) -> dict:
    """Take one reading quiz end to end; returns {part_code: score}."""
    w.go(f"{site}/?p={pid}&aid={aid}")
    sleep(5)
    w.js("let c=[...document.querySelectorAll('a,button')].filter(x=>x.offsetParent&&x.innerText&&x.innerText.trim()=='Reading Exercises');"
         "if(c.length)c[c.length-1].click();else location.href='/quiz/?type=reading&id='+arguments[0]+'&aid='+arguments[1]+'&lh=1'", pid, aid)
    for _ in range(40):
        sleep(1)
        try:
            if w.J("return Array.isArray(R.quiz)&&R.quiz.length>0&&!!(C.props&&C.props.quiz)") is True:
                break
        except Exception:
            pass
    else:
        raise Unknown("quiz never loaded")
    sleep(2)
    w.js("let s=document.querySelector('.skip-audio');if(s)s.click()")
    for _ in range(25):
        if w.J("return R.is_done") is True:
            break
        st = quiz_state(w)
        try:
            solve(w, st)
        except (Unknown, StopIteration) as e:
            dump = os.path.join(debug_dir, f"unknown_{pid}_{st['code']}.json")
            json.dump({"state": st, "check_src": w.J("return C.type.methods.check?C.type.methods.check.toString():''")},
                      open(dump, "w"), ensure_ascii=False, indent=1)
            try:
                w.shot(dump.replace(".json", ".png"))
            except Exception:
                pass
            raise Unknown(f"{st['code']}: {e} (dump: {dump})")
        sleep(0.5)
        w.J("R.next()")
        sleep(3)
    else:
        raise Unknown("quiz did not finish")
    sleep(4)
    return json.loads(w.J("return JSON.stringify(Object.fromEntries(Object.entries(R.scores).map(([k,v])=>[k,v.s])))"))


def make_runner(cookies, binary: str | None = None):
    """Start a headless Firefox and seed it with the cookies. Caller closes it."""
    w = WD(binary=binary)
    try:
        w.go("https://www.thechairmansbao.com/robots.txt")
        for c in cookies:
            w.set_cookie(c)
    except Exception:
        w.close()
        raise
    return w


def default_firefox_binary() -> str | None:
    """Locate the Firefox binary (snap build on this machine, else on PATH)."""
    for cand in ("/snap/firefox/current/usr/lib/firefox/firefox",):
        if os.path.exists(cand):
            return cand
    return None  # let geckodriver find it on PATH


def new_debug_dir() -> str:
    return tempfile.mkdtemp(prefix="paobao_")
