"""Browser registry: which browsers paobao knows, where their profiles live.

Two families share everything between them:

  firefox   Firefox                     — cookies: moz_cookies + sessionstore,
                                          engine: geckodriver (driver.WD)
  chromium  Chrome, Chromium, Brave,    — cookies: Chromium `cookies` schema
            Edge                          (values v10/v11-encrypted on Linux),
                                          engine: CDP (cdp.CdpRunner)

`detect_browsers()` answers "where am I logged in?" across all of them;
`make_runner(name)` hands the quiz engine the right driver.
"""

from __future__ import annotations

import os

from . import firefox

HOME = os.path.expanduser("~")

# display name -> (family, profile roots to check, first)
_FAMILIES = {
    "firefox": ("firefox",),
    "chrome": ("chromium",),
    "chromium": ("chromium",),
    "brave": ("chromium",),
    "edge": ("chromium",),
}

# profile directory candidates per browser, first existing wins
_PROFILE_DIRS = {
    "firefox": ["~/.mozilla/firefox", "~/snap/firefox/common/.mozilla/firefox"],
    "chrome": ["~/.config/google-chrome", "~/snap/chromium/common/chromium"],
    "chromium": ["~/.config/chromium"],
    "brave": ["~/.config/BraveSoftware/Brave-Browser"],
    "edge": ["~/.config/microsoft-edge"],
}


def family(name: str) -> str:
    try:
        return _FAMILIES[name][0]
    except KeyError:
        raise SystemExit(
            f"unknown browser {name!r} — choose from {', '.join(_FAMILIES)}")


def browser_dirs(name: str) -> list[str]:
    """Existing profile roots for ``name`` (Firefox: profiles; Chromium: config root)."""
    out = []
    for d in _PROFILE_DIRS[name]:
        p = os.path.expanduser(d)
        if os.path.isdir(p):
            out.append(p)
    return out


def profiles(name: str) -> list[str]:
    """Concrete profile directories holding a cookie DB."""
    if family(name) == "firefox":
        return firefox.candidate_profiles() or _firefox_cookie_profiles()
    from . import chromium
    prof = chromium.default_profile(name)
    return [prof] if prof else []


def _firefox_cookie_profiles() -> list[str]:
    """Firefox profiles that have cookies.sqlite but no sessionstore backup."""
    import glob
    out = []
    for d in _PROFILE_DIRS["firefox"]:
        out += sorted(glob.glob(os.path.expanduser(d) + "/*"))
    return [p for p in out if os.path.isfile(os.path.join(p, "cookies.sqlite"))]


def cookies_for(name: str, profile: str | None = None) -> list[firefox.Cookie]:
    """All thechairmansbao.com cookies ``name`` holds (decrypted where possible)."""
    if family(name) == "firefox":
        return firefox.site_cookies(profile)
    from . import chromium
    profs = [profile] if profile else profiles(name)
    merged: dict[tuple[str, str, str], firefox.Cookie] = {}
    for prof in profs:
        for c in chromium.site_cookies(prof):
            merged[(c.domain, c.name, c.path)] = c
    return sorted(merged.values(), key=lambda c: (c.domain, c.path, c.name))


def installed(name: str) -> bool:
    """True when this browser has a profile tree (or a binary) on this machine."""
    if browser_dirs(name):
        return True
    if family(name) == "firefox":
        from .driver import default_firefox_binary  # noqa: F401 — existence only
        import shutil
        return bool(shutil.which("firefox") or shutil.which("snap"))
    from .cdp import chromium_binary
    return chromium_binary() is not None


def detect(prefer: list[str] | None = None) -> tuple[str | None, list[firefox.Cookie]]:
    """Find the first browser (in ``prefer`` order, else all) holding a TCB login.

    Returns (browser_name | None, cookies). A browser with only analytics
    cookies (_ga etc.) does not count as logged in.
    """
    order = prefer or list(_FAMILIES)
    for name in order:
        try:
            cks = cookies_for(name)
        except Exception:
            continue
        if cks and _logged_in(cks):
            return name, cks
    return None, []


def _logged_in(cookies: list[firefox.Cookie]) -> bool:
    from .api import Tcb
    return Tcb(cookies).logged_in  # same analytics-cookie filter as the API client


def make_runner(name: str, cookies: list[firefox.Cookie]):
    """Start the right headless engine for ``name``, seeded with ``cookies``."""
    if family(name) == "firefox":
        from .driver import default_firefox_binary, make_runner as firefox_runner
        return firefox_runner(cookies, binary=default_firefox_binary())
    from .cdp import CdpRunner
    return CdpRunner(cookies=cookies)
