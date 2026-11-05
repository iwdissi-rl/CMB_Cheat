"""HTTP client for the Chairman's Bao API using only urllib."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request

from .firefox import Cookie

SITE = "https://www.thechairmansbao.com"
UA = "Mozilla/5.0 (X11; Linux x86_64; rv:140.0) Gecko/20100101 Firefox/140.0"

_AUDIO_TAG = re.compile(r"\[audio[^\]]*\].*?\[/audio\]", re.IGNORECASE | re.DOTALL)


class Tcb:
    """Small session wrapper: attaches the Firefox cookies to every request."""

    def __init__(self, cookies: list[Cookie], timeout: int = 30):
        self.cookies = cookies
        self.timeout = timeout

    @property
    def logged_in(self) -> bool:
        """True when the cookie jar holds something that looks like a login."""
        return bool(self._auth_cookies())

    def _auth_cookies(self) -> list[Cookie]:
        # PHP session cookies and the site's own tokens; analytics-only jars
        # (_ga, _fbp, ...) mean the login cookie is gone.
        names = {c.name.lower() for c in self.cookies}
        keep = {n for n in names if not n.startswith(("_ga", "_fbp", "_cl", "_gcl", "_pk", "_yt", "ga_", "wp_emoji"))}
        # wordpress comment/identity cookies alone are not a login either
        keep -= {"wordpress_test_cookie", "tk_ai", "tk_r33", "dt", "usprivacy"}
        return [c for c in self.cookies if c.name.lower() in keep]

    def get(self, path: str, params: dict | None = None) -> str:
        url = SITE + path
        if params:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json, text/html;q=0.8"})
        jar = self._auth_cookies() or self.cookies
        req.add_header("Cookie", "; ".join(c.header_pair() for c in jar))
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return r.read().decode("utf-8", "replace")

    def api(self, func: str, **params) -> dict | None:
        """Call /tcb-api/ and parse JSON; None when the response is not JSON."""
        p = {"web": 1, "f": func}
        p.update(params)
        try:
            body = self.get("/tcb-api/", p)
        except urllib.error.URLError:
            return None
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return None

    def pending(self) -> list[dict] | None:
        """Outstanding assignments, or None when not logged in / unreachable."""
        j = self.api("getAssignmentPending")
        if not j or not j.get("success"):
            return None
        return j.get("data") or []


def essay_markdown(a: dict) -> str:
    """Render a pending-assignment entry as a readable Markdown file body."""
    p = a["assignment"]["post"]
    m = p.get("meta_show") or {}
    body = _AUDIO_TAG.sub("", p.get("post_content", "")).strip()
    clean = lambda t: re.sub(r"<br\s*/?>", "\n", t or "").strip()
    head = [
        f"# {p.get('post_title', '')}",
        f"## {a.get('show_text', '')}",
        "",
        f"HSK {m.get('_cmb_shk_level', '?')} · {a['assignment'].get('type_text', '?')} · due {a.get('due_date_time', '?')}",
    ]
    sections = ["\n".join(head), body]
    nouns = clean(m.get("_cmb_proper_nouns"))
    if nouns:
        sections.append(f"### Proper nouns\n{nouns}")
    kw = clean(m.get("_cmb_keywords"))
    if kw:
        sections.append(f"### Keywords\n{kw}")
    return "\n\n".join(sections) + "\n"
