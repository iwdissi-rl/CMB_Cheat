# Auto_CMB

A small terminal runner for [The Chairman's Bao](https://www.thechairmansbao.com): it finds your logged-in Firefox session, you paste in passage links, and it completes the reading quizzes for you — one per passage, each after a random "reading time" you choose.

## What it does

You run `bao` and it:

1. **Detects your login.** It reads the Chairman's Bao cookies straight out of your running Firefox profile (no API key, no password entry). If the session expired, it tells you to log in and stops.
2. **Takes your links.** Paste Chairman's Bao article links at the prompt, or hand them on the command line. Links that match a pending assignment get the right article and assignment ids automatically.
3. **Reads at a human pace.** Before each quiz it waits a random number of seconds drawn from the window you set (`-r 4-9` means each passage waits between 4 and 9 minutes), so submissions are spread out instead of instant.
4. **Answers with the real key.** The site stores the quiz answers md5-hashed inside its Vue app. The runner opens each quiz in a separate headless Firefox, matches the hashes to the visible options, fills them in, and presses the app's own *next* button — so the site scores and submits normally. Listening assignments are left for you.
5. **Saves the essays.** Every pending passage is also written as Markdown to `~/tcb_essays/<slug>.md` (title, HSK level, due date, text, proper nouns, keywords).

Everything runs on your own machine; nothing is sent anywhere except the Chairman's Bao site itself.

## Getting started

### 1. What you need

- **Python 3.10 or newer** (stdlib only — no third-party packages)
- **Firefox** (snap build works out of the box) logged in to thechairmansbao.com
- **geckodriver** on `PATH` (only when quizzes are actually taken)

### 2. Run it

```bash
git clone https://github.com/iwdissi-rl/paobao.git && cd paobao

python3 -m paobao status          # am I logged in? what's pending?
python3 -m paobao save            # save all pending essays to ~/tcb_essays
python3 -m paobao run URL [URL…]  # complete these passages, paced
python3 -m paobao                 # bare: paste links at the prompt, one per line
```

After `pip install .` the same commands are available as `bao`.

### 3. Useful flags

| Flag | Meaning |
|---|---|
| `-r 4-9` / `--range 4-9` | reading time per passage, minutes (default `4-9`; `-r 5` = fixed 5) |
| `-n` / `--dry-run` | show the plan and the rolled times, touch no browser |
| `--now` | skip the waits, run back to back |
| `--profile PATH` | pick a Firefox profile instead of the newest one |

Exit codes: `0` done, `2` not logged in, `3` a quiz type the solver doesn't know (it dumps the quiz JSON + a screenshot to a debug folder and prints the path).

## Tests

```bash
python3 -m pytest -q
```

24 tests, no network: the vendored LZ4 decoder (Firefox's sessionstore format), link parsing, the pacing window, login detection, and the CLI end-to-end with the browser mocked.

## How the quiz solving works

The site's quiz component keeps the correct answers only as md5 hashes. For each part the runner reads the component's data out of the Vue tree, hashes the visible options and picks the ones whose hash matches — keyword match, true/false, single choice, multi-select and fill-the-blanks are all handled. An unseen shape exits `3` with a dump so a new branch can be added to `driver.solve()`.
