# cmb — agent notes

CLI for The Chairman's Bao quizzes. Stdlib only — no third-party runtime deps
(vendored pure-Python LZ4 for Firefox sessionstore, vendored AES-256-CBC for
Chromium-family cookies). Keep it that way.

## Commands

```bash
/usr/bin/python3 -m pytest -q          # test runner (pip --user --break-system-packages)
/usr/bin/python3 -m cmb status      # run without installing
/usr/bin/python3 -m pip install --user --break-system-packages --quiet .   # gives ~/.local/bin/cmb
```

## Layout

- `cmb/firefox.py` — Firefox profile/cookies + vendored LZ4 (`mozLz40\0` + 4B LE size + block at byte 12).
- `cmb/chromium.py` — Chromium-family cookies (Chrome/Chromium/Brave/Edge): keyring password from
  `~/.config/<bn>/Local State` + vendored PBKDF2/AES-256-CBC, v10/v11 prefixes.
- `cmb/ws.py` — minimal RFC 6455 client over `socket` (for CDP).
- `cmb/cdp.py` — `CdpRunner`: drives Chromium-family via DevTools Protocol (no driver binary);
  duck-types `driver.WD` (new/url/js/screenshot/quit). Binary discovery: first Chromium-family
  browser on PATH (chrome/chromium/brave/edge), else the newest `~/.cache/ms-playwright/chromium-*`.
- `cmb/browsers.py` — registry: `detect()`, `cookies_for(name, profile)`, `make_runner(name, profile)`
  (firefox→geckodriver, else→CdpRunner). `_BROWSERS` maps family → config dir names + CDP binary globs.
- `cmb/driver.py` — geckodriver WD client + quiz solving (`run_quiz`, `Unknown`).
- `cmb/cli.py` — argparse (`status`/`save`/`run`, bare links → run). `--browser`/`--profile` are
  SUPPRESS-default at subcommand level so `cmb --browser chrome status` works (flags before the
  subcommand are rerouted in `main()` — regression-tested).

## Gotchas

- TCB login cookies expire; exit code 2 = not logged in in every browser cmb can see.
- A locked keyring shows up as all-encrypted-cookies-decrypt-to-empty; chromium.cookies() prints a
  stderr warning for that case.
- Quiz solving needs the site's own JS — headless runs use a *separate* browser instance and inject
  walk.js into the Vue tree; never drive the owner's live profile.
- Exit codes: 0 done, 2 not logged in, 3 a quiz failed.

## Tests

`/usr/bin/python3 -m pytest -q` → 48 passed. `tests/test_cdp.py` has a live-CDP smoke test that
launches real headless Chromium (skips cleanly if no binary). Do not mock away `solve()` — the
hash-matching logic is the point (see `tests/test_cmb.py::test_solve_*`).
