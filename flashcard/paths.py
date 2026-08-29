import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DECK = Path(os.environ.get("FLASHCARD_DECK", PROJECT_ROOT / "data" / "deck.yaml"))
SEED_DECK = Path(__file__).resolve().parent / "seed_b1.yaml"
