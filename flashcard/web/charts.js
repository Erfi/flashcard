/* Small SVG chart kit — no dependencies, no build step.

   Marks follow one set of specs everywhere: bars capped at 24px with a 4px
   rounded data-end and a 2px surface gap between neighbours, 2px lines, end
   markers with a 2px surface ring, hairline solid gridlines. Every chart ships
   a table view, and every chart with two series ships a legend, so nothing is
   carried by colour alone. */

const SVGNS = "http://www.w3.org/2000/svg";
const BAR_MAX = 24;
const GAP = 2;                    // the surface gap between touching marks
const TICK_PX = 6.4;              // ~width of a digit at the 11px axis size

/* Padding is measured, not guessed: the y-axis gutter fits its widest tick and
   the right margin fits the end labels, so nothing is cramped or clipped. */
function padding({ yLabels = [], endLabels = [] } = {}) {
  const widest = (list) => list.reduce((max, t) => Math.max(max, String(t).length), 0) * TICK_PX;
  return {
    top: 20,
    right: endLabels.length ? Math.ceil(widest(endLabels)) + 18 : 16,
    bottom: 30,
    left: Math.ceil(Math.max(24, widest(yLabels))) + 12,
  };
}

/* fill and stroke go through the style attribute, not the presentation
   attribute of the same name. Browsers disagree about resolving var() inside
   presentation attributes — where it is unsupported the declaration is invalid
   and the mark falls back to its initial value, which paints every bar black.
   A style attribute is plain CSS and resolves custom properties everywhere. */
const PAINT = new Set(["fill", "stroke"]);

function sv(tag, attrs = {}, ...kids) {
  const node = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (PAINT.has(k) && String(v).includes("var(")) node.style.setProperty(k, String(v));
    else node.setAttribute(k, String(v));
  }
  kids.flat().forEach((kid) => kid && node.append(kid));
  return node;
}

function hv(tag, attrs = {}, ...kids) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, String(v));
  }
  kids.flat().forEach((kid) => {
    if (kid === null || kid === undefined || kid === false) return;
    node.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  });
  return node;
}

const dayLabel = (iso) => {
  const d = new Date(iso + "T12:00:00");
  return `${d.getDate()}.${d.getMonth() + 1}.`;
};
/* A scale whose ticks are whole, readable numbers: the step is 1/2/2.5/5/10 x a
   power of ten, and the top is a multiple of it — so no axis ever reads 37.5. */
function niceScale(maxValue) {
  const value = Math.max(1, maxValue);
  // whole-number steps only — these axes count cards, and a gridline at 2.5
  // cards is a lie. Try 4 ticks, then 5, and keep the tighter fit.
  const candidates = [4, 5, 6].map((ticks) => {
    const raw = value / ticks;
    const magnitude = Math.pow(10, Math.floor(Math.log10(raw)));
    const step = Math.max(1,   // never a fractional gridline: these axes count cards
      [1, 2, 5, 10].map((m) => m * magnitude).find((s) => s >= raw) || magnitude * 10);
    return { top: step * ticks, step, ticks };
  });
  return candidates.reduce((best, c) => (c.top < best.top ? c : best));
}
const niceMax = (value) => niceScale(value).top;

/* ------------------------------------------------------------------ shell */

export function chartCard({ title, subtitle, note, legend = [], table }) {
  const body = hv("div", { class: "chartbody" });
  const tableHost = hv("div", { class: "charttable", hidden: "hidden" });
  const legendRow = legend.length > 1
    ? hv("div", { class: "chartlegend" }, legend.map((item) =>
        hv("span", { class: "legenditem" },
          hv("span", { class: "swatch", style: `background:${item.color}` }), item.label)))
    : null;

  let showingTable = false;
  const toggle = hv("button", {
    class: "charttoggle",
    onclick: () => {
      showingTable = !showingTable;
      body.hidden = showingTable;
      tableHost.hidden = !showingTable;
      toggle.textContent = showingTable ? "Diagramm" : "Tabelle";
      if (showingTable && !tableHost.firstChild && table) tableHost.append(table());
    },
  }, "Tabelle");

  const card = hv("div", { class: "section chartcard" },
    hv("div", { class: "charthead" },
      hv("div", {}, hv("h4", {}, title),
        subtitle ? hv("div", { class: "chartsub" }, subtitle) : null),
      toggle),
    legendRow, body, tableHost,
    note ? hv("div", { class: "note" }, note) : null);
  return { card, body };
}

export function dataTable(headers, rows) {
  return hv("table", { class: "helptable datatable" },
    hv("thead", {}, hv("tr", {}, headers.map((h) => hv("th", {}, h)))),
    hv("tbody", {}, rows.map((r) => hv("tr", {}, r.map((cell) => hv("td", {}, cell))))));
}

/* ---------------------------------------------------------------- tooltip */

function attachTooltip(host, svg) {
  const tip = hv("div", { class: "charttip", hidden: "hidden" });
  host.append(tip);
  const show = (html, x, y) => {
    tip.textContent = "";
    tip.append(html);
    tip.hidden = false;
    const box = host.getBoundingClientRect();
    const width = tip.offsetWidth;
    tip.style.left = Math.max(4, Math.min(x - width / 2, box.width - width - 4)) + "px";
    tip.style.top = Math.max(0, y - tip.offsetHeight - 10) + "px";
  };
  const hide = () => { tip.hidden = true; };
  svg.addEventListener("mouseleave", hide);
  return { show, hide };
}

const tipBody = (title, lines) => hv("div", {},
  hv("div", { class: "tiptitle" }, title),
  lines.map(([label, value, color]) => hv("div", { class: "tiprow" },
    color ? hv("span", { class: "swatch", style: `background:${color}` }) : null,
    hv("span", {}, label), hv("b", {}, String(value)))));

/* ------------------------------------------------------------ line chart */

export function lineChart(host, opts) {
  const { series, labels, yMax, yFormat = (v) => v, band, height = 210 } = opts;
  const width = Math.max(320, host.clientWidth || 640);
  const scale = yMax ? { top: yMax, step: yMax / 4, ticks: 4 }
    : niceScale(Math.max(1, ...series.flatMap((s) => s.points.filter((p) => p !== null && p !== undefined))));
  const top = scale.top;
  const lastValues = series
    .map((s) => s.points.reduce((acc, v) => (v === null || v === undefined ? acc : v), null))
    .filter((v) => v !== null)
    .map((v) => yFormat(v));
  const PAD = padding({
    yLabels: Array.from({ length: scale.ticks + 1 }, (_, t) => yFormat(scale.step * t)),
    endLabels: lastValues,
  });
  const plotW = width - PAD.left - PAD.right;
  const plotH = height - PAD.top - PAD.bottom;
  const x = (i) => PAD.left + (labels.length === 1 ? plotW / 2 : (i * plotW) / (labels.length - 1));
  const y = (v) => PAD.top + plotH - (v / top) * plotH;

  const svg = sv("svg", { viewBox: `0 0 ${width} ${height}`, width: "100%", height,
                          role: "img", "aria-label": opts.ariaLabel || "" });

  if (band) {
    svg.append(sv("rect", { x: PAD.left, y: y(band[1]), width: plotW,
                            height: Math.max(1, y(band[0]) - y(band[1])),
                            fill: "var(--viz-band)" }));
    band.forEach((edge) => svg.append(sv("line", {
      x1: PAD.left, x2: PAD.left + plotW, y1: y(edge), y2: y(edge),
      stroke: "var(--viz-band-edge)", "stroke-width": 1 })));
  }
  for (let t = 0; t <= scale.ticks; t++) {
    const value = scale.step * t;
    svg.append(sv("line", { x1: PAD.left, x2: PAD.left + plotW, y1: y(value), y2: y(value),
                            stroke: "var(--viz-grid)", "stroke-width": 1 }));
    svg.append(sv("text", { x: PAD.left - 8, y: y(value) + 4, "text-anchor": "end",
                            class: "axis" }, document.createTextNode(yFormat(value))));
  }
  const every = Math.ceil(labels.length / 7);
  labels.forEach((label, i) => {
    if (i % every && i !== labels.length - 1) return;
    svg.append(sv("text", { x: x(i), y: height - 10, "text-anchor": "middle", class: "axis" },
                  document.createTextNode(dayLabel(label))));
  });

  const ends = [];
  series.forEach((s) => {
    let path = "", started = false;
    s.points.forEach((value, i) => {
      if (value === null || value === undefined) { started = false; return; }
      path += `${started ? "L" : "M"}${x(i)} ${y(value)}`;
      started = true;
    });
    if (path) {
      svg.append(sv("path", { d: path, fill: "none", stroke: s.color, "stroke-width": 2,
                              "stroke-linejoin": "round", "stroke-linecap": "round" }));
      const lastIndex = s.points.reduce((acc, v, i) => (v === null ? acc : i), -1);
      if (lastIndex >= 0) {
        svg.append(sv("circle", { cx: x(lastIndex), cy: y(s.points[lastIndex]), r: 4,
                                  fill: s.color, stroke: "var(--surface)", "stroke-width": GAP }));
        ends.push({ x: x(lastIndex), y: y(s.points[lastIndex]),
                    text: yFormat(s.points[lastIndex]) });
      }
    }
  });

  // nudge apart only if two labels would overlap; they stay beside their own line
  ends.sort((a, b) => a.y - b.y);
  ends.forEach((end, i) => {
    if (i && end.y - ends[i - 1].y < 13) end.y = ends[i - 1].y + 13;
    svg.append(sv("text", { x: end.x + 10, y: end.y + 4, "text-anchor": "start",
                            class: "endlabel" }, document.createTextNode(end.text)));
  });

  const tooltip = attachTooltip(host, svg);
  const cross = sv("line", { y1: PAD.top, y2: PAD.top + plotH, stroke: "var(--viz-axis)",
                             "stroke-width": 1, opacity: 0 });
  svg.append(cross);
  svg.addEventListener("mousemove", (event) => {
    const box = svg.getBoundingClientRect();
    const scale = width / box.width;
    const i = Math.round((((event.clientX - box.left) * scale) - PAD.left) / (plotW / Math.max(1, labels.length - 1)));
    const index = Math.max(0, Math.min(labels.length - 1, i));
    cross.setAttribute("x1", x(index)); cross.setAttribute("x2", x(index));
    cross.setAttribute("opacity", 0.5);
    tooltip.show(tipBody(dayLabel(labels[index]),
      series.map((s) => [s.label, s.points[index] === null ? "—" : yFormat(s.points[index]), s.color])),
      (x(index) / scale), (PAD.top + plotH) / scale);
  });
  svg.addEventListener("mouseleave", () => cross.setAttribute("opacity", 0));
  host.append(svg);
}

/* ---------------------------------------------------------- column chart */

export function columnChart(host, opts) {
  const { labels, stacks, colors, height = 210, tipRows, xLabel = dayLabel,
          markIndex = -1, markLabel = "" } = opts;
  const width = Math.max(320, host.clientWidth || 640);
  const totals = stacks.map((parts) => parts.reduce((a, b) => a + b, 0));
  const scale = niceScale(Math.max(1, ...totals));
  const top = scale.top;
  const PAD = padding({
    yLabels: Array.from({ length: scale.ticks + 1 }, (_, t) => Math.round(scale.step * t)),
  });
  const plotW = width - PAD.left - PAD.right;
  const plotH = height - PAD.top - PAD.bottom;
  const band = plotW / labels.length;
  const barW = Math.min(BAR_MAX, band - 6);
  const y = (v) => PAD.top + plotH - (v / top) * plotH;

  const svg = sv("svg", { viewBox: `0 0 ${width} ${height}`, width: "100%", height,
                          role: "img", "aria-label": opts.ariaLabel || "" });
  for (let t = 0; t <= scale.ticks; t++) {
    const value = scale.step * t;
    svg.append(sv("line", { x1: PAD.left, x2: PAD.left + plotW, y1: y(value), y2: y(value),
                            stroke: "var(--viz-grid)", "stroke-width": 1 }));
    svg.append(sv("text", { x: PAD.left - 8, y: y(value) + 4, "text-anchor": "end",
                            class: "axis" }, document.createTextNode(String(Math.round(value)))));
  }

  const tooltip = attachTooltip(host, svg);
  labels.forEach((label, i) => {
    const cx = PAD.left + band * i + band / 2;
    if (markIndex === i) {
      svg.append(sv("line", { x1: cx + band / 2, x2: cx + band / 2, y1: PAD.top,
                              y2: PAD.top + plotH, stroke: "var(--viz-axis)", "stroke-width": 1 }));
      svg.append(sv("text", { x: cx + band / 2 - 4, y: PAD.top + 10, "text-anchor": "end",
                              class: "axis" }, document.createTextNode(markLabel)));
    }
    let cursor = 0;
    stacks[i].forEach((value, part) => {
      if (value <= 0) { cursor += value; return; }
      const yTop = y(cursor + value);
      const yBottom = y(cursor);
      const isTop = stacks[i].slice(part + 1).every((v) => v <= 0);
      const h = Math.max(1, yBottom - yTop - (isTop ? 0 : GAP));
      svg.append(sv("rect", { x: cx - barW / 2, y: yTop, width: barW, height: h,
                              rx: isTop ? 4 : 0, fill: colors[part] }));
      if (!isTop) {   // square off the bottom of a rounded segment
        svg.append(sv("rect", { x: cx - barW / 2, y: yTop + 4, width: barW,
                                height: Math.max(0, h - 4), fill: colors[part] }));
      }
      cursor += value;
    });
    const hit = sv("rect", { x: PAD.left + band * i, y: PAD.top, width: band, height: plotH,
                             fill: "transparent" });
    hit.addEventListener("mousemove", () => tooltip.show(
      tipBody(xLabel(label), tipRows(i)), cx, y(totals[i])));
    svg.append(hit);
    const every = Math.ceil(labels.length / 7);
    if (!(i % every) || i === labels.length - 1) {
      svg.append(sv("text", { x: cx, y: height - 10, "text-anchor": "middle", class: "axis" },
                    document.createTextNode(xLabel(label))));
    }
  });
  host.append(svg);
}

/* ------------------------------------------------------- category columns */

export function categoryChart(host, opts) {
  const { labels, values, colors, height = 200 } = opts;
  const width = Math.max(320, host.clientWidth || 640);
  const PAD = padding();
  const plotW = width - PAD.left - PAD.right;
  const plotH = height - PAD.top - PAD.bottom - 6;
  const top = niceScale(Math.max(1, ...values)).top;
  const band = plotW / labels.length;
  const barW = Math.min(BAR_MAX * 1.6, band - 12);
  const y = (v) => PAD.top + plotH - (v / top) * plotH;

  const svg = sv("svg", { viewBox: `0 0 ${width} ${height}`, width: "100%", height,
                          role: "img", "aria-label": opts.ariaLabel || "" });
  svg.append(sv("line", { x1: PAD.left, x2: PAD.left + plotW, y1: y(0), y2: y(0),
                          stroke: "var(--viz-axis)", "stroke-width": 1 }));
  labels.forEach((label, i) => {
    const cx = PAD.left + band * i + band / 2;
    const value = values[i];
    if (value > 0) {
      svg.append(sv("rect", { x: cx - barW / 2, y: y(value), width: barW,
                              height: Math.max(2, y(0) - y(value)), rx: 4, fill: colors[i] }));
    }
    svg.append(sv("text", { x: cx, y: y(value) - 7, "text-anchor": "middle", class: "endlabel" },
                  document.createTextNode(String(value))));
    svg.append(sv("text", { x: cx, y: height - 10, "text-anchor": "middle", class: "axis" },
                  document.createTextNode(label)));
  });
  host.append(svg);
}

export { dayLabel };
