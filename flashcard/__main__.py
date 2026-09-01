"""Command line entry point: `python -m flashcard serve|add|stats|seed|export|import`."""

from __future__ import annotations

import argparse
import sys
import webbrowser
from pathlib import Path

from .generator import ClaudeGenerator, GeneratorError, guess_type
from .models import Card
from .paths import DEFAULT_DECK, SEED_DECKS
from .scheduler import projection
from .store import Store


def cmd_serve(args) -> int:
    import uvicorn

    from .server import create_app

    app = create_app(args.deck)
    url = f"http://{args.host}:{args.port}"
    print(f"Deck:  {Path(args.deck).resolve()}")
    print(f"Karten-App läuft auf {url}")
    if not ClaudeGenerator().available:
        print("Hinweis: ANTHROPIC_API_KEY ist nicht gesetzt — neue Karten werden leer "
              "angelegt und müssen von Hand ausgefüllt werden.")
    if args.open:
        webbrowser.open(url)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


def cmd_add(args) -> int:
    store = Store(args.deck)
    query = " ".join(args.words).strip()
    if store.by_lemma(query):
        print(f"{query} gibt es schon.")
        return 1
    generator = ClaudeGenerator()
    try:
        data = generator.generate_card(query, store.known_lemmas(), store.grammar_topics(),
                                       args.type or "")
        data["source"] = "claude"
    except GeneratorError as exc:
        print(f"! {exc}", file=sys.stderr)
        data = {"lemma": query, "type": args.type or guess_type(query), "source": "manual"}
    card = store.add(Card.from_dict(data))
    print(f"+ {card.headword}")
    if card.plural:
        print(f"  Plural: {card.plural}")
    if card.perfekt:
        print(f"  Formen: {card.praeteritum} / {card.perfekt}")
    if card.definition:
        print(f"  {card.definition}")
    if card.example:
        print(f"  » {card.example}")
    return 0


def cmd_check(args) -> int:
    """Verify the API key, and print the workspace id the API reports."""
    generator = ClaudeGenerator()
    print(f"Modell: {generator.model}")
    print(f"API-Key: {'gesetzt' if generator.available else 'FEHLT'}")
    print(f"Workspace-ID: {generator.workspace_id or '(nicht gesetzt)'}")
    result = generator.probe()
    if result.get("ok"):
        print("Verbindung OK — Karten können erzeugt werden.")
        if result.get("workspace_id"):
            print(f"Die API meldet die Workspace-ID: {result['workspace_id']}")
        return 0
    print(f"! {result['message']}")
    if result.get("workspace_id"):
        print(f"\nDie API hat diese Workspace-ID zurückgeschickt:\n"
              f"  ANTHROPIC_WORKSPACE_ID={result['workspace_id']}\n"
              f"Trag sie in die .env-Datei ein und starte die App neu.")
    return 1


def cmd_stats(args) -> int:
    store = Store(args.deck)
    info = projection(store.cards, store.settings)
    print(f"Karten gesamt: {info['total']}   im Lernstapel: {info['in_rotation']}   "
          f"neu: {info['unseen']}   gefestigt: {info['mature']}")
    if not info["grammar_enabled"] and info["grammar_total"]:
        print(f"{info['grammar_total']} Grammatikkarten sind ausgeblendet "
              f"(`set grammar on` nimmt sie in den Lernstapel).")
    if info["days_left"] is not None:
        print(f"Tage bis zum Zieldatum ({store.settings.get('target_date')}): {info['days_left']:.0f}")
        print(f"Intervall-Obergrenze zurzeit: {info['interval_cap_days']} Tage")
        if info["new_per_day_needed"]:
            print(f"Neue Karten pro Tag, um alles anzufangen: {info['new_per_day_needed']}")
    by_type = {}
    for card in store.cards:
        by_type[card.type] = by_type.get(card.type, 0) + 1
    if by_type:
        print("Nach Typ: " + ", ".join(f"{k}={v}" for k, v in sorted(by_type.items())))
    return 0


def cmd_duplicates(args) -> int:
    """Report cards that are the same word, and pairs that only look alike."""
    from .server import find_duplicates

    store = Store(args.deck)
    report = find_duplicates(store)
    if report["exact"]:
        print(f"Doppelte Karten ({len(report['exact'])} Gruppen):")
        for group in report["exact"]:
            print("  " + " | ".join(
                f"{c['headword']} [{c['id']}, {c['srs']['reps']}×, {c['source']}]" for c in group))
    else:
        print("Keine doppelten Karten.")
    if report["near"] and not args.exact_only:
        print(f"\nÄhnliche Karten ({len(report['near'])}) — meistens verschiedene Wörter, "
              f"nur zur Ansicht:")
        for pair in report["near"]:
            a, b = pair["cards"]
            print(f"  {pair['score']:.2f}  {a['headword']:28} ~ {b['headword']}")
    print(f"\n{report['total']} Karten, {report['unique']} verschiedene Stichwörter.")
    return 1 if report["exact"] else 0


def cmd_seed(args) -> int:
    store = Store(args.deck)
    if args.file:
        sources = [Path(args.file)]
    else:
        levels = ["a2", "b1"] if args.level == "both" else [args.level]
        sources = [SEED_DECKS[lvl] for lvl in levels]
    for path in sources:
        text = Path(path).read_text(encoding="utf-8")
        result = store.import_cards(text, mode="merge")
        print(f"{path.name}: {result['added']} Karten importiert, "
              f"{result['skipped']} übersprungen (schon vorhanden).")
    print(f"Deck gesamt: {len(store.cards)} Karten.")
    return 0


def cmd_export(args) -> int:
    store = Store(args.deck)
    text = store.export_text()
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"Nach {args.out} geschrieben.")
    else:
        sys.stdout.write(text)
    return 0


def cmd_import(args) -> int:
    store = Store(args.deck)
    text = Path(args.file).read_text(encoding="utf-8")
    result = store.import_cards(text, mode=args.mode)
    print(f"{result['added']} importiert, {result['skipped']} übersprungen, "
          f"{result['total']} gesamt.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="flashcard", description="Deutsch-B1-Karteikarten")
    parser.add_argument("--deck", default=str(DEFAULT_DECK), help="Pfad zur deck.yaml")
    subs = parser.add_subparsers(dest="cmd", required=True)

    serve = subs.add_parser("serve", help="Web-Oberfläche starten")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--open", action="store_true", help="Browser öffnen")
    serve.set_defaults(func=cmd_serve)

    add = subs.add_parser("add", help="Karte anlegen, z. B. `add katze`")
    add.add_argument("words", nargs="+")
    add.add_argument("--type", choices=["noun", "verb", "adjective", "adverb", "phrase", "grammar"])
    add.set_defaults(func=cmd_add)

    check = subs.add_parser("check", help="API-Key und Workspace-ID prüfen")
    check.set_defaults(func=cmd_check)

    stats = subs.add_parser("stats", help="Statistik und Prognose")
    stats.set_defaults(func=cmd_stats)

    seed = subs.add_parser("seed", help="A2- oder B1-Starterdeck importieren")
    seed.add_argument("--level", choices=["a2", "b1", "both"], default="b1",
                      help="welches mitgelieferte Deck (Standard: b1)")
    seed.add_argument("--file", help="eigene YAML-Datei statt des mitgelieferten Decks")
    seed.set_defaults(func=cmd_seed)

    dupes = subs.add_parser("duplicates", help="Deck auf doppelte Karten prüfen")
    dupes.add_argument("--exact-only", action="store_true",
                       help="nur echte Dubletten, keine ähnlichen Wörter")
    dupes.set_defaults(func=cmd_duplicates)

    export = subs.add_parser("export", help="Deck ausgeben")
    export.add_argument("--out")
    export.set_defaults(func=cmd_export)

    imp = subs.add_parser("import", help="Karten aus einer YAML-Datei importieren")
    imp.add_argument("file")
    imp.add_argument("--mode", choices=["merge", "replace"], default="merge")
    imp.set_defaults(func=cmd_import)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
