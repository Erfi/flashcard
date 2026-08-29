"""Card content generation via the Anthropic API.

Uses urllib from the standard library rather than the SDK, so the app has
exactly one third-party dependency chain (FastAPI + PyYAML) and no version
skew to worry about.

Everything the model returns is *proposed* content: it lands in the card and
you can edit any field by hand afterwards. The generator never overwrites the
`notes` field.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Dict, List, Optional

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
DEFAULT_MODEL = os.environ.get("FLASHCARD_MODEL", "claude-sonnet-5")
# Identity-linked keys (personal or service-account keys that are not scoped to a
# single workspace) must name the workspace they act in on every request.
DEFAULT_WORKSPACE = os.environ.get("ANTHROPIC_WORKSPACE_ID", "")

SYSTEM_PROMPT = """Du bist ein erfahrener DaF-Lehrer und erstellst Karteikarten für \
eine Lernerin bzw. einen Lerner auf Niveau B1 (GER).

Regeln:
- Alle Inhalte sind AUSSCHLIESSLICH auf Deutsch. Keine Übersetzungen in andere Sprachen.
- Die Definition ist eine kurze, einfache Erklärung auf B1-Niveau (höchstens 15 Wörter),
  die das Stichwort selbst nicht verwendet.
- Der Beispielsatz ist ein vollständiger, natürlicher Satz mit höchstens 14 Wörtern.
- Grammatische Angaben müssen korrekt sein (Artikel, Plural, Stammformen, Hilfsverb).
- Antworte NUR mit einem einzigen JSON-Objekt, ohne Markdown, ohne Kommentar."""

CARD_SCHEMA = """{
  "type": "noun | verb | adjective | adverb | phrase | grammar",
  "lemma": "Grundform, bei Nomen ohne Artikel und großgeschrieben",
  "article": "der | die | das   (nur bei Nomen, sonst \\"\\")",
  "plural": "Pluralform mit Artikel, z. B. \\"die Katzen\\"  (nur bei Nomen, sonst \\"\\")",
  "praesens_3sg": "3. Person Singular Präsens, z. B. \\"läuft\\"  (nur bei Verben)",
  "praeteritum": "3. Person Singular Präteritum, z. B. \\"lief\\"  (nur bei Verben)",
  "perfekt": "Perfekt mit Hilfsverb, z. B. \\"ist gelaufen\\"  (nur bei Verben)",
  "aux": "haben | sein  (nur bei Verben, sonst \\"\\")",
  "separable": true/false,
  "rection": "Präposition und Kasus, falls relevant, z. B. \\"warten auf + Akk.\\", sonst \\"\\"",
  "definition": "kurze deutsche Erklärung (B1)",
  "example": "Beispielsatz auf Deutsch",
  "tags": ["ein bis drei thematische Schlagwörter auf Deutsch"],
  "level": "A2 | B1 | B2"
}"""


class GeneratorError(RuntimeError):
    pass


def _reuse_block(known: List[str], topics: List[str]) -> str:
    if not known and not topics:
        return ""
    lines = []
    if known:
        lines.append(
            "Bekannter Wortschatz (verwende im Beispielsatz möglichst Wörter aus dieser "
            "Liste, damit der Satz Wiederholung bringt):\n" + ", ".join(known)
        )
    if topics:
        lines.append(
            "Bereits gelernte Grammatikthemen (baue eines davon in den Beispielsatz ein, "
            "wenn es natürlich passt):\n" + ", ".join(topics)
        )
    return "\n\n".join(lines)


class ClaudeGenerator:
    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None,
                 timeout: float = 60.0, workspace_id: Optional[str] = None):
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self.model = model or DEFAULT_MODEL
        self.workspace_id = (workspace_id if workspace_id is not None else DEFAULT_WORKSPACE).strip()
        self.timeout = timeout

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    # ----------------------------------------------------------------- http

    def _call(self, prompt: str, max_tokens: int = 900) -> str:
        if not self.available:
            raise GeneratorError(
                "Kein ANTHROPIC_API_KEY gesetzt — die Karte wurde leer angelegt, "
                "du kannst sie von Hand ausfüllen."
            )
        body = json.dumps({
            "model": self.model,
            "max_tokens": max_tokens,
            "system": SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": prompt}],
        }).encode("utf-8")
        headers = {
            "content-type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": API_VERSION,
        }
        if self.workspace_id:
            headers["anthropic-workspace-id"] = self.workspace_id
        request = urllib.request.Request(API_URL, data=body, method="POST", headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:400]
            raise GeneratorError(_explain_http_error(exc.code, detail)) from exc
        except urllib.error.URLError as exc:
            raise GeneratorError(f"Keine Verbindung zur API: {exc.reason}") from exc

        chunks = [b.get("text", "") for b in payload.get("content", []) if b.get("type") == "text"]
        text = "".join(chunks).strip()
        if not text:
            raise GeneratorError("Die API hat eine leere Antwort geschickt.")
        return text

    # -------------------------------------------------------------- diagnose

    def probe(self) -> Dict:
        """Send the smallest possible request and report what came back.

        Also reports the workspace id, which the API echoes in the
        `anthropic-workspace-id` response header — including on the error that
        asks for it. That is the only way to learn the id of the Default
        Workspace, which the Console does not list.
        """
        if not self.available:
            return {"ok": False, "message": "ANTHROPIC_API_KEY ist nicht gesetzt."}
        body = json.dumps({
            "model": self.model, "max_tokens": 1,
            "messages": [{"role": "user", "content": "hi"}],
        }).encode("utf-8")
        headers = {
            "content-type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": API_VERSION,
        }
        if self.workspace_id:
            headers["anthropic-workspace-id"] = self.workspace_id
        request = urllib.request.Request(API_URL, data=body, method="POST", headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return {"ok": True, "model": self.model,
                        "workspace_id": response.headers.get("anthropic-workspace-id", "")}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:400]
            return {"ok": False, "code": exc.code,
                    "message": _explain_http_error(exc.code, detail),
                    "workspace_id": exc.headers.get("anthropic-workspace-id", "") if exc.headers else ""}
        except urllib.error.URLError as exc:
            return {"ok": False, "message": f"Keine Verbindung zur API: {exc.reason}"}

    # ------------------------------------------------------------- generate

    def generate_card(self, query: str, known: List[str] | None = None,
                      topics: List[str] | None = None, hint_type: str = "") -> Dict:
        hint = ""
        if hint_type:
            hint = f"\nDas Stichwort ist ein/e: {hint_type}."
        if hint_type == "grammar":
            hint += (
                "\nBei einer Grammatikkarte ist \"lemma\" der Name des Themas, "
                "\"definition\" die Regel in einfachen Worten (höchstens 30 Wörter) "
                "und \"example\" ein Beispielsatz, der die Regel zeigt."
            )
        prompt = (
            f"Erstelle eine Karteikarte für das Stichwort: {query!r}.{hint}\n\n"
            f"Gib genau dieses JSON-Format zurück:\n{CARD_SCHEMA}\n\n"
            f"{_reuse_block(known or [], topics or [])}"
        ).strip()
        data = _extract_json(self._call(prompt))
        data.setdefault("lemma", query.strip())
        return _normalise(data)

    def generate_sentence(self, card_summary: Dict, known: List[str] | None = None,
                          topics: List[str] | None = None, avoid: str = "") -> str:
        avoid_line = f"\nDer alte Satz war: {avoid!r}. Schreibe einen deutlich anderen Satz." if avoid else ""
        prompt = (
            "Schreibe EINEN neuen deutschen Beispielsatz (B1, höchstens 14 Wörter) "
            f"für dieses Stichwort:\n{json.dumps(card_summary, ensure_ascii=False)}"
            f"{avoid_line}\n\n{_reuse_block(known or [], topics or [])}\n\n"
            "Antworte nur mit JSON: {\"example\": \"...\"}"
        )
        data = _extract_json(self._call(prompt, max_tokens=400))
        sentence = str(data.get("example") or "").strip()
        if not sentence:
            raise GeneratorError("Die API hat keinen Satz geliefert.")
        return sentence


def _explain_http_error(code: int, detail: str) -> str:
    """Turn the common API errors into something actionable."""
    lowered = detail.lower()
    if "anthropic-workspace-id" in lowered:
        return (
            "Dein API-Key ist an eine Identität gebunden und braucht die Workspace-ID. "
            "Setze ANTHROPIC_WORKSPACE_ID in der .env-Datei (Console → Settings → "
            "Workspaces, Spalte ID, z. B. wrkspc_01ABC...) und starte die App neu."
        )
    if code == 401:
        return "API-Key wurde abgelehnt (401). Prüfe ANTHROPIC_API_KEY in der .env-Datei."
    if "credit balance" in lowered or "billing" in lowered:
        return "Kein Guthaben auf dem Console-Konto. Lade unter Billing Credits auf."
    if code == 429:
        return "Zu viele Anfragen (429). Warte kurz und versuche es noch einmal."
    if code == 404 and "model" in lowered:
        return (f"Das Modell wurde nicht gefunden (404). Setze FLASHCARD_MODEL auf ein "
                f"verfügbares Modell. Antwort: {detail}")
    return f"API-Fehler {code}: {detail}"


def _extract_json(text: str) -> Dict:
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fenced:
        text = fenced.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError as exc:
            raise GeneratorError(f"Antwort war kein gültiges JSON: {text[:200]}") from exc
    raise GeneratorError(f"Antwort war kein gültiges JSON: {text[:200]}")


def _normalise(data: Dict) -> Dict:
    out = dict(data)
    ctype = str(out.get("type") or "").strip().lower()
    aliases = {
        "nomen": "noun", "substantiv": "noun", "n": "noun",
        "verb": "verb", "v": "verb",
        "adjektiv": "adjective", "adj": "adjective",
        "adverb": "adverb", "adv": "adverb",
        "redewendung": "phrase", "wendung": "phrase", "ausdruck": "phrase",
        "grammatik": "grammar",
    }
    out["type"] = aliases.get(ctype, ctype or "other")

    article = str(out.get("article") or "").strip().lower()
    out["article"] = article if article in ("der", "die", "das") else ""

    lemma = str(out.get("lemma") or "").strip()
    # strip a leading article the model may have included anyway
    match = re.match(r"^(der|die|das)\s+(.*)$", lemma, re.I)
    if match:
        out["article"] = out["article"] or match.group(1).lower()
        lemma = match.group(2).strip()
    out["lemma"] = lemma

    if out["type"] == "noun" and lemma:
        out["lemma"] = lemma[0].upper() + lemma[1:]

    aux = str(out.get("aux") or "").strip().lower()
    out["aux"] = aux if aux in ("haben", "sein") else ""
    out["separable"] = bool(out.get("separable"))

    tags = out.get("tags") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",")]
    out["tags"] = [str(t).strip() for t in tags if str(t).strip()][:4]

    for key in ("plural", "praesens_3sg", "praeteritum", "perfekt", "rection",
                "definition", "example"):
        out[key] = str(out.get(key) or "").strip()

    level = str(out.get("level") or "B1").strip().upper()
    out["level"] = level if level in ("A1", "A2", "B1", "B2", "C1") else "B1"
    return out


def guess_type(word: str) -> str:
    """Cheap offline guess, used when no API key is configured."""
    word = word.strip()
    if not word:
        return "other"
    if " " in word:
        return "phrase"
    if word[0].isupper():
        return "noun"
    if word.endswith(("en", "ern", "eln")):
        return "verb"
    return "other"
