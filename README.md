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

To start from the included decks:

```bash
./.venv/bin/python -m flashcard seed --level b1     # 300 B1 cards
./.venv/bin/python -m flashcard seed --level a2     # 300 A2 cards
./.venv/bin/python -m flashcard seed --level both   # 570 unique cards
```

| Deck | Cards | Nouns | Verbs | Adjectives | Adverbs | Phrases | Grammar |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `seed_a2.yaml` | 300 | 141 | 76 | 36 | 8 | 9 | 30 |
| `seed_b1.yaml` | 300 | 119 | 87 | 40 | 6 | 13 | 35 |

The 30 (A2) and 35 (B1) grammar cards together cover the grammar of both levels
end to end — from Perfekt with haben/sein and Wechselpräpositionen up to
Konjunktiv II der Vergangenheit, all four passive forms, Relativsätze with
dessen/deren, and n-Deklination. Each carries a worked explanation (form, use
and the usual mistake) plus three example sentences.

Importing both is safe: the 30 words that appear in both decks are recognised
as duplicates and skipped, so you end up with 570 unique cards.

Every noun in both decks had its gender and plural checked against a
Wiktionary-derived lexicon, and every verb's Perfekt against its auxiliary.

Without an API key the app still runs: new cards are created empty and you fill
them in by hand in the editor.

## If card generation fails

```bash
./.venv/bin/python -m flashcard check
python -m flashcard duplicates
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
| `set reverse off` | Turn the production direction (meaning → word) on or off |
| `set unlock 3` | Interval in days at which production unlocks for a word |
| `set shuffle off` | Study equally urgent cards in a fixed order instead of shuffled |
| `set grammar on` | Put grammar cards into the study queue (off by default) |
| `export` | Download the deck as YAML |
| `help` | The whole list, in the app |

Keyboard while reviewing: `Leertaste` reveals, `1`–`4` grade
(Nochmal / Schwer / Gut / Leicht), `e` edits the current card, `/` jumps back
to the command bar. The card header says which direction you are being asked
(*Produktion* for meaning → word).

## Two directions

Every vocabulary card is studied both ways, and each direction keeps its own
scheduling state — ease, interval, lapses — because recognising a word and
producing it are different skills that mature at different speeds.

* **Erkennen (recognition).** Front: the word. Back: article, plural or verb
  forms, definition, example. This is the direction you already know.
* **Produktion (production).** Front: the German definition plus the example
  sentence with the word blanked out. You have to come up with the word — and
  for a noun, its article, which is why the article disappears along with the
  noun: *Die Katze schläft auf dem Sofa* → *\_\_\_\_ schläft auf dem Sofa.*

The blanking handles inflected forms, so *läuft*, *lief* and *gelaufen* all
vanish for **laufen**, a separable verb loses its prefix at the end of the
clause, and a reflexive *sich* stays visible as a hint. Grammar cards have no
word to produce, so they stay one-directional.

**Production unlocks, it isn't switched on all at once.** A word's production
card only enters the queue once its recognition card reaches a real review
interval (3 days by default, `set unlock`). Producing a word you cannot yet
recognise is mostly frustration, and unlocking gradually means the workload
grows with your progress instead of doubling on day one. Once production has
started for a word it keeps running on its own schedule, even if the
recognition card later lapses.

In the queue, an unlocked production card outranks a word you have never seen:
revisiting known material beats piling on new vocabulary, and without that rule
a large backlog of new words would starve the production direction completely.

## Progress charts

The Statistik tab plots five things. Two come from the deck itself and work on
the first run; three read `data/reviews.csv`, an append-only log with one row
per answer that the app writes as you study. The log keeps history out of
`deck.yaml` (which stays small and hand-editable) and opens in any spreadsheet.

| Chart | Question it answers |
| --- | --- |
| Gefestigte Karten | Am I getting anywhere? Cards past a 3-day interval, per day, recognition vs production |
| Wiederholungen pro Tag | How much did I actually do? Answers per day; the tooltip breaks them down by grade |
| Behalten | Is it sticking? 7-day rolling share of already-learned cards graded Gut or better |
| Fällig in den nächsten 14 Tagen | What's coming? Already-scheduled reviews, overdue cards folded into today |
| Intervall-Verteilung | How mature is the deck right now? Cards per interval band |

**Behalten is the one to watch.** Below ~80% you are introducing new cards
faster than you are keeping the old ones — lower `set new`. Above ~95% the
intervals are shorter than they need to be and you are spending time you could
give to new material.

The history charts start the day logging begins, and the "Gefestigte Karten"
curve is reconstructed by rewinding today's true numbers through the log, so it
always ends on the figure the deck actually shows. Days before that are flat
rather than invented. Every chart has a **Tabelle** toggle with the same numbers
in text, and the time range (14 / 30 / 60 days) applies to all of them at once.

## Duplicates

`add` checks twice. First against what you typed, ignoring case and a leading
article, so `add die Katze` finds the existing **Katze**. Then again after
Claude has normalised the word, which is the only point at which an inflected
input reveals its lemma — so `add gelaufen` or `add läuft` finds **laufen**
instead of filing a second copy. The generated card is discarded and the
existing one opens.

Renaming a card onto another one is refused, and so is creating one through the
API with a lemma that already exists (`allow_duplicate: true` overrides it).

Words that merely *look* alike are reported, never blocked — `erinnern` and
`die Erinnerung` are two cards worth having, as are `arbeiten` and `die Arbeit`.
Adding one when the other exists shows "Ähnlich im Deck: …" and carries on.

To audit a deck:

```bash
python -m flashcard duplicates              # exact duplicates plus lookalikes
python -m flashcard duplicates --exact-only
```

It exits non-zero when it finds real duplicates, so it works in a pre-commit
hook. `GET /api/duplicates` returns the same report as JSON.

## Grammar cards are reference, not drill

Grammar cards stay **out of the study queue by default**. A rule with three
worked examples is something you read and come back to, not something a
two-second recall test does much for — and 65 of them in the rotation crowd out
vocabulary, which is what spaced repetition is actually good at.

They remain full members of the deck: searchable and editable in **Karten**,
exported and imported with everything else, and `satz <thema>` still rerolls
their examples. `set grammar on` puts them into the queue if you want to drill
them anyway; `set grammar off` takes them back out, and any scheduling they had
picked up is kept for when you switch them back on.

While they are hidden they are not counted as a backlog: the statistics show
how many cards are in the rotation, list the grammar cards separately, and the
daily new-card limit is spent entirely on vocabulary.

## Order of the queue

Learning cards first (their intervals are minutes long), then due reviews, most
overdue first, then new material. Within each of those groups the order is
**shuffled** — cards that are equally urgent come in random order rather than
alphabetically or by creation date, so you don't learn a word by its position
between two neighbours. Reviews are shuffled inside each whole day of lateness,
so a badly overdue card still comes before a mildly overdue one. `set shuffle
off` restores the fixed order.

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
    # grammar cards use `examples:` with three sentences instead of `example:`
    tags: [tier]
    level: B1
    source: claude
    srs:            # recognition: word -> meaning
      state: review
      ease: 2.5
      interval_days: 3.0
      due: '2026-09-01T07:00:00+00:00'
      reps: 4
      lapses: 0
    srs_reverse:    # production: meaning -> word, written once it has started
      state: learning
      ease: 2.5
      interval_days: 0.0
      due: '2026-08-30T09:10:00+00:00'
      reps: 1
      lapses: 0
```

Edit it while the app is running — the server notices the changed mtime and
reloads. `data/reviews.csv` sits beside it and holds the review history; delete
it and you lose the charts' history, nothing else. Every write is atomic and keeps the previous version as
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
python -m flashcard seed --level both
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
  cloze.py       blanks the word out of the example for the production direction
  history.py     the review log and the series the charts draw
  commands.py    the command-bar grammar
  server.py      FastAPI endpoints
  web/           the browser UI (no build step, no dependencies)
                 charts.js is a small SVG chart kit; the palette is validated
                 for colour-vision deficiency in both light and dark themes
  seed_a2.yaml   the A2 deck (300 cards)
  seed_b1.yaml   the B1 deck (300 cards)
tests/           182 tests: scheduler maths, storage, commands, API, generator
```

```bash
./.venv/bin/python -m pytest -q
```
