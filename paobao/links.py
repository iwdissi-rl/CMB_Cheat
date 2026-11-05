"""Parse Chairman's Bao passage links and match them to pending assignments.

A passage link on thechairmansbao.com looks like

    https://www.thechairmansbao.com/the-benefits-of-the-world-cup-to-the-us-economy/
    https://www.thechairmansbao.com/?p=12345&aid=678

The slug (the last path part) is the WordPress ``post_name``; the pending API
returns it as ``assignment.post.post_name`` together with ``assignment.id``
(the assignment id, ``aid``) and ``assignment.article_id`` (``pid``).
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

SITE = "https://www.thechairmansbao.com"

_IGNORABLE = {
    "", "lessons", "blog", "category", "plans", "about-us", "faq", "tutorial",
    "register", "sign-in", "my-account", "assignments", "tasks", "level",
}


def link_to_slug(link: str) -> tuple[str | None, dict[str, str]]:
    """Return (slug, params) for a Chairman's Bao link.

    slug is None when the link uses the ``?p=`` form or is not a passage link.
    params carries any query parameters (p, aid, ...).
    """
    u = urlparse(link.strip())
    if not u.path and not u.query:
        return None, {}
    params = {k: v[0] for k, v in parse_qs(u.query).items()}
    parts = [p for p in u.path.split("/") if p]
    if params.get("p"):
        return None, params
    if not parts:
        return None, params
    slug = parts[-1]
    if slug in _IGNORABLE or "." in slug:
        return None, params
    return slug, params


def parse_links(text: str) -> list[str]:
    """Split user input (one link per line, or space separated) into links."""
    out = []
    for chunk in re.split(r"[\s,]+", text):
        chunk = chunk.strip()
        if chunk.startswith("http://") or chunk.startswith("https://"):
            out.append(chunk)
    return out


def index_pending(data: list[dict]) -> dict[str, dict]:
    """Index the pending-assignments API payload by passage slug."""
    idx: dict[str, dict] = {}
    for a in data or []:
        A = a.get("assignment") or {}
        post = A.get("post") or {}
        slug = post.get("post_name")
        if slug:
            idx[slug] = a
    return idx


def resolve(link: str, pending_idx: dict[str, dict]) -> dict:
    """Resolve a link to {link, slug, pid, aid, title} using the pending index.

    pid/aid come from the pending list when the passage is assigned; from the
    link's own query parameters otherwise. Both may be None for a bare slug
    link that is not assigned — the site can still open it.
    """
    slug, params = link_to_slug(link)
    entry = pending_idx.get(slug) if slug else None
    pid = params.get("p")
    aid = params.get("aid")
    title = link
    if entry:
        A = entry["assignment"]
        post = A.get("post") or {}
        pid = pid or str(A.get("article_id") or post.get("ID") or "") or None
        aid = aid or str(A.get("id") or "") or None
        title = entry.get("show_text") or post.get("post_title") or link
    return {"link": link, "slug": slug, "pid": pid, "aid": aid, "title": title}
