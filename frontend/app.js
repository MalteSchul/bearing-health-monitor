"use strict";

// Display only: every value shown is computed by the API, nothing is judged here.

const API = "/api/v1";

// Colour only where something needs doing, as in ISA-101 operator screens: amber plan the
// replacement, red act now. Cards and the rig light stay grey otherwise, so an alarm stands out.
// Crosstalk's band is green: the index is high, but this bearing needs nothing.
// band: legend name of the shading behind the health index, null for none. Shades get stronger
// with urgency, so on a washed-out projector danger still stands out and learning recedes.
const STATUS = {
  baseline: { label: "Learning", band: "learning", colour: "#8a8983", shade: "rgba(138, 137, 131, 0.13)" },
  ok: { label: "OK", band: null, colour: "#2e9d5b", shade: null },
  crosstalk: { label: "OK", band: "crosstalk", colour: "#2e9d5b", shade: "rgba(46, 157, 91, 0.24)" },
  alert: { label: "Alert", band: "alert", colour: "#d99a00", shade: "rgba(217, 154, 0, 0.28)" },
  danger: { label: "Danger", band: "danger", colour: "#d03b3b", shade: "rgba(208, 59, 59, 0.32)" },
};
// What the number on a card means, shown on hovering its label.
const INDEX_MEANING = "The bearing's largest vibration feature, relative to its own first 24 h";
// The alarms, with what each asks of the operator.
const ACTIONS = { alert: "Plan the replacement", danger: "Reduce load or stop" };

const ENVELOPE = {
  env_bpfo: { label: "outer race (BPFO)", colour: "#2a78d6" },
  env_bpfi: { label: "inner race (BPFI)", colour: "#eb6834" },
  env_bsf: { label: "roller (BSF)", colour: "#1baf7a" },
  env_ftf: { label: "cage (FTF)", colour: "#eda100" },
};
// rms decides danger, so it takes the darkest slot, a wider line and the top layer. The pink
// (below 3:1 on white) goes to crest factor, which is only peak over rms.
const TIME_DOMAIN = {
  rms: { label: "rms", colour: "#4a3aa7", width: 2 },
  peak: { label: "peak", colour: "#008300" },
  crest_factor: { label: "crest factor", colour: "#e87ba4" },
  kurtosis: { label: "kurtosis", colour: "#e34948" },
};
const FEATURES = { ...ENVELOPE, ...TIME_DOMAIN };

const HOUR_MS = 60 * 60 * 1000;
// Drawing only: a longer step means the rig stood still, so the line must not bridge it.
const GAP_MS = HOUR_MS / 2;
// Only long stops are cut out of the axis: cutting short ones crowds the date labels together.
const CUT_MS = 12 * HOUR_MS;
const FINAL_PHASE_MS = 100 * HOUR_MS;

const INK = "#1f1f1d";
const MUTED = "#76756f";
const GRID = "#ecebe6";
const STOP = "#9d9b93";
const FONT = "system-ui, -apple-system, 'Segoe UI', sans-serif";
const CONFIG = {
  responsive: true,
  displaylogo: false,
  // Plotly's own pop-up tips cover the run selector; the chart has its own hint.
  showTips: false,
  modeBarButtonsToRemove: ["select2d", "lasso2d", "autoScale2d"],
};
// Top to bottom: health index, envelope features, time-domain features.
const DOMAINS = [
  [0.66, 1],
  [0.34, 0.58],
  [0, 0.24],
];
// Both charts keep the same side margins, so a moment sits at the same x in each.
const MARGIN = { l: 52, r: 190, t: 34, b: 36 };
// Overview lanes in pixels, so every run's overview looks alike whatever its bearing count.
const LANE_PX = 80;
const LANE_GAP_PX = 30;
// A replay lasts about this long whatever the run's length: it skips snapshots on long runs.
const PLAY_MS = 30_000;
const FRAME_MS = 100;
// The page background, translucent: what came later stays visible, but steps back.
const VEIL = "rgba(246, 245, 241, 0.78)";

// bearings: each bearing's condition at the end of the run, which the charts mark.
// cards: each bearing's condition as of the moment shown, which the cards report.
// machine: the rig's condition as of the moment shown, its worst bearing's.
// health: each bearing's health-index series of the current run, which the overview draws.
// evaluation: the run's verdicts against the documented end, hindsight whatever the moment.
// bearing: the one shown in detail and in the copilot's focus, null for the whole rig.
// moment: index of the snapshot shown, null for the latest.
// range: null for the whole run, "final" for its last 100 h, or [from, to] dragged in a chart.
// asked: question, run, moment and bearing of the copilot answer on show, null before the first.
// hindsight: whether the documented outcome is shown; off, the page is what an operator saw.
// features: whether the bearing detail shows the features behind its health index.
const state = {
  run: null,
  last: null,
  bearing: null,
  bearings: [],
  cards: [],
  machine: null,
  health: {},
  evaluation: null,
  moment: null,
  range: null,
  asked: null,
  hindsight: false,
  features: false,
};

const $ = (id) => document.getElementById(id);

async function getJSON(path, signal = undefined) {
  const response = await fetch(path, { signal });
  if (!response.ok) {
    throw new Error(`${path}: ${response.status} ${response.statusText}`);
  }
  return response.json();
}

/**
 * A click's work with any failure shown, and the page marked busy meanwhile. Replay steps are not
 * marked: dimming the page ten times a second would flicker.
 */
function handle(task, busy = true) {
  return async () => {
    $("error").hidden = true;
    if (busy) document.body.classList.add("loading");
    try {
      await task();
    } catch (error) {
      $("error").textContent = `Could not load data. ${error.message}`;
      $("error").hidden = false;
    } finally {
      if (busy) document.body.classList.remove("loading");
    }
  };
}

// The dataset's timestamps have no time zone. Parsed as UTC and printed as text, so the browser's
// zone and its daylight-saving jumps never apply.
const formatTime = (iso) => iso.slice(0, 16).replace("T", " ");
const millis = (iso) => Date.parse(`${iso}Z`);
const isoAt = (ms) => new Date(ms).toISOString().slice(0, 19);
const runLabel = (name) => name.replace("set", "Set ");

// --- run selector -----------------------------------------------------------------------------

function renderRuns(experiments) {
  $("runs").replaceChildren(
    ...experiments.map((experiment) => {
      const button = document.createElement("button");
      button.textContent = runLabel(experiment.name);
      button.title =
        `${formatTime(experiment.first)} to ${formatTime(experiment.last)} · ` +
        `${experiment.snapshots.toLocaleString("en")} one-second vibration snapshots`;
      button.dataset.run = experiment.name;
      button.addEventListener("click", handle(() => selectRun(experiment.name)));
      return button;
    }),
  );
}

/** The view in the address, so it can be bookmarked and shared. */
function remember() {
  const params = new URLSearchParams({ run: state.run });
  if (state.bearing !== null) params.set("bearing", state.bearing);
  if (state.moment !== null) params.set("at", runTimes()[state.moment]);
  if (state.hindsight) params.set("hindsight", "1");
  if (state.features) params.set("features", "1");
  history.replaceState(null, "", `?${params}`);
}

/** The run's snapshot times: every bearing is recorded at each of them. */
const runTimes = () => state.health[state.bearings[0].bearing].timestamps;

async function selectRun(name, bearing = state.bearing, at = null) {
  playing = false;
  momentRequest?.abort();
  const experiment = await getJSON(`${API}/experiments/${name}`);
  const [evaluation, ...series] = await Promise.all([
    getJSON(`${API}/experiments/${name}/evaluation`),
    ...experiment.bearings.map((b) => getJSON(`${API}/experiments/${name}/bearings/${b.bearing}/health-index`)),
  ]);
  // "Final 100 h" means the same in every run; a dragged range does not.
  if (name !== state.run && state.range !== "final") state.range = null;
  state.run = name;
  state.last = experiment.last;
  state.bearings = experiment.bearings;
  state.health = Object.fromEntries(series.map((health) => [health.bearing, health]));
  state.evaluation = evaluation;
  renderResult();

  // A new run starts at its end unless the address asks for a moment.
  const times = runTimes();
  const last = times.length - 1;
  const index = at === null ? last : Math.max(0, times.findLastIndex((time) => time <= at));
  state.moment = index >= last ? null : index;
  const shown =
    state.moment === null
      ? experiment
      : await getJSON(`${API}/experiments/${name}?at=${encodeURIComponent(times[index])}`);
  state.cards = shown.bearings;
  state.machine = shown.machine;
  renderMachine();
  $("moment").max = String(last);
  $("moment").value = String(index);
  markMoment();
  markReply();
  for (const button of $("runs").children) {
    button.setAttribute("aria-pressed", String(button.dataset.run === name));
  }

  const numbers = experiment.bearings.map((b) => b.bearing);
  await selectBearing(numbers.includes(bearing) ? bearing : null);
}

// --- hindsight --------------------------------------------------------------------------------

const resultOf = (bearing) => state.evaluation.bearings.find((r) => r.bearing === bearing);
const capitalised = (text) => text[0].toUpperCase() + text.slice(1);

/** How early each level came, in operating hours before the run ended; null without an alert. */
function leads(result) {
  const parts = [];
  if (result.alert_lead_op_h !== null) parts.push(`alert ${Math.round(result.alert_lead_op_h)}`);
  if (result.danger_lead_op_h !== null) parts.push(`danger ${Math.round(result.danger_lead_op_h)}`);
  return parts.length ? `${parts.join(" · ")} op-h` : null;
}

const listed = (items) =>
  items.length < 2 ? items.join("") : `${items.slice(0, -1).join(", ")} and ${items.at(-1)}`;

/** The run's result in one line; each lane gives its bearing's. Stays put while the replay moves. */
function renderResult() {
  const run = state.evaluation;
  $("run-result").textContent =
    `Documented failures alerted: ${run.failures_alerted} of ${run.failures}` +
    ` · false alarms on survivors: ${run.false_alarms} of ${run.survivors}`;
}

// --- bearing cards ----------------------------------------------------------------------------

/**
 * A card's one line, only where there is something to say: what an alarm asks and which part, or
 * whose fault a high index comes from. When it was raised is in the chart.
 */
function cardLine(condition) {
  if (condition.status in ACTIONS) {
    const part = condition.diagnosis === null ? "" : ` · ${condition.diagnosis}`;
    return `<strong>${ACTIONS[condition.status]}</strong>${part}`;
  }
  if (condition.status === "crosstalk") {
    // Here the diagnosis is the part of the fault it hears, not one of its own.
    const fault = condition.diagnosis === null ? "fault" : `${condition.diagnosis} fault`;
    return `<span class="muted">Hears bearing ${condition.crosstalk_from}'s ${fault}</span>`;
  }
  return "";
}

/** A card's frame, built once per run. A click needs press and release on the same element. */
function buildCard(bearing) {
  const card = document.createElement("button");
  card.className = "card";
  card.dataset.bearing = String(bearing);
  // A toggle: pressing the selected card again releases it, back to the whole rig.
  card.addEventListener("click", handle(() => pickBearing(bearing === state.bearing ? null : bearing)));
  card.innerHTML = `
    <span class="card-head">
      <span class="name">Bearing ${bearing}</span>
      <span class="pill"></span>
    </span>
    <span class="index"><span class="value"></span><small title="${INDEX_MEANING}">health index ⓘ</small></span>
    <span class="facts"></span>`;
  return card;
}

// The markup last written into each element, so an unchanged part is left alone.
const written = new WeakMap();

function setMarkup(element, html) {
  if (written.get(element) === html) return;
  element.innerHTML = html;
  written.set(element, html);
}

function fillCard(card, summary) {
  const { condition } = summary;
  const status = STATUS[condition.status];
  const part = (name) => card.querySelector(`.${name}`);
  const alarm = condition.status in ACTIONS;
  card.style.setProperty("--status", status.colour);
  card.classList.toggle("alarm", alarm);
  card.setAttribute("aria-pressed", String(summary.bearing === state.bearing));
  part("pill").textContent = status.label;
  part("pill").classList.toggle("calm", !alarm);
  part("value").textContent = condition.index === null ? "–" : `${condition.index.toFixed(1)}×`;
  setMarkup(part("facts"), cardLine(condition));
}

/**
 * Updates the cards in place. A replay refreshes them ten times a second: a card rebuilt between
 * press and release would swallow the click.
 */
function renderCards() {
  const grid = $("bearings");
  const bearings = state.cards.map((summary) => String(summary.bearing));
  const built = [...grid.children].map((card) => card.dataset.bearing);
  if (grid.dataset.run !== state.run || built.join() !== bearings.join()) {
    grid.replaceChildren(...state.cards.map((summary) => buildCard(summary.bearing)));
    grid.dataset.run = state.run;
  }
  state.cards.forEach((summary, k) => fillCard(grid.children[k], summary));
}

// --- chart ------------------------------------------------------------------------------------

/** Copies of the series with a null wherever the rig stood still, so lines break there. */
function breakAtGaps(timestamps, series) {
  const x = [];
  const ys = series.map(() => []);
  timestamps.forEach((time, i) => {
    if (i > 0 && millis(time) - millis(timestamps[i - 1]) > GAP_MS) {
      x.push(null);
      ys.forEach((y) => y.push(null));
    }
    x.push(time);
    ys.forEach((y, k) => y.push(series[k][i]));
  });
  return { x, ys };
}

/** Long stops of the rig, cut out of the time axis: damage only grows while it runs. */
function longStops(timestamps) {
  const stops = [];
  for (let i = 1; i < timestamps.length; i += 1) {
    const ms = millis(timestamps[i]) - millis(timestamps[i - 1]);
    if (ms >= CUT_MS) stops.push({ from: timestamps[i - 1], to: timestamps[i], ms });
  }
  return stops;
}

const duration = (ms) =>
  ms >= 48 * HOUR_MS ? `${(ms / (24 * HOUR_MS)).toFixed(1)} days` : `${Math.round(ms / HOUR_MS)} h`;

/**
 * Contiguous stretches of one status, as shaded rectangles behind the health index. A status
 * holds until the next snapshot, across a stop too, as `?at=` answers; only the index line breaks
 * there, because nothing was measured.
 */
function statusShapes(timestamps, status, axis = "y") {
  const shapes = [];
  let first = 0;
  for (let i = 1; i <= status.length; i += 1) {
    const end = i === status.length;
    if (end || status[i] !== status[first]) {
      if (STATUS[status[first]].shade !== null) {
        shapes.push({
          type: "rect",
          layer: "below",
          xref: "x",
          yref: `${axis} domain`,
          x0: timestamps[first],
          x1: timestamps[end ? i - 1 : i],
          y0: 0,
          y1: 1,
          fillcolor: STATUS[status[first]].shade,
          line: { width: 0 },
        });
      }
      first = i;
    }
  }
  return shapes;
}

function logAxis(domain, ticks = [0.5, 1, 2, 5, 10, 20, 50, 100, 200, 500]) {
  return {
    domain,
    type: "log",
    anchor: "x",
    // Zooming moves through time only; the scale stays comparable.
    fixedrange: true,
    tickvals: ticks,
    ticktext: ticks.map((t) => `${t}×`),
    gridcolor: GRID,
    zeroline: false,
    tickfont: { color: MUTED },
  };
}

/** The time axis of both charts: long stops cut out, the same range in each. */
function timeAxis(cuts, anchor) {
  const range = state.range === "final" ? finalPhase() : state.range;
  return {
    type: "date",
    anchor,
    // A minute of margin on both sides of a cut keeps the line ends visible.
    rangebreaks: cuts.map((stop) => ({
      values: [isoAt(millis(stop.from) + 60000)],
      dvalue: stop.ms - 120000,
    })),
    showline: true,
    linecolor: "#c9c7be",
    // Room below the axis line for the // marks.
    ticklabelstandoff: 8,
    hoverformat: "%Y-%m-%d %H:%M",
    nticks: 9,
    tickangle: 0,
    // Times only on ticks less than a day apart: daily ticks all fall at midnight.
    tickformatstops: [
      { dtickrange: [null, 43200000], value: "%b %d %H:%M" },
      { dtickrange: [43200000, null], value: "%b %d" },
    ],
    gridcolor: GRID,
    tickfont: { color: MUTED },
    showspikes: true,
    spikemode: "across",
    spikesnap: "cursor",
    spikethickness: 1,
    spikedash: "solid",
    spikecolor: "#a9a8a1",
    ...(range ? { range, autorange: false } : { autorange: true }),
  };
}

/** A dashed line at the alert threshold, across one panel or lane. */
function thresholdShape(threshold, axis) {
  return {
    type: "line",
    xref: "paper",
    yref: axis,
    x0: 0,
    x1: 1,
    y0: threshold,
    y1: threshold,
    line: { color: MUTED, width: 1, dash: "dash" },
  };
}

/** A vertical line at each moment, over the full height of one panel or lane. */
function momentShapes(moments, axis) {
  return moments.map((moment) => ({
    type: "line",
    layer: moment.layer ?? "above",
    xref: "x",
    yref: `${axis} domain`,
    x0: moment.at,
    x1: moment.at,
    y0: 0,
    y1: 1,
    line: { color: moment.colour, width: moment.width ?? 1.5, dash: moment.dash ?? "solid" },
  }));
}

/** Where each long stop was cut out: a dotted line, as a moment that is no event. */
const stopMoments = (cuts) =>
  cuts.map((stop) => ({ at: stop.from, colour: STOP, width: 1, dash: "dot", layer: "below" }));

/** The usual mark for a broken axis, sitting on the axis line where the time jumps. */
function stopMarks(cuts) {
  return cuts.map((stop) => ({
    text: "//",
    xref: "x",
    yref: "paper",
    x: stop.from,
    y: 0,
    yanchor: "middle",
    showarrow: false,
    borderpad: 0,
    bgcolor: "#ffffff",
    font: { size: 11, color: INK, weight: 600 },
    hovertext: `Rig stopped ${duration(stop.ms)}: ${formatTime(stop.from)} to ${formatTime(stop.to)}`,
    hoverlabel: { bgcolor: "#ffffff", bordercolor: STOP, font: { color: INK } },
  }));
}

/** A legend entry for something drawn as a shape, which has no trace of its own. */
function key(name, style) {
  return { type: "scatter", x: [null], y: [null], name, hoverinfo: "skip", ...style };
}

/**
 * The replay's moment: a line at it and a veil over what came later, which nobody knew yet. Always
 * the last two shapes of a chart, so a replay step moves them without redrawing the rest.
 */
function replayShapes(last) {
  const now = state.moment === null ? last : runTimes()[state.moment];
  return [
    {
      type: "rect",
      layer: "above",
      xref: "x",
      yref: "paper",
      x0: now,
      x1: last,
      y0: 0,
      y1: 1,
      fillcolor: VEIL,
      line: { width: 0 },
    },
    {
      type: "line",
      layer: "above",
      xref: "x",
      yref: "paper",
      x0: now,
      x1: now,
      y0: 0,
      y1: 1,
      visible: state.moment !== null,
      line: { color: INK, width: 1.5 },
    },
  ];
}

/** One legend entry per shaded status; a heading only in a column, since a row has no room. */
function statusKeys(legend, heading = true) {
  const group = heading
    ? { legendgroup: "status", legendgrouptitle: { text: "Status", font: { color: MUTED } } }
    : {};
  return Object.values(STATUS)
    .filter((status) => status.band !== null)
    .map((status) =>
      key(status.band, {
        mode: "markers",
        legend,
        ...group,
        marker: {
          symbol: "square",
          size: 14,
          color: status.shade,
          line: { color: status.colour, width: 1 },
        },
      }),
    );
}

function legendBeside([, top]) {
  return { x: 1.02, xanchor: "left", y: top, yanchor: "top", bgcolor: "rgba(0,0,0,0)" };
}

function note(text, x, y, extra = {}) {
  return {
    text,
    xref: "paper",
    yref: "paper",
    x,
    y,
    xanchor: "left",
    yanchor: "bottom",
    align: "left",
    showarrow: false,
    ...extra,
  };
}

function finalPhase() {
  return [isoAt(millis(state.last) - FINAL_PHASE_MS), state.last];
}

/** When the bearing was raised to alert and to danger, as one mark if both came at once. */
function escalations(condition) {
  if (condition.alert_at === null) return [];
  if (condition.danger_at === condition.alert_at) {
    return [{ at: condition.danger_at, text: "alert and danger", colour: STATUS.danger.colour }];
  }
  const marks = [{ at: condition.alert_at, text: "alert", colour: STATUS.alert.colour }];
  if (condition.danger_at !== null) {
    marks.push({ at: condition.danger_at, text: "danger", colour: STATUS.danger.colour });
  }
  return marks;
}

/**
 * One bearing over the run: its health index, and with `ratios` the features behind it in two more
 * panels. Without them the index stands alone; the features are detail on demand.
 */
function drawChart(health, ratios, condition) {
  const names = ratios === null ? [] : Object.keys(FEATURES);
  const { x, ys } = breakAtGaps(health.timestamps, [
    health.index,
    health.driver.map((d) => (d === null ? "" : FEATURES[d].label)),
    ...names.map((name) => ratios[name]),
  ]);
  const [index, driver, ...features] = ys;
  const cuts = longStops(health.timestamps);
  const marks = escalations(condition);
  const domains = ratios === null ? [[0, 1]] : DOMAINS;
  const axes = domains.map((_, k) => axisName(k));

  const traces = [
    {
      type: "scatter",
      mode: "lines",
      name: "health index",
      x,
      y: index,
      customdata: driver,
      legend: "legend",
      // The panel's title names it; the legend explains only lines and shades without a label.
      showlegend: false,
      line: { color: INK, width: 1.4 },
      hovertemplate: "<b>%{y:.2f}×</b> from %{customdata}<extra>health index</extra>",
    },
    key(`threshold ${health.threshold}×`, {
      mode: "lines",
      line: { color: MUTED, width: 1, dash: "dash" },
    }),
    ...(cuts.length
      ? [key("stop ≥ 12 h, cut out", { mode: "lines", line: { color: STOP, width: 1, dash: "dot" } })]
      : []),
    ...statusKeys("legend"),
    ...names.map((name, k) => {
      const envelope = name in ENVELOPE;
      return {
        type: "scatter",
        mode: "lines",
        name: FEATURES[name].label,
        x,
        y: features[k],
        yaxis: envelope ? "y2" : "y3",
        legend: envelope ? "legend2" : "legend3",
        line: { color: FEATURES[name].colour, width: FEATURES[name].width ?? 0.9 },
        zorder: FEATURES[name].width ? 1 : 0,
        hovertemplate: `%{y:.2f}×<extra>${FEATURES[name].label}</extra>`,
      };
    }),
  ];

  const shapes = statusShapes(health.timestamps, health.status);
  axes.forEach((axis) => {
    shapes.push(
      thresholdShape(health.threshold, axis),
      ...momentShapes(marks, axis),
      ...momentShapes(stopMoments(cuts), axis),
    );
  });
  shapes.push(...replayShapes(health.timestamps.at(-1)));

  // Short titles; the rule behind each panel shows on hovering the ⓘ.
  const title = (text, y, explanation) =>
    note(`${text} <span style="color:${MUTED}">ⓘ</span>`, 0, y, {
      yshift: 8,
      font: { size: 13, color: INK, weight: 600 },
      hovertext: explanation,
      hoverlabel: { bgcolor: "#ffffff", bordercolor: GRID, font: { color: INK } },
    });
  const annotations = [
    title(
      "Health index and status",
      domains[0][1],
      `The largest feature over its baseline<br>alert: ${health.threshold}× for 1 h, not crosstalk<br>` +
        `danger: rms ${health.threshold}× as well`,
    ),
  ];
  if (ratios !== null) {
    annotations.push(
      title("Envelope features", DOMAINS[1][1], "One per bearing part: the damaged part's rises"),
      title("Time-domain features", DOMAINS[2][1], "Overall level and impulsiveness · rms decides danger"),
    );
  }
  const middle = millis(health.timestamps[Math.floor(health.timestamps.length / 2)]);
  marks.forEach((mark, k) => {
    // Towards the middle of the run: Plotly centres an "auto" label on its line and stretches the
    // time axis past the data to fit one that sticks out at the end.
    const late = millis(mark.at) > middle;
    annotations.push({
      text: `${mark.text} ${formatTime(mark.at)}`,
      xref: "x",
      yref: "y domain",
      x: mark.at,
      // One below the other: alert and danger can be close together on a long run.
      y: 1 - 0.12 * k,
      xanchor: late ? "right" : "left",
      xshift: late ? -3 : 3,
      yanchor: "top",
      showarrow: false,
      borderpad: 3,
      bgcolor: "rgba(255, 255, 255, 0.85)",
      font: { size: 11, color: mark.colour, weight: 600 },
    });
  });
  annotations.push(...stopMarks(cuts));

  const layout = {
    height: ratios === null ? 340 : 760,
    margin: MARGIN,
    paper_bgcolor: "rgba(0,0,0,0)",
    plot_bgcolor: "#ffffff",
    font: { family: FONT, size: 12, color: INK },
    // One hover label for all panels: every value at that moment.
    hovermode: "x unified",
    hoversubplots: "axis",
    hoverlabel: { bgcolor: "rgba(255, 255, 255, 0.96)", bordercolor: GRID },
    xaxis: timeAxis(cuts, axes.at(-1)),
    shapes,
    annotations,
  };
  domains.forEach((domain, k) => {
    layout[`yaxis${k === 0 ? "" : k + 1}`] = logAxis(domain);
    layout[`legend${k === 0 ? "" : k + 1}`] = legendBeside(domain);
  });
  return Plotly.react("chart", traces, layout, CONFIG);
}

// --- run overview -----------------------------------------------------------------------------

const axisName = (k) => (k === 0 ? "y" : `y${k + 1}`);

/** Each lane's vertical extent in paper units, top lane first. */
function laneDomains(count) {
  const plot = count * LANE_PX + (count - 1) * LANE_GAP_PX;
  return Array.from({ length: count }, (_, k) => {
    const top = 1 - (k * (LANE_PX + LANE_GAP_PX)) / plot;
    return [top - LANE_PX / plot, top];
  });
}

/** One range for every lane, so a bearing that only hears a fault sits visibly lower. */
function sharedRange(series) {
  let low = 0.8;
  let high = 1;
  for (const health of series) {
    for (const value of health.index) {
      if (value === null) continue;
      low = Math.min(low, value * 0.95);
      high = Math.max(high, value);
    }
  }
  return [low, high * 1.25];
}

/** Lane ticks: the threshold first, then whatever fits in the lane without crowding. */
function laneTicks([low, high], threshold) {
  const pxPerDecade = LANE_PX / Math.log10(high / low);
  const ticks = [];
  for (const tick of [threshold, 10, 100, 1, 5, 50, 20]) {
    const inside = tick >= low && tick <= high;
    const clear = ticks.every((t) => Math.abs(Math.log10(tick / t)) * pxPerDecade >= 18);
    if (inside && clear) ticks.push(tick);
  }
  return ticks.sort((a, b) => a - b);
}

/** A lane's verdict at its end, with how early each level came. */
function laneVerdict(summary) {
  const result = resultOf(summary.bearing);
  const lines = [`<b>${capitalised(result.verdict)}</b> · ${summary.documented_failure ?? "survived"}`];
  const lead = leads(result);
  if (lead !== null) {
    lines.push(...[lead, "before the run ended"].map((line) => `<span style="color:${MUTED}">${line}</span>`));
  }
  return lines.join("<br>");
}

function drawOverview() {
  const lanes = state.bearings.map((summary) => ({ summary, health: state.health[summary.bearing] }));
  const { timestamps, threshold } = lanes[0].health;
  const cuts = longStops(timestamps);
  const domains = laneDomains(lanes.length);
  const range = sharedRange(lanes.map((lane) => lane.health));

  const traces = lanes.map(({ summary, health }, k) => {
    const { x, ys } = breakAtGaps(health.timestamps, [
      health.index,
      health.driver.map((d) => (d === null ? "" : FEATURES[d].label)),
    ]);
    return {
      type: "scatter",
      mode: "lines",
      name: `Bearing ${summary.bearing}`,
      x,
      y: ys[0],
      customdata: ys[1],
      yaxis: axisName(k),
      showlegend: false,
      line: { color: INK, width: 1.2 },
      hovertemplate: `<b>%{y:.2f}×</b> from %{customdata}<extra>Bearing ${summary.bearing}</extra>`,
    };
  });
  traces.push(
    key(`threshold ${threshold}×`, { mode: "lines", line: { color: MUTED, width: 1, dash: "dash" } }),
    ...statusKeys("legend", false),
  );

  const shapes = [];
  const annotations = [];
  lanes.forEach(({ summary, health }, k) => {
    const axis = axisName(k);
    const [bottom, top] = domains[k];
    const selected = summary.bearing === state.bearing;
    shapes.push(
      ...statusShapes(health.timestamps, health.status, axis),
      thresholdShape(threshold, axis),
      ...momentShapes(stopMoments(cuts), axis),
    );
    // Outlined like its card, so the lane and the detail below it read as one selection.
    if (selected) {
      shapes.push({
        type: "rect",
        xref: "paper",
        yref: `${axis} domain`,
        x0: 0,
        x1: 1,
        y0: 0,
        y1: 1,
        line: { color: INK, width: 2 },
      });
    }
    annotations.push(
      note(`Bearing ${summary.bearing}`, 0, top, {
        yshift: 2,
        font: { size: 13, color: INK, weight: selected ? 700 : 500 },
      }),
    );
    if (state.hindsight) {
      annotations.push(
        note(laneVerdict(summary), 1.02, (bottom + top) / 2, {
          yanchor: "middle",
          font: { size: 12, color: INK },
        }),
      );
    }
  });
  shapes.push(...replayShapes(timestamps.at(-1)));
  annotations.push(...stopMarks(cuts));

  const layout = {
    height: MARGIN.t + MARGIN.b + lanes.length * LANE_PX + (lanes.length - 1) * LANE_GAP_PX,
    margin: MARGIN,
    paper_bgcolor: "rgba(0,0,0,0)",
    plot_bgcolor: "#ffffff",
    font: { family: FONT, size: 12, color: INK },
    // Every bearing's index at the pointer's moment, to compare a source with its neighbours.
    hovermode: "x unified",
    hoversubplots: "axis",
    hoverlabel: { bgcolor: "rgba(255, 255, 255, 0.96)", bordercolor: GRID },
    xaxis: timeAxis(cuts, axisName(lanes.length - 1)),
    // A key only: the lanes are not for hiding.
    legend: {
      orientation: "h",
      x: 1,
      xanchor: "right",
      y: 1,
      yanchor: "bottom",
      bgcolor: "rgba(0,0,0,0)",
      itemclick: false,
      itemdoubleclick: false,
    },
    shapes,
    annotations,
  };
  const ticks = laneTicks(range, threshold);
  domains.forEach((domain, k) => {
    layout[`yaxis${k === 0 ? "" : k + 1}`] = {
      ...logAxis(domain, ticks),
      range: range.map(Math.log10),
      autorange: false,
    };
  });
  return Plotly.react("overview-chart", traces, layout, CONFIG);
}

/** The bearing whose lane is nearest to a click, found from the pointer's height. */
function laneAt(event) {
  const box = $("overview-chart").getBoundingClientRect();
  const y = 1 - (event.clientY - box.top - MARGIN.t) / (box.height - MARGIN.t - MARGIN.b);
  const distance = ([bottom, top]) => Math.abs(y - (bottom + top) / 2);
  const domains = laneDomains(state.bearings.length);
  const nearest = domains.reduce((best, domain, k) => (distance(domain) < distance(domains[best]) ? k : best), 0);
  return state.bearings[nearest].bearing;
}

/** Whether a pointer is over the lanes, not over the margins, the key or Plotly's toolbar. */
function onLanes(event) {
  const box = $("overview-chart").getBoundingClientRect();
  const x = event.clientX - box.left;
  const y = event.clientY - box.top;
  return x >= MARGIN.l && x <= box.width - MARGIN.r && y >= MARGIN.t && y <= box.height - MARGIN.b;
}

/**
 * Lane clicks from the pointer itself rather than plotly_click: Plotly drops a click when its
 * hover state is redrawn mid-press, which a replay does ten times a second. A press that moves is
 * a zoom drag, not a click.
 */
function watchLaneClicks() {
  let press = null;
  $("overview-chart").addEventListener(
    "pointerdown",
    (event) => {
      press = event.button === 0 && onLanes(event) ? event : null;
    },
    true,
  );
  window.addEventListener(
    "pointerup",
    (event) => {
      if (press === null) return;
      const moved = Math.hypot(event.clientX - press.clientX, event.clientY - press.clientY);
      press = null;
      const bearing = laneAt(event);
      if (moved <= 4 && bearing !== state.bearing) handle(() => pickBearing(bearing))();
    },
    true,
  );
}

// --- time range -------------------------------------------------------------------------------

function markRange() {
  $("zoom-all").setAttribute("aria-pressed", String(state.range === null));
  $("zoom-final").setAttribute("aria-pressed", String(state.range === "final"));
}

const CHARTS = ["overview-chart", "chart"];
// Set while charts follow a range change, so their own relayout events are not taken as drags.
let following = false;

/** Shows the current range in every drawn chart but the one it came from. */
async function applyRange(source = null) {
  const range = state.range === "final" ? finalPhase() : state.range;
  const update = range ? { "xaxis.range": range } : { "xaxis.autorange": true };
  following = true;
  try {
    const drawn = CHARTS.filter((id) => id !== source && $(id).data);
    await Promise.all(drawn.map((id) => Plotly.relayout(id, update)));
  } finally {
    following = false;
  }
}

function showRange(range) {
  state.range = range;
  markRange();
  return applyRange();
}

/** Keeps a range dragged in one chart: the other follows, and it survives switching bearings. */
function followDrag(source) {
  return (event) => {
    if (following) return;
    if ("xaxis.range[0]" in event) {
      state.range = [event["xaxis.range[0]"], event["xaxis.range[1]"]];
    } else if (event["xaxis.autorange"]) {
      state.range = null;
    } else {
      return;
    }
    markRange();
    applyRange(source);
  };
}

/** Legend clicks hide or isolate features; the first legend is a key, not for clicking. */
function featuresOnly(event) {
  // Not done with itemclick: Plotly applies the first legend's setting to every legend.
  return event.data[event.curveNumber].legend !== "legend";
}

// --- replay -----------------------------------------------------------------------------------

let playing = false;
// The moment's request in flight: a newer moment cancels it, so an old answer never lands last.
let momentRequest = null;

/** What the bar says about the moment shown. */
function markMoment() {
  const times = runTimes();
  const latest = state.moment === null;
  const at = formatTime(times[state.moment ?? times.length - 1]);
  const label = `${latest ? "Latest" : "As of"} ${at}`;
  if (latest) {
    $("as-of").replaceChildren(label);
  } else {
    // Only while replaying: the veil over what came later needs saying.
    const note = document.createElement("small");
    note.textContent = "dimmed: not known yet then";
    $("as-of").replaceChildren(label, note);
  }
}

function markPlay() {
  $("play").textContent = playing ? "❚❚ Pause" : "▶ Replay";
  $("play").setAttribute("aria-label", playing ? "Pause the replay" : "Replay the run");
}

/** Moves the line and the veil in every drawn chart; nothing else is redrawn. */
function moveVeil() {
  const [veil, line] = replayShapes(runTimes().at(-1));
  const drawn = CHARTS.filter((id) => $(id).data);
  return Promise.all(
    drawn.map((id) => {
      const k = $(id).layout.shapes.length - 2;
      return Plotly.relayout(id, {
        [`shapes[${k}].x0`]: veil.x0,
        [`shapes[${k + 1}].x0`]: line.x0,
        [`shapes[${k + 1}].x1`]: line.x1,
        [`shapes[${k + 1}].visible`]: line.visible,
      });
    }),
  );
}

/** The rig's one light: its worst status, and for an alert or danger which bearings have it. */
function renderMachine() {
  const { status, bearings } = state.machine;
  const named = status === "alert" || status === "danger";
  const which = named ? ` · bearing${bearings.length > 1 ? "s" : ""} ${listed(bearings)}` : "";
  const pill = $("machine").querySelector(".pill");
  pill.style.setProperty("--status", STATUS[status].colour);
  pill.classList.toggle("calm", !named);
  pill.textContent = `${STATUS[status].label}${which}`;
}

/** The cards and the rig's light as of the moment shown, through the same `?at=` any client uses. */
async function loadMoment() {
  momentRequest?.abort();
  const request = new AbortController();
  momentRequest = request;
  const at = state.moment === null ? "" : `?at=${encodeURIComponent(runTimes()[state.moment])}`;
  try {
    const shown = await getJSON(`${API}/experiments/${state.run}${at}`, request.signal);
    state.cards = shown.bearings;
    state.machine = shown.machine;
    renderCards();
    renderMachine();
  } catch (error) {
    if (error.name !== "AbortError") throw error;
  }
}

async function showMoment(index) {
  state.moment = index >= runTimes().length - 1 ? null : index;
  $("moment").value = String(index);
  markMoment();
  markReply();
  remember();
  await Promise.all([moveVeil(), loadMoment()]);
}

/** Steps through the run from where the slider is, or from its start when at the end. */
async function play() {
  if (playing) {
    playing = false;
    return;
  }
  const run = state.run;
  const last = runTimes().length - 1;
  const step = Math.max(1, Math.round(last / (PLAY_MS / FRAME_MS)));
  let index = state.moment ?? 0;
  playing = true;
  markPlay();
  try {
    // Each frame waits for its cards, so a slow connection slows the replay instead of piling up.
    while (playing && state.run === run) {
      const started = performance.now();
      await showMoment(index);
      if (index === last) break;
      index = Math.min(last, index + step);
      const rest = FRAME_MS - (performance.now() - started);
      if (rest > 0) await new Promise((resolve) => setTimeout(resolve, rest));
    }
  } finally {
    playing = false;
    markPlay();
  }
}

// --- bearing detail ---------------------------------------------------------------------------

/** Set after each draw; removing the old handlers first keeps one per event. */
function listen(id, handlers) {
  const chart = $(id);
  for (const [event, handler] of Object.entries(handlers)) {
    chart.removeAllListeners(event);
    chart.on(event, handler);
  }
}

/** Shows one bearing in detail, or with null the whole rig. The selection is the copilot's focus. */
async function selectBearing(bearing) {
  const ratios =
    bearing === null || !state.features
      ? null
      : await getJSON(`${API}/experiments/${state.run}/bearings/${bearing}/ratios`);
  state.bearing = bearing;
  remember();
  renderCards();
  markRange();
  renderScope();

  $("copilot").hidden = false;
  // Shown before drawing: Plotly sizes a chart from its container.
  $("overview").hidden = false;
  $("detail").hidden = bearing === null;
  if (bearing === null) {
    // Gone rather than hidden, so the replay and the time range leave it alone.
    Plotly.purge("chart");
    await drawOverview();
  } else {
    $("detail-title").textContent = `Bearing ${bearing}`;
    const summary = state.bearings.find((b) => b.bearing === bearing);
    await Promise.all([drawOverview(), drawChart(state.health[bearing], ratios, summary.condition)]);
    listen("chart", {
      plotly_relayout: followDrag("chart"),
      plotly_legendclick: featuresOnly,
      plotly_legenddoubleclick: featuresOnly,
    });
  }
  listen("overview-chart", { plotly_relayout: followDrag("overview-chart") });
}

/**
 * A selection by click, which also brings the detail into view: it sits below the overview, out of
 * sight from the cards. Loading a run or a link selects without scrolling.
 */
async function pickBearing(bearing) {
  await selectBearing(bearing);
  if (bearing !== null) $("detail").scrollIntoView({ behavior: "smooth", block: "nearest" });
}

// --- copilot ----------------------------------------------------------------------------------

// A click asks: nothing is asked on its own, since every answer is a paid model call. "This
// bearing" means nothing for the whole rig, so that one needs a bearing selected.
const SUGGESTIONS = [
  { text: "Which bearing is the problem?", needsBearing: false },
  { text: "What is wrong with this bearing?", needsBearing: true },
  { text: "Is it getting worse?", needsBearing: false },
  { text: "What should I do now?", needsBearing: false },
];
const CITATION = /\[(\d+)\]/;

/** What a question is about: the selected bearing, or the whole rig. Follows the selection. */
function renderScope() {
  const focus = state.bearing === null ? "the whole rig" : `bearing ${state.bearing}`;
  $("ask-scope").textContent = `Focus: ${focus} · answers only from looked-up facts, each cited`;
  const fitting = SUGGESTIONS.filter((s) => state.bearing !== null || !s.needsBearing);
  $("suggestions").replaceChildren(
    ...fitting.map(({ text }) => {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = text;
      button.disabled = $("ask-button").disabled;
      button.addEventListener("click", () => {
        $("question").value = text;
        ask();
      });
      return button;
    }),
  );
}

function setAsking(asking) {
  for (const button of [$("ask-button"), ...$("suggestions").children]) button.disabled = asking;
  $("ask-button").textContent = asking ? "Asking…" : "Ask";
}

function setStatus(text, error = false) {
  $("copilot-status").textContent = text;
  $("copilot-status").classList.toggle("error", error);
}

/**
 * Asks about the moment shown, with the selected bearing in focus, or none for the whole rig. A
 * running replay stops first, so the answer and the dashboard stay on the same moment.
 */
async function ask() {
  const question = $("question").value.trim();
  if (!question || $("ask-button").disabled) return;
  playing = false;
  const at = state.moment === null ? null : runTimes()[state.moment];
  const asked = { question, run: state.run, moment: state.moment, bearing: state.bearing, time: at ?? state.last };
  setAsking(true);
  $("reply").hidden = true;
  setStatus("The copilot is looking up the facts and writing an answer…");
  try {
    const query = at === null ? "" : `?at=${encodeURIComponent(at)}`;
    const response = await fetch(`${API}/experiments/${asked.run}/copilot${query}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, bearing: asked.bearing }),
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      // A 429 says to wait a minute; the API words every refusal for the user.
      throw new Error(typeof body.detail === "string" ? body.detail : `${response.status} ${response.statusText}`);
    }
    state.asked = asked;
    renderReply(body);
    setStatus("");
    $("question").value = "";
  } catch (error) {
    setStatus(`Could not ask: ${error.message}`, true);
  } finally {
    setAsking(false);
  }
}

/**
 * The answer as text, with each [n] made a link to fact n. The model's text is never parsed as
 * HTML, so whatever it writes stays text; a number that is no fact stays text too, marked.
 */
function answerParts(text, ids) {
  return text.split(CITATION).map((part, k) => {
    // split keeps the captured numbers at the odd positions.
    if (k % 2 === 0) return document.createTextNode(part);
    const id = Number(part);
    if (!ids.has(id)) {
      const unknown = document.createElement("span");
      unknown.className = "unknown-citation";
      unknown.title = "No fact with this number was looked up";
      unknown.textContent = `[${part}]`;
      return unknown;
    }
    const link = document.createElement("a");
    link.href = `#fact-${id}`;
    link.textContent = `[${id}]`;
    link.addEventListener("click", (event) => {
      event.preventDefault();
      showFact(id);
    });
    return link;
  });
}

/** Scrolls to a cited fact and marks it, so a sentence can be checked against what it rests on. */
function showFact(id) {
  for (const item of $("reply").querySelectorAll("[aria-current]")) item.removeAttribute("aria-current");
  const item = $(`fact-${id}`);
  item.setAttribute("aria-current", "true");
  item.scrollIntoView({ behavior: "smooth", block: "center" });
}

function factItem(source) {
  const item = document.createElement("li");
  item.id = `fact-${source.id}`;
  const number = document.createElement("span");
  number.className = "fact-id";
  number.textContent = `[${source.id}]`;
  const ref = document.createElement("small");
  ref.textContent = source.ref;
  item.append(number, ` ${source.text}`, ref);
  return item;
}

/** The cited facts in view, every other fact folded away: without an answer, open. */
function renderReply(reply) {
  const ids = new Set(reply.sources.map((source) => source.id));
  const cited = new Set(
    [...(reply.answer ?? "").matchAll(new RegExp(CITATION, "g"))]
      .map((match) => Number(match[1]))
      .filter((id) => ids.has(id)),
  );
  const { question, run, moment, bearing, time } = state.asked;
  const asked = document.createElement("strong");
  asked.textContent = `“${question}”`;
  const end = moment === null ? " (latest)" : "";
  const scope = bearing === null ? "whole rig" : `bearing ${bearing} in focus`;
  $("reply-label").replaceChildren(asked, ` · ${runLabel(run)} as of ${formatTime(time)}${end} · ${scope}`);
  $("answer").hidden = reply.answer === null;
  $("answer").replaceChildren(...(reply.answer === null ? [] : answerParts(reply.answer, ids)));
  $("answer-note").textContent = reply.note ?? "";
  $("cited").replaceChildren(...reply.sources.filter((s) => cited.has(s.id)).map(factItem));
  const others = reply.sources.filter((s) => !cited.has(s.id));
  const folded = $("other-facts");
  folded.querySelector("summary").textContent =
    `${cited.size ? "Other facts looked up" : "Facts looked up"} (${others.length})`;
  folded.querySelector("ul").replaceChildren(...others.map(factItem));
  folded.open = reply.answer === null;
  $("reply").hidden = false;
  markReply();
}

/** Greys an answer out once the dashboard shows another run or moment than it was about. */
function markReply() {
  if (state.asked === null) return;
  const stale = state.asked.run !== state.run || state.asked.moment !== state.moment;
  $("reply").classList.toggle("stale", stale);
  $("stale-note").hidden = !stale;
}

// --- start ------------------------------------------------------------------------------------

async function start() {
  $("zoom-all").addEventListener("click", () => showRange(null));
  $("zoom-final").addEventListener("click", () => showRange("final"));
  $("play").addEventListener("click", handle(play, false));
  $("ask").addEventListener("submit", (event) => {
    event.preventDefault();
    ask();
  });
  watchLaneClicks();
  $("hindsight").addEventListener(
    "change",
    handle(async () => {
      showHindsight($("hindsight").checked);
      remember();
      await drawOverview();
    }, false),
  );
  $("features").addEventListener(
    "change",
    handle(async () => {
      state.features = $("features").checked;
      remember();
      await selectBearing(state.bearing);
    }),
  );
  // Dragging the slider takes over from a running replay.
  $("moment").addEventListener("input", (event) => {
    playing = false;
    handle(() => showMoment(Number(event.target.value)), false)();
  });
  const [experiments, version] = await Promise.all([
    getJSON(`${API}/experiments`),
    getJSON("/api/version"),
  ]);
  $("version").textContent = version.commit.slice(0, 7);
  renderRuns(experiments);
  const params = new URLSearchParams(location.search);
  const names = experiments.map((e) => e.name);
  const run = names.includes(params.get("run")) ? params.get("run") : names[0];
  // Without a bearing in the address, the page opens on the whole rig: the machine first.
  const bearing = params.has("bearing") ? Number(params.get("bearing")) : null;
  showHindsight(params.get("hindsight") === "1");
  state.features = params.get("features") === "1";
  $("features").checked = state.features;
  await selectRun(run, bearing, params.get("at"));
}

/** The run's result line follows at once; the lane verdicts need the overview redrawn. */
function showHindsight(on) {
  state.hindsight = on;
  $("hindsight").checked = on;
  $("run-result").hidden = !on;
}

handle(start)();
