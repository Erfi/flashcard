# Karteikarten — Deutsch B1

A spaced-repetition flashcard app for German vocabulary and grammar. You add a
word with a command (`add katze`), Claude fills in the article, plural, verb
forms and a B1 example sentence, and the scheduler brings each card back at the
right moment — with intervals capped so nothing is pushed past your target date.

## Quick start

```bash
cp .env.example .env          # then put your Anthropic API key in it
./run.sh                      # creates .venv, installs deps, opens the browser
```

The app runs at <http://127.0.0.1:8000>. The deck lives in `data/deck.yaml`.

To start from the included B1 refresher deck (88 cards — 32 nouns, 24 verbs,
10 adjectives, 5 set phrases, 17 grammar cards):

```bash
./.venv/bin/python -m flashcard seed
```

Without an API key the app still runs: new cards are created empty and you fill
them in by hand in the editor.

## If card generation fails

```bash
./.venv/bin/python -m flashcard check
```

This sends one tiny request and reports what came back. The common cases:

* **`anthropic-workspace-id is required ...`** — your key is *identity-linked*
  (a personal or service-account key that is not scoped to a single workspace),
  so every request has to name the workspace it acts in. Put the id in `.env`:

  ```
  ANTHROPIC_WORKSPACE_ID=wrkspc_01...
  ```

  Find it in the Console under **Settings → Workspaces**, column **ID**. The
  Default Workspace is not listed there — but the API echoes the id in the
  `anthropic-workspace-id` response header, including on the error that asks
  for it, so `flashcard check` prints it for you to copy.
* **401** — the key itself was rejected; check `ANTHROPIC_API_KEY`.
* **credit balance** — the Console account has no credits; top up under Billing.

The app never breaks on a failed generation: the card is created empty (or keeps
its old sentence) and the error appears as a message in the app.

## The command bar

| Command | What it does |
| --- | --- |
| `add katze` | New card; the type is detected automatically |
| `add verb: laufen` | Force a type — `noun`, `verb`, `adjective`, `adverb`, `phrase`, `grammar` |
| `add grammar: Konjunktiv II` | Grammar card: rule plus example sentence |
| `satz katze` | Ask for a fresh example sentence for that card |
| `edit katze` | Open the card in the editor |
| `del katze` | Delete a card |
| `find wohn` | Search the deck |
| `lernen` / `stats` / `karten` | Switch view |
| `set target 2026-09-18` | Target date — drives the interval cap |
| `set new 40` | New cards introduced per day (`0` = unlimited) |
| `set reviews 3` | How many more reviews each card should get before the target date |
| `export` | Download the deck as YAML |
| `help` | The whole list, in the app |

Keyboard while reviewing: `Leertaste` reveals, `1`–`4` grade
(Nochmal / Schwer / Gut / Leicht), `e` edits the current card, `/` jumps back
to the command bar.

## How the scheduling works

Plain SM-2 optimises for long-term retention: intervals grow multiplicatively
and quickly run to weeks. Studying towards a fixed date wants a different shape,
so this app keeps SM-2's grading and ease factor and adds two things:

1. **Intraday learning steps** — 10 min, 1 h, 4 h. Several study blocks a day
   means a new card can usefully come back the same afternoon.
2. **A deadline-derived interval cap.** With a target date set, no interval
   exceeds `days_left ÷ reviews_before_target`. Twenty days out that is about
   6.7 days; three days out it is one day. Nothing gets scheduled past the date,
   and every card keeps earning spaced repetitions inside the horizon.

Once the target date passes, the cap disappears and the behaviour is ordinary
SM-2 — so the deck stays useful after the exam.

Grades: **Nochmal** restarts the learning steps and drops ease by 0.20;
**Schwer** keeps the card in place (×1.2, ease −0.15); **Gut** multiplies by the
ease factor; **Leicht** adds a 1.3 bonus and raises ease.

A practical note: eight hours of review in a day is well past the point where
new vocabulary sticks — recall gains fall off sharply after roughly 60–90
minutes without a break. The intraday steps exist so you can do four or five
shorter blocks instead of one long one; that is what the scheduler is tuned for.

## Colours

Cards are colour-coded on the left edge and in the browse grid:

| | |
| --- | --- |
| **der** | blue |
| **die** | pink |
| **das** | green |
| Verbs | orange |
| Adjectives | violet |
| Adverbs | teal |
| Set phrases | amber |
| Grammar | grey |

The front of a card stays neutral — the article is what you are trying to
recall, so it is not given away by the colour until you flip.

## Example sentences that build on what you know

Every generation request carries a list of the vocabulary you already know
(longest interval first — those are the words you actually remember) plus your
grammar topics, and asks for a sentence that reuses them. As the deck grows,
new sentences increasingly recycle old cards. The **Neuer Satz** button in the
editor (or `satz <word>`) rerolls a sentence you don't like; it is told the old
sentence and asked for something different.

## The deck file

`data/deck.yaml` is plain YAML, meant to be read and edited by hand:

```yaml
cards:
  - id: katze
    type: noun
    lemma: Katze
    article: die
    plural: die Katzen
    definition: Ein kleines Haustier, das oft in Wohnungen lebt.
    example: Die Katze schläft auf dem Sofa.
    tags: [tier]
    level: B1
    source: claude
    srs:
      state: review
      ease: 2.5
      interval_days: 3.0
      due: '2026-09-01T07:00:00+00:00'
      reps: 4
      lapses: 0
```

Edit it while the app is running — the server notices the changed mtime and
reloads. Every write is atomic and keeps the previous version as
`data/deck.yaml.bak`.

Import and export from the **Karten** tab, or:

```bash
python -m flashcard export --out backup.yaml
python -m flashcard import other-deck.yaml --mode merge   # or replace
```

## Command line

```bash
python -m flashcard serve --port 8000 --open
python -m flashcard add katze --type noun
python -m flashcard check
python -m flashcard stats
python -m flashcard seed
```

`--deck path/to/deck.yaml` works on every subcommand; `FLASHCARD_DECK` sets it
globally, `FLASHCARD_MODEL` picks the model (default `claude-sonnet-5`), and
`ANTHROPIC_WORKSPACE_ID` is required for identity-linked keys.

## Layout

```
flashcard/
  models.py      Card + SRS state, YAML-friendly (de)serialisation
  store.py       deck.yaml load/save, atomic writes, import/export
  scheduler.py   deadline-aware SM-2, queue building, projections
  generator.py   Anthropic API calls, JSON extraction, normalisation
  commands.py    the command-bar grammar
  server.py      FastAPI endpoints
  web/           the browser UI (no build step, no dependencies)
  seed_b1.yaml   the B1 refresher deck
tests/           74 tests: scheduler maths, storage, commands, API, generator
```

```bash
./.venv/bin/python -m pytest -q
```
