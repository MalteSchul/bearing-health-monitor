"use strict";

// Display only: every value shown is computed by the API, nothing is judged here.

const API = "/api/v1";

const STATUS = {
  baseline: { label: "Learning", colour: "#8a8983", shade: "rgba(138, 137, 131, 0.22)" },
  ok: { label: "OK", colour: "#2e9d5b", shade: null },
  crosstalk: { label: "Crosstalk", colour: "#d99a00", shade: "rgba(217, 154, 0, 0.2)" },
  alarm: { label: "Alarm", colour: "#d03b3b", shade: "rgba(208, 59, 59, 0.13)" },
};

const ENVELOPE = {
  env_bpfo: { label: "outer race (BPFO)", colour: "#2a78d6" },
  env_bpfi: { label: "inner race (BPFI)", colour: "#eb6834" },
  env_bsf: { label: "roller (BSF)", colour: "#1baf7a" },
  env_ftf: { label: "cage (FTF)", colour: "#eda100" },
};
const TIME_DOMAIN = {
  rms: { label: "rms", colour: "#e87ba4" },
  peak: { label: "peak", colour: "#008300" },
  crest_factor: { label: "crest factor", colour: "#4a3aa7" },
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
  modeBarButtonsToRemove: ["select2d", "lasso2d", "autoScale2d"],
};
// Top to bottom: health index, envelope features, time-domain features.
const DOMAINS = [
  [0.66, 1],
  [0.34, 0.58],
  [0, 0.24],
];

// range: null for the whole run, "final" for its last 100 h, or [from, to] dragged in the chart.
const state = { run: null, last: null, bearing: null, bearings: [], range: null };

const $ = (id) => document.getElementById(id);

async function getJSON(path) {
  const response = await fetch(path);
  if (!response.ok) {
    throw new Error(`${path}: ${response.status} ${response.statusText}`);
  }
  return response.json();
}

/** A click's work, with the page marked busy meanwhile and any failure shown. */
function handle(task) {
  return async () => {
    $("error").hidden = true;
    document.body.classList.add("loading");
    try {
      await task();
    } catch (error) {
      $("error").textContent = `Could not load data. ${error.message}`;
      $("error").hidden = false;
    } finally {
      document.body.classList.remove("loading");
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
      button.dataset.run = experiment.name;
      button.addEventListener("click", handle(() => selectRun(experiment.name)));
      return button;
    }),
  );
}

/** Run and bearing in the address, so a view can be bookmarked and shared. */
function remember() {
  const params = new URLSearchParams({ run: state.run, bearing: state.bearing });
  history.replaceState(null, "", `?${params}`);
}

async function selectRun(name, bearing = state.bearing) {
  const experiment = await getJSON(`${API}/experiments/${name}`);
  // "Final 100 h" means the same in every run; a dragged range does not.
  if (name !== state.run && state.range !== "final") state.range = null;
  state.run = name;
  state.last = experiment.last;
  state.bearings = experiment.bearings;
  for (const button of $("runs").children) {
    button.setAttribute("aria-pressed", String(button.dataset.run === name));
  }
  $("run-info").textContent =
    `${runLabel(name)} · ${formatTime(experiment.first)} to ${formatTime(experiment.last)} · ` +
    `${experiment.snapshots.toLocaleString("en")} one-second vibration snapshots`;

  const numbers = experiment.bearings.map((b) => b.bearing);
  await selectBearing(numbers.includes(bearing) ? bearing : numbers[0]);
}

// --- bearing cards ----------------------------------------------------------------------------

function conditionFacts(condition) {
  const facts = [];
  if (condition.status === "baseline") facts.push("Recording its first 24 h as the reference");
  if (condition.status === "ok") facts.push("No feature held above the alarm threshold for 1 h");
  if (condition.crosstalk_from !== null) {
    facts.push(`Louder on bearing ${condition.crosstalk_from}: heard, not its own`);
  }
  if (condition.alarm_at !== null) facts.push(`Alarm since ${formatTime(condition.alarm_at)}`);
  if (condition.diagnosis !== null) {
    facts.push(`Diagnosis: <strong>${condition.diagnosis}</strong>`);
  }
  return facts;
}

function renderCards() {
  $("bearings").replaceChildren(
    ...state.bearings.map((summary) => {
      const { condition } = summary;
      const status = STATUS[condition.status];
      const card = document.createElement("button");
      card.className = "card";
      card.style.setProperty("--status", status.colour);
      card.setAttribute("aria-pressed", String(summary.bearing === state.bearing));
      card.addEventListener("click", handle(() => selectBearing(summary.bearing)));

      const index = condition.index === null ? "–" : `${condition.index.toFixed(1)}×`;
      const driver = condition.driver === null ? "&nbsp;" : `driven by ${FEATURES[condition.driver].label}`;
      card.innerHTML = `
        <span class="card-head">
          <span class="name">Bearing ${summary.bearing}</span>
          <span class="pill">${status.label}</span>
        </span>
        <span class="index">${index}<small>health index</small></span>
        <span class="driver">${driver}</span>
        <span class="facts">${conditionFacts(condition).join("<br>")}</span>
        <span class="truth" title="From the dataset readme. The detector never sees it.">
          <small>Documented at the end · hindsight</small>
          ${summary.documented_failure ?? "survived"}
        </span>`;
      return card;
    }),
  );
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

/** What the gaps and cuts in the chart mean, for the runs that have them. */
function stopNote(timestamps) {
  const steps = timestamps.slice(1).map((time, i) => millis(time) - millis(timestamps[i]));
  return (
    (steps.some((ms) => ms > GAP_MS) ? " Gaps: the rig stood still." : "") +
    (steps.some((ms) => ms >= CUT_MS)
      ? " Stops of 12 h or more are cut out of the time axis and marked with a dotted line" +
        " and //; hover the // for how long."
      : "")
  );
}

/** Contiguous stretches of one status, as shaded rectangles behind the health index. */
function statusShapes(timestamps, status) {
  const shapes = [];
  let first = 0;
  for (let i = 1; i <= status.length; i += 1) {
    const end = i === status.length;
    const gap = !end && millis(timestamps[i]) - millis(timestamps[i - 1]) > GAP_MS;
    if (end || gap || status[i] !== status[first]) {
      if (STATUS[status[first]].shade !== null) {
        shapes.push({
          type: "rect",
          layer: "below",
          xref: "x",
          yref: "y domain",
          x0: timestamps[first],
          x1: end || gap ? timestamps[i - 1] : timestamps[i],
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

function logAxis(domain) {
  const ticks = [0.5, 1, 2, 5, 10, 20, 50, 100, 200, 500];
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

function drawChart(health, ratios, condition) {
  const names = Object.keys(FEATURES);
  const { x, ys } = breakAtGaps(health.timestamps, [
    health.index,
    health.driver.map((d) => (d === null ? "" : FEATURES[d].label)),
    ...names.map((name) => ratios[name]),
  ]);
  const [index, driver, ...features] = ys;
  const cuts = longStops(health.timestamps);

  // Legend entries for what is drawn as shapes: they carry no data of their own.
  const key = (name, style) => ({
    type: "scatter",
    x: [null],
    y: [null],
    name,
    legend: "legend",
    hoverinfo: "skip",
    ...style,
  });
  const traces = [
    {
      type: "scatter",
      mode: "lines",
      name: "health index",
      x,
      y: index,
      customdata: driver,
      legend: "legend",
      line: { color: INK, width: 1.4 },
      hovertemplate: "<b>%{y:.2f}×</b> from %{customdata}<extra>health index</extra>",
    },
    key(`threshold ${health.threshold}×`, {
      mode: "lines",
      line: { color: MUTED, width: 1, dash: "dash" },
    }),
    key("alarm raised", { mode: "lines", line: { color: STATUS.alarm.colour, width: 1.5 } }),
    ...(cuts.length
      ? [key("stop ≥ 12 h, cut out", { mode: "lines", line: { color: STOP, width: 1, dash: "dot" } })]
      : []),
    ...["baseline", "crosstalk", "alarm"].map((status) =>
      key(STATUS[status].label.toLowerCase(), {
        mode: "markers",
        legendgroup: "status",
        legendgrouptitle: { text: "Status", font: { color: MUTED } },
        marker: {
          symbol: "square",
          size: 14,
          color: STATUS[status].shade,
          line: { color: STATUS[status].colour, width: 1 },
        },
      }),
    ),
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
        line: { color: FEATURES[name].colour, width: 0.9 },
        hovertemplate: `%{y:.2f}×<extra>${FEATURES[name].label}</extra>`,
      };
    }),
  ];

  const shapes = statusShapes(health.timestamps, health.status);
  ["y", "y2", "y3"].forEach((axis) => {
    shapes.push({
      type: "line",
      xref: "paper",
      yref: axis,
      x0: 0,
      x1: 1,
      y0: health.threshold,
      y1: health.threshold,
      line: { color: MUTED, width: 1, dash: "dash" },
    });
    if (condition.alarm_at !== null) {
      shapes.push({
        type: "line",
        xref: "x",
        yref: `${axis} domain`,
        x0: condition.alarm_at,
        x1: condition.alarm_at,
        y0: 0,
        y1: 1,
        line: { color: STATUS.alarm.colour, width: 1.5 },
      });
    }
    cuts.forEach((stop) => {
      shapes.push({
        type: "line",
        layer: "below",
        xref: "x",
        yref: `${axis} domain`,
        x0: stop.from,
        x1: stop.from,
        y0: 0,
        y1: 1,
        line: { color: STOP, width: 1, dash: "dot" },
      });
    });
  });

  const title = { yshift: 8, font: { size: 13, color: INK, weight: 600 } };
  const annotations = [
    note(
      `Health index and status · alarm after ${health.threshold}× for 1 h on own evidence`,
      0,
      DOMAINS[0][1],
      title,
    ),
    note("Envelope features · one per bearing part", 0, DOMAINS[1][1], title),
    note("Time-domain features · overall level and impulsiveness", 0, DOMAINS[2][1], title),
    note("Click a feature to hide it,<br>double-click to see it alone.", 1.02, DOMAINS[2][0], {
      font: { size: 11, color: MUTED },
    }),
  ];
  if (condition.alarm_at !== null) {
    annotations.push({
      text: `alarm ${formatTime(condition.alarm_at)}`,
      xref: "x",
      yref: "y domain",
      x: condition.alarm_at,
      y: 1,
      // Left of the line near the right edge, right of it near the left edge.
      xanchor: "auto",
      yanchor: "top",
      showarrow: false,
      borderpad: 3,
      bgcolor: "rgba(255, 255, 255, 0.85)",
      font: { size: 11, color: STATUS.alarm.colour, weight: 600 },
    });
  }
  // The usual mark for a broken axis, sitting on the axis line where the time jumps.
  cuts.forEach((stop) => {
    annotations.push({
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
    });
  });

  const range = state.range === "final" ? finalPhase() : state.range;
  const layout = {
    height: 760,
    margin: { l: 52, r: 190, t: 34, b: 36 },
    paper_bgcolor: "rgba(0,0,0,0)",
    plot_bgcolor: "#ffffff",
    font: { family: FONT, size: 12, color: INK },
    // One hover label for all three panels: every value at that moment.
    hovermode: "x unified",
    hoversubplots: "axis",
    hoverlabel: { bgcolor: "rgba(255, 255, 255, 0.96)", bordercolor: GRID },
    xaxis: {
      type: "date",
      anchor: "y3",
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
      tickformatstops: [
        { dtickrange: [null, 86400000], value: "%b %d %H:%M" },
        { dtickrange: [86400000, null], value: "%b %d" },
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
    },
    yaxis: logAxis(DOMAINS[0]),
    yaxis2: logAxis(DOMAINS[1]),
    yaxis3: logAxis(DOMAINS[2]),
    // The first legend is a key to the shading, nothing to toggle.
    legend: { ...legendBeside(DOMAINS[0]), itemclick: false, itemdoubleclick: false },
    legend2: legendBeside(DOMAINS[1]),
    legend3: legendBeside(DOMAINS[2]),
    shapes,
    annotations,
  };
  return Plotly.react("chart", traces, layout, CONFIG);
}

// --- time range -------------------------------------------------------------------------------

function markRange() {
  $("zoom-all").setAttribute("aria-pressed", String(state.range === null));
  $("zoom-final").setAttribute("aria-pressed", String(state.range === "final"));
}

function showRange(range) {
  state.range = range;
  markRange();
  const update = range === "final" ? { "xaxis.range": finalPhase() } : { "xaxis.autorange": true };
  return Plotly.relayout("chart", update);
}

/** Keeps a range dragged in the chart, so it survives switching bearings. */
function followDrag(event) {
  if ("xaxis.range[0]" in event) state.range = [event["xaxis.range[0]"], event["xaxis.range[1]"]];
  if (event["xaxis.autorange"]) state.range = null;
  markRange();
}

// --- bearing detail ---------------------------------------------------------------------------

async function selectBearing(bearing) {
  const path = `${API}/experiments/${state.run}/bearings/${bearing}`;
  const [health, ratios] = await Promise.all([
    getJSON(`${path}/health-index`),
    getJSON(`${path}/ratios`),
  ]);
  state.bearing = bearing;
  remember();
  renderCards();
  markRange();

  const summary = state.bearings.find((b) => b.bearing === bearing);
  $("detail").hidden = false;
  $("detail-title").textContent = `${runLabel(state.run)} · Bearing ${bearing}`;
  $("detail-story").textContent =
    "Each feature divided by its median over the first 24 h, on the louder sensor; " +
    "the health index is the largest of them." +
    stopNote(health.timestamps);

  const chart = $("chart");
  await drawChart(health, ratios, summary.condition);
  chart.removeAllListeners("plotly_relayout");
  chart.on("plotly_relayout", followDrag);
}

// --- start ------------------------------------------------------------------------------------

async function start() {
  $("zoom-all").addEventListener("click", () => showRange(null));
  $("zoom-final").addEventListener("click", () => showRange("final"));
  const [experiments, version] = await Promise.all([
    getJSON(`${API}/experiments`),
    getJSON("/api/version"),
  ]);
  $("version").textContent = version.commit.slice(0, 7);
  renderRuns(experiments);
  const params = new URLSearchParams(location.search);
  const names = experiments.map((e) => e.name);
  const run = names.includes(params.get("run")) ? params.get("run") : names[0];
  await selectRun(run, Number(params.get("bearing")));
}

handle(start)();
