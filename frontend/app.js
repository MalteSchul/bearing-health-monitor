"use strict";

// Display only: every value shown is computed by the API, nothing is judged here.

const API = "/api/v1";

// A colour is an action: grey wait, green nothing, amber plan the replacement, red act now.
// Crosstalk is green because this bearing needs nothing; its band shows why the index is high.
// band: legend name of the shading behind the health index, null for none.
const STATUS = {
  baseline: { label: "Learning", band: "learning", colour: "#8a8983", shade: "rgba(138, 137, 131, 0.22)" },
  ok: { label: "OK", band: null, colour: "#2e9d5b", shade: null },
  crosstalk: { label: "OK", band: "crosstalk", colour: "#2e9d5b", shade: "rgba(46, 157, 91, 0.14)" },
  alert: { label: "Alert", band: "alert", colour: "#d99a00", shade: "rgba(217, 154, 0, 0.2)" },
  danger: { label: "Danger", band: "danger", colour: "#d03b3b", shade: "rgba(208, 59, 59, 0.13)" },
};

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

// range: null for the whole run, "final" for its last 100 h, or [from, to] dragged in a chart.
// health: each bearing's health-index series of the current run, which the overview draws.
const state = { run: null, last: null, bearing: null, bearings: [], health: {}, range: null };

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
  const series = await Promise.all(
    experiment.bearings.map((b) => getJSON(`${API}/experiments/${name}/bearings/${b.bearing}/health-index`)),
  );
  // "Final 100 h" means the same in every run; a dragged range does not.
  if (name !== state.run && state.range !== "final") state.range = null;
  state.run = name;
  state.last = experiment.last;
  state.bearings = experiment.bearings;
  state.health = Object.fromEntries(series.map((health) => [health.bearing, health]));
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
  if (condition.status === "ok") facts.push("No feature held above the alert threshold for 1 h");
  if (condition.status === "crosstalk") {
    // Here the diagnosis is the part of the fault it hears, not one of its own.
    const fault = condition.diagnosis === null ? "fault" : `${condition.diagnosis} fault`;
    facts.push(`Hears bearing ${condition.crosstalk_from}'s ${fault}: no action here`);
  }
  if (condition.status === "alert") facts.push("<strong>Plan the replacement</strong>");
  if (condition.status === "danger") facts.push("<strong>Act now:</strong> reduce load or stop");
  if (condition.alert_at !== null) facts.push(`Alert raised ${formatTime(condition.alert_at)}`);
  if (condition.danger_at !== null) facts.push(`Danger raised ${formatTime(condition.danger_at)}`);
  if (condition.diagnosis !== null && condition.status !== "crosstalk") {
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

function drawChart(health, ratios, condition) {
  const names = Object.keys(FEATURES);
  const { x, ys } = breakAtGaps(health.timestamps, [
    health.index,
    health.driver.map((d) => (d === null ? "" : FEATURES[d].label)),
    ...names.map((name) => ratios[name]),
  ]);
  const [index, driver, ...features] = ys;
  const cuts = longStops(health.timestamps);
  const marks = escalations(condition);

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
    key("alert raised", { mode: "lines", line: { color: STATUS.alert.colour, width: 1.5 } }),
    key("danger raised", { mode: "lines", line: { color: STATUS.danger.colour, width: 1.5 } }),
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
  ["y", "y2", "y3"].forEach((axis) => {
    shapes.push(
      thresholdShape(health.threshold, axis),
      ...momentShapes(marks, axis),
      ...momentShapes(stopMoments(cuts), axis),
    );
  });

  const title = { yshift: 8, font: { size: 13, color: INK, weight: 600 } };
  const annotations = [
    note(
      `Health index and status · alert: ${health.threshold}× for 1 h on own evidence · ` +
        `danger: rms ${health.threshold}× as well`,
      0,
      DOMAINS[0][1],
      title,
    ),
    note("Envelope features · one per bearing part", 0, DOMAINS[1][1], title),
    note(
      "Time-domain features · overall level and impulsiveness · rms decides danger",
      0,
      DOMAINS[2][1],
      title,
    ),
    note("Click a name to hide its line,<br>double-click to show only that one.", 1.02, 0, {
      font: { size: 11, color: MUTED },
    }),
  ];
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
    height: 760,
    margin: MARGIN,
    paper_bgcolor: "rgba(0,0,0,0)",
    plot_bgcolor: "#ffffff",
    font: { family: FONT, size: 12, color: INK },
    // One hover label for all three panels: every value at that moment.
    hovermode: "x unified",
    hoversubplots: "axis",
    hoverlabel: { bgcolor: "rgba(255, 255, 255, 0.96)", bordercolor: GRID },
    xaxis: timeAxis(cuts, "y3"),
    yaxis: logAxis(DOMAINS[0]),
    yaxis2: logAxis(DOMAINS[1]),
    yaxis3: logAxis(DOMAINS[2]),
    legend: legendBeside(DOMAINS[0]),
    legend2: legendBeside(DOMAINS[1]),
    legend3: legendBeside(DOMAINS[2]),
    shapes,
    annotations,
  };
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

const hindsight = (summary) =>
  summary.documented_failure === null ? "survived" : `documented: ${summary.documented_failure}`;

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
      note(hindsight(summary), 1.02, (bottom + top) / 2, {
        yanchor: "middle",
        font: { size: 12, color: MUTED },
      }),
    );
  });
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

function selectLane(click) {
  const bearing = laneAt(click.event);
  if (bearing !== state.bearing) handle(() => selectBearing(bearing))();
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

// --- bearing detail ---------------------------------------------------------------------------

/** Set after each draw; removing the old handlers first keeps one per event. */
function listen(id, handlers) {
  const chart = $(id);
  for (const [event, handler] of Object.entries(handlers)) {
    chart.removeAllListeners(event);
    chart.on(event, handler);
  }
}

async function selectBearing(bearing) {
  const ratios = await getJSON(`${API}/experiments/${state.run}/bearings/${bearing}/ratios`);
  state.bearing = bearing;
  remember();
  renderCards();
  markRange();

  const summary = state.bearings.find((b) => b.bearing === bearing);
  // Shown before drawing: Plotly sizes a chart from its container.
  $("overview").hidden = false;
  $("overview-title").textContent = `${runLabel(state.run)} · all bearings`;
  $("detail").hidden = false;
  $("detail-title").textContent = `${runLabel(state.run)} · Bearing ${bearing}`;

  await Promise.all([drawOverview(), drawChart(state.health[bearing], ratios, summary.condition)]);
  listen("overview-chart", { plotly_relayout: followDrag("overview-chart"), plotly_click: selectLane });
  listen("chart", {
    plotly_relayout: followDrag("chart"),
    plotly_legendclick: featuresOnly,
    plotly_legenddoubleclick: featuresOnly,
  });
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
