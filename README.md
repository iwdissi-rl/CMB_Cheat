# Auto_CMB

A terminal runner for [The Chairman's Bao](https://www.thechairmansbao.com): it borrows your browser login, you paste passage links, and it completes the reading quizzes — one per passage, each after a random "reading time" you set.

## What it does

You run `bao` and it:

1. **Detects your login.** Reads the Chairman's Bao cookies straight from your browser profile — **Firefox, Chrome, Chromium, Brave or Edge** (no API key, no password entry). If the session expired, it says so and stops.
2. **Takes your links.** Paste article links at the prompt or hand them on the command line; links matching a pending assignment resolve automatically.
3. **Reads at a human pace.** Before each quiz it waits a random time from your window (`-r 4-9` = between 4 and 9 minutes per passage), so submissions spread out instead of firing instantly.
4. **Answers with the real key.** The site stores quiz answers as md5 hashes inside its Vue app. Each quiz opens in a separate headless browser (WebDriver for Firefox, DevTools Protocol for Chromium — nothing to install beyond the browser itself), the hashes are matched to the visible options, and the app's own *next* button is pressed, so the site scores normally. Listening assignments are left for you.
5. **Saves the essays.** Pending passages are written as Markdown to `~/tcb_essays/<slug>.md`.

Everything runs on your machine; nothing leaves it except requests to thechairmansbao.com.

## Getting started

### Install

```bash
pipx install git+https://github.com/iwdissi-rl/CMB_Cheat.git   # or: pip install git+…
```

No `pipx`? Clone and run — there is nothing to install at all:

```bash
git clone https://github.com/iwdissi-rl/CMB_Cheat.git && cd CMB_Cheat
python3 -m paobao status
```

Requirements: **Python 3.10+** (stdlib only) and any one of Firefox / Chrome / Chromium / Brave / Edge, logged in to thechairmansbao.com. Firefox additionally wants `geckodriver` on `PATH` when quizzes are actually taken.

### Run it

```bash
bao status              # am I logged in? what's pending? (tells you which browser it found)
bao save                # save all pending essays to ~/tcb_essays
bao run URL [URL…]      # complete these passages, paced
bao                     # bare: paste links at the prompt, one per line
```

Without `pip install` prefix everything with `python3 -m paobao`.

### Useful flags

| Flag | Meaning |
|---|---|
| `-r 4-9` / `--range 4-9` | reading time per passage, minutes (default `4-9`; `-r 5` = fixed 5) |
| `-n` / `--dry-run` | show the plan and the rolled times, touch no browser |
| `--now` | skip the waits, run back to back |
| `--browser NAME` | `firefox`, `chrome`, `chromium`, `brave`, `edge` (default: first browser holding a login) |
| `--profile PATH` | pick a profile directory instead of the default one |

Exit codes: `0` done, `2` not logged in, `3` a quiz failed (unknown type dumps the quiz JSON + a screenshot to a debug folder and prints the path).

## Tests

```bash
python3 -m pytest -q
```

No network: the vendored LZ4 decoder (Firefox's sessionstore format), the vendored AES decryptor (Chromium's cookie format), link parsing, pacing, login detection across browser families, and the CLI end-to-end with the browser mocked.

## How the quiz solving works

The site's quiz component keeps the correct answers only as md5 hashes. For each part the runner reads the component's data out of the Vue tree, hashes the visible options and picks the ones whose hash matches — keyword match, true/false, single choice, multi-select and fill-the-blanks are all handled. An unseen shape exits `3` with a dump so a new branch can be added to `driver.solve()`.
