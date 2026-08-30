import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DECK = Path(os.environ.get("FLASHCARD_DECK", PROJECT_ROOT / "data" / "deck.yaml"))

PACKAGE_DIR = Path(__file__).resolve().parent
SEED_DECKS = {
    "a2": PACKAGE_DIR / "seed_a2.yaml",
    "b1": PACKAGE_DIR / "seed_b1.yaml",
}
SEED_DECK = SEED_DECKS["b1"]  # default when no level is given
