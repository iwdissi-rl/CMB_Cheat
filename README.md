# Auto_CMB

A terminal based cheat/hack for [The Chairman's Bao](https://www.thechairmansbao.com): user paste passage links, and it completes the reading quizzes after a random "reading time" users set

## Getting started

### Install

```bash
pipx install git+https://github.com/iwdissi-rl/CMB_Cheat.git   # or: pip install git+…
```

OR Clone n run:

```bash
git clone https://github.com/iwdissi-rl/CMB_Cheat.git && cd CMB_Cheat
python3 -m cmb status
```

Requirements: **Python 3.10+** (stdlib only) and any one of Firefox / Chrome / Chromium / Brave / Edge, logged in to thechairmansbao.com. Firefox additionally wants `geckodriver` on `PATH` when quizzes are actually taken.

### Run it

```bash
cmb status              # am I logged in? what's pending? (tells you which browser it found)
cmb save                # save all pending essays to ~/tcb_essays
cmb run URL [URL…]      # complete these passages, paced
cmb                     # bare: paste links at the prompt, one per line
```

Without `pip install` prefix everything with `python3 -m cmb`.

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