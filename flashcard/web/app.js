/* German B1 flashcards — browser UI.
   Plain ES modules-free JS: one file, no build step, no dependencies. */

const S = {
  view: "review",
  state: null,
  queue: [],
  index: 0,
  revealed: false,
  editing: null,
  browse: { q: "", type: "", sort: "due", cards: [] },
  allCards: [],
  help: null,
};

const TYPE_LABEL = {
  noun: "Nomen", verb: "Verb", adjective: "Adjektiv", adverb: "Adverb",
  phrase: "Wendung", grammar: "Grammatik", other: "Sonstiges",
};
const COLOR_KEYS = ["der", "die", "das", "verb", "adjective", "adverb", "phrase", "grammar", "other"];
const GRADES = [
  { g: 0, lab: "Nochmal", key: "1" },
  { g: 1, lab: "Schwer", key: "2" },
  { g: 2, lab: "Gut", key: "3" },
  { g: 3, lab: "Leicht", key: "4" },
];

const $ = (sel) => document.querySelector(sel);
const el = (tag, attrs = {}, ...kids) => {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") node.className = v;
    else if (k === "style") node.setAttribute("style", v);
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v);
  }
  for (const kid of kids.flat()) {
    if (kid === null || kid === undefined || kid === false) continue;
    node.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
  return node;
};
const colorVar = (key) => `--c: var(--${COLOR_KEYS.includes(key) ? key : "other"})`;

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "content-type": "application/json" },
    ...options,
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (_) {}
    throw new Error(detail);
  }
  return res.headers.get("content-type")?.includes("json") ? res.json() : res.text();
}

/* --------------------------------------------------------------- toasts */
function toast(message, isError = false, ms = 4200) {
  const box = el("div", { class: "toast" + (isError ? " err" : "") },
    el("span", {}, message),
    el("button", { class: "close", onclick: (e) => e.target.closest(".toast").remove() }, "✕"));
  $("#toasts").append(box);
  if (ms) setTimeout(() => box.remove(), ms);
}

/* ---------------------------------------------------------------- status */
function renderStatus() {
  const bar = $("#status");
  bar.textContent = "";
  const st = S.state;
  if (!st) return;
  const c = st.counts;
  const pills = [
    ["fällig", c.due], ["im Lernen", c.learning], ["neu", c.new],
  ].map(([label, n]) => el("span", { class: "pill" }, el("b", {}, String(n)), " " + label));
  if (c.reverse) {
    pills.push(el("span", { class: "pill" }, el("b", {}, String(c.reverse)), " Produktion"));
  }

  const p = st.projection;
  if (p.days_left !== null && p.days_left !== undefined) {
    pills.push(el("span", { class: "pill" },
      el("b", {}, `${Math.max(0, Math.round(p.days_left))}`), " Tage bis zum Ziel"));
    pills.push(el("span", { class: "pill" },
      "max. Intervall ", el("b", {}, `${p.interval_cap_days} T.`)));
  }
  if (!c.due && !c.learning && !c.new && st.next_due) {
    pills.push(el("span", { class: "pill" }, "nächste Karte in ", el("b", {}, st.next_due)));
  }
  if (!st.generator_available) {
    pills.push(el("span", { class: "pill" }, "kein API-Key — Karten bleiben leer"));
  }
  pills.forEach((p2) => bar.append(p2));
}

/* ---------------------------------------------------------------- review */
const ASK = {
  noun: "Artikel? Plural?",
  verb: "Präteritum? Perfekt?",
  grammar: "Regel? Beispielsatz?",
};
const ASK_REVERSE = {
  noun: "Welches Wort — mit Artikel?",
  verb: "Welches Verb?",
  adjective: "Welches Adjektiv?",
  adverb: "Welches Wort?",
  phrase: "Welche Wendung?",
};

function cardFront(card) {
  if (card.direction === "reverse") return cardFrontReverse(card);
  const isGrammar = card.type === "grammar";
  return el("div", { class: "flashcard front", style: isGrammar ? colorVar(card.color_key) : "" },
    el("div", { class: "kicker" }, TYPE_LABEL[card.type] || card.type),
    el("div", { class: "headword" }, card.lemma),
    ASK[card.type] ? el("div", { class: "keyhint" }, ASK[card.type]) : null,
  );
}

/* Production: you get the meaning and a sentence with a gap, and have to come
   up with the word itself. The card stays neutral in colour — for a noun the
   article is part of the answer. */
function cardFrontReverse(card) {
  const sentence = card.example_masked || (card.examples_masked || [])[0] || "";
  return el("div", { class: "flashcard front reverse" },
    el("div", { class: "kicker" }, "Produktion · " + (TYPE_LABEL[card.type] || card.type)),
    el("div", { class: "prompt-def" }, card.definition || "—"),
    sentence ? el("div", { class: "example" }, sentence) : null,
    el("div", { class: "keyhint" }, ASK_REVERSE[card.type] || "Welches Wort?"),
  );
}

function cardBack(card) {
  const forms = [];
  if (card.type === "noun" && card.plural) forms.push(["Plural", card.plural]);
  if (card.type === "verb") {
    if (card.praesens_3sg) forms.push(["Präsens (er/sie)", card.praesens_3sg]);
    if (card.praeteritum) forms.push(["Präteritum", card.praeteritum]);
    if (card.perfekt) forms.push(["Perfekt", card.perfekt]);
    if (card.separable) forms.push(["", "trennbar"]);
  }
  if (card.rection) forms.push(["Rektion", card.rection]);

  return el("div", { class: "flashcard", style: colorVar(card.color_key) },
    el("div", { class: "kicker" },
      (card.direction === "reverse" ? "Produktion · " : "") + (TYPE_LABEL[card.type] || card.type)),
    el("div", { class: "headword" },
      card.article ? el("span", { class: "art" }, card.article + " ") : null, card.lemma),
    forms.length ? el("div", { class: "formrow" },
      forms.map(([k, v]) => el("span", {}, k ? k + ": " : "", el("b", {}, v)))) : null,
    card.definition ? el("div", { class: "definition" }, card.definition) : null,
    card.example ? el("div", { class: "example" }, card.example) : null,
    (card.examples || []).length
      ? el("div", { class: "examplelist" }, card.examples.map((x) => el("div", { class: "example" }, x)))
      : null,
    card.tags?.length ? el("div", { class: "tagrow" }, card.tags.map((t) => el("span", { class: "tag" }, t))) : null,
  );
}

function renderReview() {
  const view = $("#view");
  view.textContent = "";
  const card = S.queue[S.index];

  if (!card) {
    view.append(el("div", { class: "empty" },
      el("h2", {}, S.state && S.state.projection.total ? "Alles wiederholt 🎉" : "Noch keine Karten"),
      el("p", {}, S.state && S.state.projection.total
        ? (S.state.next_due ? `Die nächste Karte ist in ${S.state.next_due} fällig.` : "Nichts mehr fällig.")
        : "Leg die erste an: tippe oben z. B. add katze"),
      el("button", { class: "bigbtn", style: "max-width:260px;margin:14px auto 0", onclick: refreshQueue }, "Neu laden")));
    return;
  }

  const stage = el("div", { class: "stage" });
  stage.append(S.revealed ? cardBack(card) : cardFront(card));

  if (!S.revealed) {
    stage.append(el("button", { class: "bigbtn", onclick: reveal }, "Aufdecken  ·  Leertaste"));
  } else {
    const row = el("div", { class: "answers" });
    GRADES.forEach(({ g, lab, key }) => {
      row.append(el("button", { class: "g" + g, onclick: () => answer(g) },
        el("span", { class: "lab" }, lab),
        el("span", { class: "sub" }, card.preview ? card.preview[String(g)] : ""),
        el("span", { class: "sub" }, key)));
    });
    stage.append(row);
    stage.append(el("div", { class: "keyhint" },
      `${S.queue.length - S.index} in der Warteschlange · `,
      el("a", { href: "#", onclick: (e) => { e.preventDefault(); openEditor(card.id); } }, "bearbeiten"),
      " · ",
      el("a", { href: "#", onclick: (e) => { e.preventDefault(); regenerate(card.id); } },
        card.type === "grammar" ? "neue Beispiele" : "neuer Satz")));
  }
  view.append(stage);
}

function reveal() {
  if (!S.queue[S.index]) return;
  S.revealed = true;
  renderReview();
}

async function answer(grade) {
  const card = S.queue[S.index];
  if (!card || !S.revealed) return;
  try {
    const res = await api("/api/answer", {
      method: "POST",
      body: { id: card.id, grade, direction: card.direction || "forward" },
    });
    S.state = res.state;
    // A card graded "again" or still in learning comes back this session:
    // drop it here and refetch the queue so ordering stays honest.
    S.queue.splice(S.index, 1);   // this pair is done for now
    S.revealed = false;
    if (S.index >= S.queue.length) S.index = 0;
    renderStatus();
    renderReview();
    if (S.queue.length <= 1) refreshQueue();
  } catch (err) {
    toast(err.message, true);
  }
}

async function refreshQueue() {
  const res = await api("/api/queue");
  S.queue = res.cards;
  S.index = 0;
  S.revealed = false;
  await refreshState();
  if (S.view === "review") renderReview();
}

async function refreshState() {
  S.state = await api("/api/state");
  renderStatus();
}

/* ---------------------------------------------------------------- browse */
async function loadBrowse() {
  const params = new URLSearchParams({ q: S.browse.q, type: S.browse.type, sort: S.browse.sort });
  const res = await api("/api/cards?" + params.toString());
  S.browse.cards = res.cards;
  // Repaint only the results. Rebuilding the whole view would replace the
  // search field mid-keystroke and the browser would drop focus with it.
  if (S.view === "browse") renderGrid();
}

let searchTimer = null;
function scheduleSearch() {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(loadBrowse, 300);
}

function dueLabel(card) {
  const srs = card.srs;
  if (srs.state === "new") return "neu";
  if (!srs.due) return "—";
  const diff = (new Date(srs.due) - new Date()) / 86400000;
  if (diff <= 0) return "fällig";
  if (diff < 1) return `in ${Math.max(1, Math.round(diff * 24))} Std.`;
  return `in ${Math.round(diff)} T.`;
}

function renderBrowse() {
  const view = $("#view");
  view.textContent = "";

  const search = el("input", {
    id: "browsesearch", placeholder: "suchen …", value: S.browse.q, autocomplete: "off",
    oninput: (e) => { S.browse.q = e.target.value; scheduleSearch(); },
  });
  const typeSel = el("select", { onchange: (e) => { S.browse.type = e.target.value; loadBrowse(); } },
    el("option", { value: "" }, "alle Typen"),
    Object.entries(TYPE_LABEL).map(([k, v]) => el("option", { value: k, selected: S.browse.type === k }, v)));
  const sortSel = el("select", { onchange: (e) => { S.browse.sort = e.target.value; loadBrowse(); } },
    el("option", { value: "due", selected: S.browse.sort === "due" }, "nach Fälligkeit"),
    el("option", { value: "alpha", selected: S.browse.sort === "alpha" }, "alphabetisch"),
    el("option", { value: "created", selected: S.browse.sort === "created" }, "zuletzt angelegt"));

  view.append(el("div", { class: "toolrow" }, search, typeSel, sortSel,
    el("button", { class: "bigbtn", style: "width:auto;padding:8px 14px", onclick: exportDeck }, "Export"),
    el("button", { class: "bigbtn", style: "width:auto;padding:8px 14px", onclick: importDeck }, "Import")));
  view.append(el("div", { class: "grid", id: "cardgrid" }));
  renderGrid();
}

/* Only the result tiles. The toolbar above them is never touched here, so the
   search field keeps focus and the caret position while results update. */
function renderGrid() {
  const grid = $("#cardgrid");
  if (!grid) { renderBrowse(); return; }
  grid.textContent = "";

  if (!S.browse.cards.length) {
    grid.className = "";
    grid.append(el("div", { class: "empty" }, el("p", {}, "Keine Karten gefunden.")));
    return;
  }

  grid.className = "grid";
  S.browse.cards.forEach((card) => {
    grid.append(el("div", { class: "mini", style: colorVar(card.color_key), onclick: () => openEditor(card.id) },
      el("div", { class: "hw" },
        card.article ? el("span", { class: "art" }, card.article + " ") : null, card.lemma),
      el("div", { class: "sub" }, card.definition || card.example || (card.examples || [])[0] || "—"),
      el("div", { class: "meta" },
        el("span", { class: "dot" }), " ",
        el("span", {}, TYPE_LABEL[card.type] || card.type),
        el("span", {}, "·"),
        el("span", {}, dueLabel(card)),
        card.srs.reps ? el("span", {}, `${card.srs.reps}×`) : null,
        card.srs_reverse && card.srs_reverse.reps ? el("span", { title: "Produktion läuft" }, "⇄") : null)));
  });
}

/* ---------------------------------------------------------------- editor */
async function openEditor(cardId) {
  const found = [...S.queue, ...S.browse.cards].find((c) => c.id === cardId);
  const card = found || (await api("/api/cards?q=" + encodeURIComponent(cardId))).cards.find((c) => c.id === cardId);
  if (!card) return toast("Karte nicht gefunden", true);
  S.editing = JSON.parse(JSON.stringify(card));
  renderEditor();
  $("#drawer").hidden = false;
}

function closeEditor() {
  $("#drawer").hidden = true;
  S.editing = null;
}

function autosize(area) {
  const fit = () => { area.style.height = "auto"; area.style.height = area.scrollHeight + 4 + "px"; };
  area.addEventListener("input", fit);
  requestAnimationFrame(fit);
  return area;
}

function field(label, key, opts = {}) {
  const value = S.editing[key] ?? "";
  const onInput = (e) => { S.editing[key] = e.target.value; };
  const input = opts.area
    ? autosize(el("textarea", { oninput: onInput }, value))
    : el("input", { value, oninput: onInput });
  return el("div", { class: "field" }, el("label", {}, label), input);
}

function renderEditor() {
  const card = S.editing;
  const panel = $("#panel");
  panel.textContent = "";

  const typeSel = el("select", { onchange: (e) => { S.editing.type = e.target.value; renderEditor(); } },
    Object.entries(TYPE_LABEL).map(([k, v]) => el("option", { value: k, selected: card.type === k }, v)));
  const artSel = el("select", { onchange: (e) => { S.editing.article = e.target.value; renderEditor(); } },
    el("option", { value: "" }, "—"),
    ["der", "die", "das"].map((a) => el("option", { value: a, selected: card.article === a }, a)));

  const children = [
    el("h3", {}, card.headword || card.lemma),
    el("div", { class: "sub" }, `id: ${card.id} · Quelle: ${card.source || "manual"}`),
    card.type === "noun"
      ? el("div", { class: "two" },
          el("div", { class: "field" }, el("label", {}, "Typ"), typeSel),
          el("div", { class: "field" }, el("label", {}, "Artikel"), artSel))
      : el("div", { class: "field" }, el("label", {}, "Typ"), typeSel),
    field("Stichwort", "lemma"),
    card.type === "noun" ? field("Plural", "plural") : null,
    card.type === "verb" ? el("div", { class: "two" },
      field("Präsens (3. Sg.)", "praesens_3sg"), field("Präteritum", "praeteritum")) : null,
    card.type === "verb" ? el("div", { class: "two" },
      field("Perfekt", "perfekt"), field("Hilfsverb", "aux")) : null,
    field("Rektion", "rection"),
    field(card.type === "grammar" ? "Regel (Deutsch)" : "Definition (Deutsch)", "definition", { area: true }),
    card.type === "grammar" ? null : field("Beispielsatz", "example", { area: true }),
    el("div", { class: "field" },
      el("label", {}, card.type === "grammar" ? "Beispielsätze (einer pro Zeile)"
                                              : "Weitere Beispielsätze (einer pro Zeile)"),
      autosize(el("textarea", {
        oninput: (e) => {
          S.editing.examples = e.target.value.split("\n").map((x) => x.trim()).filter(Boolean);
        },
      }, (card.examples || []).join("\n")))),
    field("Notizen", "notes", { area: true }),
    el("div", { class: "field" }, el("label", {}, "Tags (Komma-getrennt)"),
      el("input", {
        value: (card.tags || []).join(", "),
        oninput: (e) => { S.editing.tags = e.target.value.split(",").map((t) => t.trim()).filter(Boolean); },
      })),
    el("div", { class: "srsbox" },
      el("div", {}, `Erkennen — Status: ${card.srs.state} · Intervall: ${card.srs.interval_days} T. · `
        + `Ease: ${card.srs.ease} · ${card.srs.reps}× · Fehler: ${card.srs.lapses}`),
      card.supports_reverse
        ? el("div", {}, `Produktion — Status: ${card.srs_reverse.state} · `
            + `Intervall: ${card.srs_reverse.interval_days} T. · ${card.srs_reverse.reps}×`
            + (card.srs_reverse.reps || card.srs.interval_days >= 3 ? "" : " (noch gesperrt)"))
        : el("div", {}, "Nur eine Richtung — Grammatikkarten werden nicht produziert.")),
    el("div", { class: "panelactions" },
      el("button", { class: "primary", onclick: saveEditor }, "Speichern"),
      el("button", { id: "regen", onclick: () => regenerate(card.id, true) },
        card.type === "grammar" ? "Neue Beispiele" : "Neuer Satz"),
      el("button", { onclick: () => resetCard(card.id) }, "Fortschritt zurücksetzen"),
      el("button", { class: "danger", onclick: () => deleteCard(card.id) }, "Löschen"),
      el("button", { onclick: closeEditor }, "Schließen")),
  ];
  children.filter(Boolean).forEach((child) => panel.append(child));
}

async function saveEditor() {
  const card = S.editing;
  try {
    await api("/api/cards/" + encodeURIComponent(card.id), { method: "PATCH", body: card });
    toast("Gespeichert.");
    closeEditor();
    await Promise.all([loadBrowse(), refreshQueue(), loadAllCards()]);
  } catch (err) { toast(err.message, true); }
}

async function regenerate(cardId, inEditor = false) {
  const button = inEditor ? $("#regen") : null;
  if (button) { button.disabled = true; button.textContent = "…"; }
  try {
    const res = await api(`/api/cards/${encodeURIComponent(cardId)}/sentence`, { method: "POST" });
    const shown = res.card.example || (res.card.examples || []).join("  ·  ");
    toast("Neu: " + shown);
    if (S.editing && S.editing.id === cardId) {
      S.editing.example = res.card.example;
      S.editing.examples = res.card.examples || [];
      renderEditor();
    }
    const inQueue = S.queue.find((c) => c.id === cardId);
    if (inQueue) { inQueue.example = res.card.example; inQueue.examples = res.card.examples || []; }
    if (S.view === "review") renderReview();
    loadBrowse();
  } catch (err) {
    toast(err.message, true, 9000);
    if (button) { button.disabled = false; button.textContent = "Neuer Satz"; }
    return;
  }
}

async function resetCard(cardId) {
  await api(`/api/cards/${encodeURIComponent(cardId)}/reset`, { method: "POST" });
  toast("Fortschritt zurückgesetzt.");
  closeEditor();
  await Promise.all([loadBrowse(), refreshQueue(), loadAllCards()]);
}

async function deleteCard(cardId) {
  if (!confirmish(`Karte wirklich löschen?`)) return;
  await api("/api/cards/" + encodeURIComponent(cardId), { method: "DELETE" });
  toast("Gelöscht.");
  closeEditor();
  await Promise.all([loadBrowse(), refreshQueue(), loadAllCards()]);
}

/* window.confirm blocks; keep it but wrapped so it is easy to swap out later */
function confirmish(message) { return window.confirm(message); }

/* ----------------------------------------------------------------- stats */
async function loadAllCards() {
  S.allCards = (await api("/api/cards")).cards;
}

function renderStats() {
  const view = $("#view");
  view.textContent = "";
  const st = S.state;
  if (!st) return;
  const p = st.projection;

  view.append(el("div", { class: "cards2" },
    stat(p.total, "Karten gesamt"),
    stat(p.unseen, "noch nie gesehen"),
    stat(p.mature, "gefestigt (≥3 T.)"),
    stat(p.days_left === null || p.days_left === undefined ? "—" : Math.max(0, Math.round(p.days_left)), "Tage bis zum Ziel")));

  const byColor = {};
  (S.allCards || S.browse.cards).forEach((c) => { byColor[c.color_key] = (byColor[c.color_key] || 0) + 1; });
  const max = Math.max(1, ...Object.values(byColor));
  const labels = { der: "der", die: "die", das: "das", verb: "Verben", adjective: "Adjektive",
                   adverb: "Adverbien", phrase: "Wendungen", grammar: "Grammatik", other: "Sonstiges" };
  const bars = el("div", { class: "bars" });
  COLOR_KEYS.filter((k) => byColor[k]).forEach((k) => {
    bars.append(el("div", { class: "bar", style: colorVar(k) },
      el("span", {}, labels[k] || k),
      el("span", { class: "track" }, el("span", { class: "fill", style: `width:${(byColor[k] / max) * 100}%` })),
      el("span", {}, String(byColor[k]))));
  });
  view.append(el("div", { class: "section" }, el("h4", {}, "Verteilung"), bars,
    el("div", { class: "note" }, "Farben: der = blau, die = rosa, das = grün, Verben = orange, Adjektive = violett, Grammatik = grau.")));

  const settings = st.settings || {};
  view.append(el("div", { class: "section" }, el("h4", {}, "Planung"),
    el("table", { class: "helptable" },
      row("Zieldatum", settings.target_date || "nicht gesetzt", "set target 2026-09-18"),
      row("Wiederholungen bis dahin", String(settings.reviews_before_target), "set reviews 3"),
      row("Intervall-Obergrenze jetzt", `${p.interval_cap_days} Tage`, ""),
      row("Neue Karten pro Tag", String(settings.daily_new_limit || "unbegrenzt"), "set new 40"),
      row("Reihenfolge", settings.shuffle === false ? "fest" : "gemischt", "set shuffle on"),
      row("Produktion (Bedeutung → Wort)",
          settings.reverse_enabled === false ? "aus" : "an", "set reverse off"),
      row("Produktion startet ab", `${settings.reverse_unlock_interval_days} Tagen Intervall`,
          "set unlock 3"),
      row("Produktionskarten", `${p.reverse_open} von ${p.reverse_possible} freigeschaltet, `
          + `${p.reverse_started} begonnen`, ""),
      p.new_per_day_needed ? row("Nötig, um alles anzufangen", `${p.new_per_day_needed} neue Karten/Tag`, "") : null),
    el("div", { class: "note" },
      "Die Obergrenze ergibt sich aus (Tage bis zum Ziel ÷ gewünschte Wiederholungen). "
      + "Dadurch wird keine Karte über das Zieldatum hinaus geschoben. "
      + "Jede Vokabel wird in zwei Richtungen geplant; die Produktionsrichtung kommt erst dazu, "
      + "wenn du das Wort sicher wiedererkennst.")));

  view.append(el("div", { class: "section" }, el("h4", {}, "Deck-Datei"),
    el("div", { class: "note" }, st.deck_path),
    el("div", { class: "panelactions" },
      el("button", { onclick: exportDeck }, "Als YAML herunterladen"),
      el("button", { onclick: importDeck }, "YAML importieren"))));

  if (S.help) {
    const table = el("table", { class: "helptable" });
    S.help.forEach(([cmd, desc]) => table.append(el("tr", {}, el("td", {}, el("code", {}, cmd)), el("td", {}, desc))));
    view.append(el("div", { class: "section" }, el("h4", {}, "Befehle"), table));
  }
}

const stat = (n, label) => el("div", { class: "stat" }, el("div", { class: "n" }, String(n)), el("div", { class: "l" }, label));
const row = (a, b, c) => el("tr", {}, el("td", {}, a), el("td", {}, b, c ? el("span", {}, "  ", el("code", {}, c)) : null));

/* ------------------------------------------------------- import / export */
function exportDeck() { window.location.href = "/api/export"; }

function importDeck() {
  const input = el("input", { type: "file", accept: ".yaml,.yml,.txt" });
  input.addEventListener("change", async () => {
    const file = input.files[0];
    if (!file) return;
    const text = await file.text();
    const mode = confirmish("Vorhandene Karten ersetzen? (Abbrechen = zusammenführen)") ? "replace" : "merge";
    try {
      const res = await api("/api/import", { method: "POST", body: { yaml: text, mode } });
      toast(`${res.added} importiert, ${res.skipped} übersprungen.`);
      S.state = res.state;
      await Promise.all([loadBrowse(), refreshQueue(), loadAllCards()]);
      renderStatus();
    } catch (err) { toast(err.message, true); }
  });
  input.click();
}

/* -------------------------------------------------------------- commands */
async function runCommand(text) {
  const input = $("#cmd");
  input.disabled = true;
  const busy = el("div", { class: "toast" }, el("span", { class: "spin" }), el("span", {}, "…"));
  $("#toasts").append(busy);
  try {
    const res = await api("/api/command", { method: "POST", body: { text } });
    busy.remove();
    if (res.state) { S.state = res.state; renderStatus(); }
    await handleCommandResult(res);
    input.value = "";
  } catch (err) {
    busy.remove();
    toast(err.message, true);
  } finally {
    input.disabled = false;
    input.focus();
  }
}

async function handleCommandResult(res) {
  switch (res.action) {
    case "error":
      toast(res.message, true); break;
    case "help":
      S.help = res.entries; await loadAllCards(); setView("stats"); break;
    case "review":
      await refreshQueue(); setView("review"); break;
    case "browse":
      await loadBrowse(); setView("browse"); break;
    case "stats":
      await Promise.all([loadBrowse(), loadAllCards()]); setView("stats"); break;
    case "export":
      exportDeck(); break;
    case "find":
      S.browse.cards = res.cards; S.browse.q = res.query; setView("browse");
      if ($("#browsesearch")) $("#browsesearch").value = res.query;
      toast(`${res.cards.length} Treffer für „${res.query}“`); break;
    case "edit":
      S.editing = res.card; renderEditor(); $("#drawer").hidden = false; break;
    case "added":
      toast(cardSummary(res.card, "angelegt"));
      if (res.warning) toast(res.warning, true, 9000);
      await Promise.all([loadBrowse(), refreshQueue(), loadAllCards()]);
      S.editing = res.card; renderEditor(); $("#drawer").hidden = false;
      break;
    case "duplicate":
      toast(res.message); S.editing = res.card; renderEditor(); $("#drawer").hidden = false; break;
    case "updated":
      toast(cardSummary(res.card, "aktualisiert"));
      await Promise.all([loadBrowse(), refreshQueue(), loadAllCards()]); break;
    case "deleted":
      toast(res.message); await Promise.all([loadBrowse(), refreshQueue()]); break;
    case "settings":
      toast(res.message); if (S.view === "stats") renderStats(); await refreshQueue(); break;
    case "noop": break;
    default:
      if (res.message) toast(res.message);
  }
}

function cardSummary(card, verb) {
  const bits = [card.headword];
  if (card.plural) bits.push("Pl. " + card.plural);
  if (card.perfekt) bits.push(card.praeteritum + " / " + card.perfekt);
  return `${bits.join(" · ")} ${verb}`;
}

/* ------------------------------------------------------------------ view */
function setView(view) {
  S.view = view;
  document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("active", b.dataset.view === view));
  if (view === "review") renderReview();
  if (view === "browse") renderBrowse();
  if (view === "stats") renderStats();
}

/* ------------------------------------------------------------- listeners */
document.addEventListener("DOMContentLoaded", async () => {
  $("#cmd").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && $("#cmd").value.trim()) runCommand($("#cmd").value.trim());
  });
  document.querySelectorAll("#tabs button").forEach((b) => {
    b.addEventListener("click", async () => {
      if (b.dataset.view === "browse") await loadBrowse();
      if (b.dataset.view === "stats") await Promise.all([loadBrowse(), loadAllCards()]);
      if (b.dataset.view === "review") await refreshQueue();
      setView(b.dataset.view);
    });
  });
  $("#drawer").addEventListener("click", (e) => { if (e.target.id === "drawer") closeEditor(); });

  document.addEventListener("keydown", (e) => {
    const typing = ["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName);
    if (e.key === "Escape") { if (!$("#drawer").hidden) closeEditor(); else $("#cmd").blur(); return; }
    if (!typing && e.key === "/") { e.preventDefault(); $("#cmd").focus(); return; }
    if (typing || !$("#drawer").hidden) return;
    if (S.view !== "review") return;
    if (e.key === " " || e.key === "Enter") { e.preventDefault(); S.revealed ? answer(2) : reveal(); }
    else if (["1", "2", "3", "4"].includes(e.key) && S.revealed) answer(Number(e.key) - 1);
    else if (e.key === "e" && S.queue[S.index]) openEditor(S.queue[S.index].id);
  });

  await refreshState();
  await Promise.all([refreshQueue(), loadBrowse(), loadAllCards()]);
  setView("review");
});
