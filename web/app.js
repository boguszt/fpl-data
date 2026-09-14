const GITHUB_DATA =
  "https://raw.githubusercontent.com/boguszt/fpl-data/master/web/data";
const PAGE_SIZE = 50;
const DEFAULT_SEASON = "2025-26";
const DEFAULT_MIN = 600;
const POS_ORDER = ["GK", "DEF", "MID", "FWD"];
const POS_ID = { GK: 1, DEF: 2, MID: 3, FWD: 4 };
const HASH_KEY = "fpl.v1.hashes";
const IDB_NAME = "fpl-data";
const IDB_STORE = "files";

const SHORT = {
  xg: "xG",
  xa: "xA",
  xgi: "xGI",
  xgc: "xGC",
  goals: "G",
  assists: "A",
  goals_conceded: "GC",
  tackles: "Tck",
  recoveries: "Rec",
  clearances_blocks_interceptions: "CBI",
  defensive_contribution: "DefC",
  clean_sheets: "CS",
  saves: "Saves",
  penalties_saved: "PS",
  total_points: "Pts",
  bps: "BPS",
  bonus: "Bonus",
  yellow_cards: "YC",
  red_cards: "RC",
  own_goals: "OG",
  penalties_missed: "PM",
  minutes_share: "Min%",
};

const DEFAULT_METRICS = {
  GK: ["saves", "xgc", "goals_conceded", "clean_sheets", "penalties_saved"],
  DEF: [
    "defensive_contribution",
    "clearances_blocks_interceptions",
    "tackles",
    "recoveries",
    "xgi",
    "xg",
  ],
  MID: ["xgi", "xg", "xa", "defensive_contribution", "recoveries"],
  FWD: ["xg", "xa", "xgi", "goals"],
};

const DEFAULT_SHARE = { DEF: ["xg"], FWD: ["xg"] };

const DEFAULT_FRAMES = ["t", "a90"];
const FRAME_TOGGLES = [
  ["t", "tot"],
  ["p90", "/90"],
  ["a90", "Adj/90"],
  ["s", "sh"],
];
const FRAME_LABEL = { t: "tot", p90: "/90", a90: "Adj/90", s: "sh", rk: "rk" };
const FRAME_KIND = { t: "num", p90: "num", a90: "num", s: "share", rk: "rank" };
const FRAME_ORDER = ["t", "p90", "a90", "s", "rk"];
const DEFAULT_SORT = "min";

const localData = new URLSearchParams(location.search).has("local");
const dataBase = localData ? new URL("data/", location.href).href.replace(/\/$/, "") : GITHUB_DATA;

let dbPromise = null;
let cache = {
  manifest: null,
  metrics: [],
  regions: [],
  seasons: {},
  hashes: {},
  offline: false,
  generatedAt: null,
};
let state = readHash();
let openMenu = null;

function metricKey(name, frame) {
  if (name === "minutes_share") return "min_sh";
  if (frame === "rk") return `_rk_${name}`;
  return `${name}_${frame}`;
}

function csv(v) {
  return (v || "").split(",").map((s) => s.trim()).filter(Boolean);
}

function sameList(a, b) {
  return (a || []).join(",") === (b || []).join(",");
}

function readHash() {
  const p = new URLSearchParams(location.hash.replace(/^#/, ""));
  const minRaw = p.get("min");
  return {
    season: p.get("s") || DEFAULT_SEASON,
    min: minRaw === null || minRaw === "" ? DEFAULT_MIN : Number(minRaw),
    pos: csv(p.get("pos")),
    club: csv(p.get("club")),
    nat: csv(p.get("nat")),
    metrics: p.has("m") ? csv(p.get("m")) : null,
    frames: p.has("fr") ? csv(p.get("fr")) : DEFAULT_FRAMES.slice(),
    rank: p.get("rk") === "1",
    sort: p.get("sort") || DEFAULT_SORT,
    all: p.get("all") === "1",
    page: Math.max(0, Number(p.get("p") || 0) || 0),
  };
}

function writeHash() {
  const p = new URLSearchParams();
  if (state.season !== DEFAULT_SEASON) p.set("s", state.season);
  if (state.min !== DEFAULT_MIN) p.set("min", String(state.min));
  if (state.pos.length) p.set("pos", state.pos.join(","));
  if (state.club.length) p.set("club", state.club.join(","));
  if (state.nat.length) p.set("nat", state.nat.join(","));
  const names = onMetrics();
  if (state.metrics != null && !sameList(names, defaultMetricNames())) {
    p.set("m", names.join(","));
  }
  if ((state.frames || []).join(",") !== DEFAULT_FRAMES.join(",")) {
    p.set("fr", (state.frames || []).join(","));
  }
  if (state.rank) p.set("rk", "1");
  if (state.sort && state.sort !== DEFAULT_SORT) p.set("sort", state.sort);
  if (state.all) p.set("all", "1");
  if (state.page) p.set("p", String(state.page));
  const next = p.toString() ? `#${p.toString()}` : "";
  const cur = location.hash || "";
  if (cur !== next) {
    history.replaceState(null, "", next || `${location.pathname}${location.search}`);
  }
}

function loadHashes() {
  try {
    return JSON.parse(localStorage.getItem(HASH_KEY) || "{}");
  } catch {
    return {};
  }
}

function saveHashes(h) {
  localStorage.setItem(HASH_KEY, JSON.stringify(h));
}

function openDb() {
  if (dbPromise) return dbPromise;
  dbPromise = new Promise((resolve, reject) => {
    const req = indexedDB.open(IDB_NAME, 1);
    req.onupgradeneeded = () => req.result.createObjectStore(IDB_STORE);
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
  return dbPromise;
}

async function idbGet(key) {
  const db = await openDb();
  return new Promise((resolve, reject) => {
    const req = db.transaction(IDB_STORE, "readonly").objectStore(IDB_STORE).get(key);
    req.onsuccess = () => resolve(req.result ?? null);
    req.onerror = () => reject(req.error);
  });
}

async function idbSet(key, value) {
  const db = await openDb();
  return new Promise((resolve, reject) => {
    const req = db.transaction(IDB_STORE, "readwrite").objectStore(IDB_STORE).put(value, key);
    req.onsuccess = () => resolve();
    req.onerror = () => reject(req.error);
  });
}

async function fetchJson(file, noStore) {
  const url = `${dataBase}/${file}`;
  const res = await fetch(url, noStore ? { cache: "no-store" } : {});
  if (!res.ok) throw new Error(`${res.status} ${file}`);
  return res.json();
}

async function loadFile(file, hash, force) {
  const cachedHash = cache.hashes[file];
  if (!force && hash && cachedHash === hash) {
    const hit = await idbGet(file);
    if (hit != null) return hit;
  }
  const data = await fetchJson(file, true);
  await idbSet(file, data);
  cache.hashes[file] = hash;
  return data;
}

async function loadCached(file) {
  const hit = await idbGet(file);
  if (hit != null) return hit;
  throw new Error(`no cache for ${file}`);
}

function wantedFiles(manifest) {
  const files = [
    ["metrics.json", manifest.metrics?.hash],
    ["regions.json", manifest.regions?.hash],
  ];
  for (const s of manifest.seasons || []) files.push([s.file, s.hash]);
  return files;
}

async function loadAll(force = false) {
  cache.hashes = loadHashes();
  let manifest;
  try {
    manifest = await fetchJson("manifest.json", true);
    cache.offline = false;
  } catch (err) {
    const cached = await idbGet("manifest.json");
    if (!cached) throw err;
    manifest = cached;
    cache.offline = true;
  }
  cache.manifest = manifest;
  cache.generatedAt = manifest.generated_at;

  const files = wantedFiles(manifest);
  const wanted = new Set(files.map(([f]) => f));
  wanted.add("manifest.json");

  for (const [file, hash] of files) {
    try {
      if (cache.offline) {
        cacheFile(file, await loadCached(file));
      } else {
        cacheFile(file, await loadFile(file, hash, force));
      }
    } catch (err) {
      try {
        cacheFile(file, await loadCached(file));
        cache.offline = true;
      } catch {
        throw err;
      }
    }
  }

  await idbSet("manifest.json", manifest);
  const keep = new Set([...wanted, HASH_KEY]);
  const db = await openDb();
  await new Promise((resolve, reject) => {
    const store = db.transaction(IDB_STORE, "readwrite").objectStore(IDB_STORE);
    const req = store.getAllKeys();
    req.onsuccess = () => {
      for (const key of req.result) {
        if (!keep.has(key)) store.delete(key);
      }
      resolve();
    };
    req.onerror = () => reject(req.error);
  });
  for (const key of Object.keys(cache.hashes)) {
    if (!wanted.has(key)) delete cache.hashes[key];
  }
  saveHashes(cache.hashes);
}

function cacheFile(file, data) {
  if (file === "metrics.json") cache.metrics = data;
  else if (file === "regions.json") cache.regions = data;
  else if (file.startsWith("players_")) {
    const season = file.slice("players_".length, -".json".length);
    cache.seasons[season] = data;
  }
}

function metricByName(name) {
  return cache.metrics.find((m) => m.name === name);
}

function applies(metric, posIds) {
  const allowed = metric.applies_to_positions || [1, 2, 3, 4];
  return posIds.every((id) => allowed.includes(id));
}

function selectedPosIds() {
  if (!state.pos.length) return POS_ORDER.map((p) => POS_ID[p]);
  return state.pos.map((p) => POS_ID[p]);
}

function visibleMetrics() {
  const posIds = selectedPosIds();
  return cache.metrics.filter((m) => {
    if (state.all) return true;
    if (onMetrics().includes(m.name)) return true;
    return applies(m, posIds);
  });
}

function defaultMetricNames() {
  const poses = state.pos.length ? state.pos : POS_ORDER;
  let names = null;
  for (const p of poses) {
    const list = DEFAULT_METRICS[p] || [];
    names = names ? names.filter((n) => list.includes(n)) : list.slice();
  }
  return names || [];
}

function onMetrics() {
  return state.metrics == null ? defaultMetricNames() : state.metrics.slice();
}

function defaultShareNames() {
  const poses = state.pos.length ? state.pos : POS_ORDER;
  const names = new Set();
  for (const p of poses) {
    for (const n of DEFAULT_SHARE[p] || []) names.add(n);
  }
  if (poses.length > 1) {
    const common = DEFAULT_SHARE[poses[0]] || [];
    return common.filter((n) => poses.every((p) => (DEFAULT_SHARE[p] || []).includes(n)));
  }
  return [...names];
}

function numericFrames() {
  return (state.frames || []).filter((f) => f !== "s");
}

function shareOn() {
  return (state.frames || []).includes("s");
}

function sortFrames(frames) {
  return [...frames].sort((a, b) => {
    const ia = FRAME_ORDER.indexOf(a);
    const ib = FRAME_ORDER.indexOf(b);
    return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib);
  });
}

function seasonKeys() {
  const keys = new Set();
  for (const row of cache.seasons[state.season] || []) {
    for (const k of Object.keys(row)) keys.add(k);
  }
  return keys;
}

function frameExists(name, frame, keys) {
  if (frame === "rk") return true;
  if (name === "minutes_share") return keys.has("min_sh") || !keys.size;
  if (!keys.size) return true;
  return keys.has(metricKey(name, frame));
}

function framesForMetric(name) {
  if (name === "minutes_share") {
    const frames = ["s"];
    if (state.rank) frames.push("rk");
    return frames;
  }
  const frames = [];
  const extraShare = defaultShareNames().includes(name);
  if (numericFrames().includes("t")) frames.push("t");
  if (numericFrames().includes("p90")) frames.push("p90");
  if (numericFrames().includes("a90")) frames.push("a90");
  if (shareOn() || extraShare) frames.push("s");
  if (state.rank) frames.push("rk");
  return sortFrames(frames);
}

function columnSpec() {
  const cols = [
    { id: "player", block: "", label: "Player", kind: "name", sticky: true },
    { id: "club", block: "", label: "Club", kind: "name" },
    { id: "pos", block: "", label: "Pos", kind: "name" },
    { id: "nat", block: "", label: "Nat", kind: "name" },
    { id: "min", block: "", label: "Min", kind: "num", sortable: true },
  ];
  const keys = seasonKeys();
  const names = onMetrics();
  const ordered = cache.metrics.filter((m) => names.includes(m.name)).map((m) => m.name);
  for (const n of names) {
    if (!ordered.includes(n)) ordered.push(n);
  }

  const add = (name, frame) => {
    const meta = metricByName(name);
    if (!meta) return;
    if (!frameExists(name, frame, keys)) return;
    const short = SHORT[name] || meta.display_name;
    const frameLabel = name === "minutes_share" && frame === "s" ? "" : FRAME_LABEL[frame] || "";
    cols.push({
      id: metricKey(name, frame),
      block: frame === "t" ? "observed" : "derived",
      label: `${short} ${frameLabel}`.trim(),
      kind: name === "minutes_share" && frame === "s" ? "share" : FRAME_KIND[frame] || "num",
      metric: name,
      frame,
      format: meta.format,
      sortable: true,
      direction: meta.direction || "higher",
    });
  };

  for (const name of ordered) {
    for (const frame of framesForMetric(name)) {
      if (frame === "t") add(name, frame);
    }
  }
  for (const name of ordered) {
    for (const frame of framesForMetric(name)) {
      if (frame !== "t") add(name, frame);
    }
  }
  return cols;
}

function cellValue(row, col) {
  if (col.id === "player") return row.player;
  if (col.id === "club") return row.club;
  if (col.id === "pos") return row.pos;
  if (col.id === "nat") return row.nat;
  if (col.id === "min") return row.min;
  if (col.frame === "rk") {
    const rank = row[`_rk_${col.metric}`];
    const n = row[`_rkn_${col.metric}`];
    if (rank == null || n == null) return null;
    return { rank, n };
  }
  if (col.metric === "minutes_share") return row.min_sh;
  return row[col.id];
}

function formatCell(v, col) {
  if (v == null) return "—";
  if (typeof v === "number" && Number.isNaN(v)) return "—";
  if (col.kind === "name") return v;
  if (col.kind === "rank") return `${v.rank} / ${v.n}`;
  if (col.kind === "share" || col.format === "percent" || col.metric === "minutes_share") {
    return `${Math.round(Number(v) * 100)}%`;
  }
  if (col.id === "min") return String(Math.round(v));
  if (col.frame === "p90" || col.frame === "a90" || col.format === "2dp") {
    return Number(v).toFixed(2);
  }
  if (col.format === "1dp") return Number(v).toFixed(1);
  return String(Math.round(v));
}

function rankKey(name) {
  if (name === "minutes_share") return "min_sh";
  return `${name}_a90`;
}

function applyRanks() {
  if (!state.rank) return;
  const seasonRows = cache.seasons[state.season] || [];
  for (const name of onMetrics()) {
    const meta = metricByName(name);
    const key = rankKey(name);
    const lower = (meta?.direction || "higher") === "lower";
    const byPos = new Map();
    for (const row of seasonRows) {
      row[`_rk_${name}`] = null;
      row[`_rkn_${name}`] = null;
      const v = row[key];
      if (v == null || Number.isNaN(v)) continue;
      const pos = row.pos || "";
      const list = byPos.get(pos) || [];
      list.push({ row, v });
      byPos.set(pos, list);
    }
    for (const group of byPos.values()) {
      group.sort((a, b) => (lower ? a.v - b.v : b.v - a.v));
      const n = group.length;
      let prev = null;
      let rank = 0;
      for (let k = 0; k < group.length; k++) {
        if (prev == null || group[k].v !== prev) rank = k + 1;
        group[k].row[`_rk_${name}`] = rank;
        group[k].row[`_rkn_${name}`] = n;
        prev = group[k].v;
      }
    }
  }
}

function clubParts(club) {
  return (club || "").split(" / ").map((s) => s.trim()).filter(Boolean);
}

function sortValue(row, col) {
  if (!col) return null;
  if (col.frame === "rk") return row[`_rk_${col.metric}`] ?? null;
  const v = cellValue(row, col);
  if (v == null || (typeof v === "number" && Number.isNaN(v))) return null;
  if (typeof v === "number") return v;
  return null;
}

function filteredRows(cols) {
  applyRanks();
  const rows = cache.seasons[state.season] || [];
  const min = Number.isFinite(state.min) ? state.min : 0;
  const pos = new Set(state.pos);
  const clubs = new Set(state.club);
  const nats = new Set(state.nat);
  const out = [];
  for (const src of rows) {
    if ((src.min ?? 0) < min) continue;
    if (pos.size && !pos.has(src.pos)) continue;
    if (clubs.size && !clubParts(src.club).some((c) => clubs.has(c))) continue;
    if (nats.size && !nats.has(src.nat)) continue;
    out.push(src);
  }
  const sortCol = cols.find((c) => c.id === state.sort) || cols.find((c) => c.id === DEFAULT_SORT);
  out.sort((a, b) => {
    const av = sortValue(a, sortCol);
    const bv = sortValue(b, sortCol);
    if (av == null && bv == null) return (a.player || "").localeCompare(b.player || "");
    if (av == null) return 1;
    if (bv == null) return -1;
    const d = bv - av;
    if (d) return d;
    return (a.player || "").localeCompare(b.player || "");
  });
  return out;
}

function seasonOptions() {
  return (cache.manifest?.seasons || []).map((s) => s.season);
}

function optionLists(season) {
  const rows = cache.seasons[season] || [];
  const clubs = new Set();
  const nats = new Set();
  for (const r of rows) {
    for (const c of clubParts(r.club)) clubs.add(c);
    if (r.nat) nats.add(r.nat);
  }
  return {
    clubs: [...clubs].sort(),
    nats: [...nats].sort(),
  };
}

function filterCount() {
  let n = state.pos.length + state.club.length + state.nat.length;
  if (state.min !== DEFAULT_MIN) n += 1;
  return n;
}

function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2), v);
    else if (v === true) node.setAttribute(k, "");
    else if (v === false || v == null) continue;
    else node.setAttribute(k, v);
  }
  for (const child of children) {
    if (child == null) continue;
    node.append(child.nodeType ? child : document.createTextNode(child));
  }
  return node;
}

function closeMenus() {
  if (openMenu) {
    openMenu.remove();
    openMenu = null;
  }
}

function mountMulti(host, label, options, selected, onChange, id) {
  const btn = el("button", {
    type: "button",
    class: `ms-btn${selected.length ? " active" : ""}`,
    id,
    text: selected.length ? `${label} · ${selected.length}` : label,
  });
  btn.addEventListener("click", (e) => {
    e.stopPropagation();
    if (openMenu && openMenu.dataset.for === id) {
      closeMenus();
      return;
    }
    closeMenus();
    const panel = el("div", { class: "ms-panel", "data-for": id });
    for (const opt of options) {
      const on = selected.includes(opt);
      const inp = el("input", { type: "checkbox" });
      inp.checked = on;
      const row = el("label", {}, [inp, opt]);
      inp.addEventListener("change", () => {
        const next = new Set(selected);
        if (next.has(opt)) next.delete(opt);
        else next.add(opt);
        onChange([...next]);
      });
      panel.append(row);
    }
    host.append(panel);
    openMenu = panel;
  });
  host.append(btn);
}

function renderFilters() {
  const root = document.getElementById("filters");
  root.replaceChildren();
  const seasons = seasonOptions();
  if (!seasons.includes(state.season) && seasons.length) state.season = seasons[0];
  const opts = optionLists(state.season);
  const maxMin = Math.max(1, ...(cache.seasons[state.season] || []).map((r) => r.min || 0));

  const seasonField = el("div", { class: "field" }, [el("label", { text: "Season" })]);
  const seasonSel = el("select");
  for (const s of seasons) {
    const o = el("option", { value: s, text: s });
    if (s === state.season) o.selected = true;
    seasonSel.append(o);
  }
  seasonSel.addEventListener("change", () => {
    state.season = seasonSel.value;
    state.club = [];
    state.nat = [];
    state.page = 0;
    render();
  });
  seasonField.append(seasonSel);
  root.append(seasonField);

  const clubField = el("div", { class: "field" });
  clubField.append(el("label", { text: "Club" }));
  mountMulti(clubField, "Club", opts.clubs, state.club, (v) => {
    state.club = v;
    state.page = 0;
    render();
  }, "club-filter");
  root.append(clubField);

  const posField = el("div", { class: "field" });
  posField.append(el("label", { text: "Pos" }));
  mountMulti(posField, "Pos", POS_ORDER, state.pos, (v) => {
    const wasDefault = state.metrics == null || sameList(state.metrics, defaultMetricNames());
    state.pos = v;
    if (wasDefault) state.metrics = null;
    state.page = 0;
    render();
  }, "pos-filter");
  root.append(posField);

  const natField = el("div", { class: "field" });
  natField.append(el("label", { text: "Nat" }));
  mountMulti(natField, "Nat", opts.nats, state.nat, (v) => {
    state.nat = v;
    state.page = 0;
    render();
  }, "nat-filter");
  root.append(natField);

  const minField = el("div", { class: "field" }, [el("label", { text: "Minutes" })]);
  const wrap = el("div", { class: "min-wrap" });
  const slider = el("input", {
    type: "range",
    min: "0",
    max: String(maxMin),
    value: String(Math.min(state.min, maxMin)),
  });
  const val = el("span", { text: String(Math.min(state.min, maxMin)) });
  slider.addEventListener("input", () => {
    val.textContent = slider.value;
  });
  slider.addEventListener("change", () => {
    state.min = Number(slider.value);
    state.page = 0;
    render();
  });
  wrap.append(slider, val);
  minField.append(wrap);
  root.append(minField);

  const n = filterCount();
  const meta = el("div", { class: "filter-meta" });
  if (n) {
    meta.append(`${n} active`);
    const clear = el("button", { type: "button", text: "clear" });
    clear.addEventListener("click", () => {
      state.pos = [];
      state.club = [];
      state.nat = [];
      state.min = DEFAULT_MIN;
      state.metrics = null;
      state.page = 0;
      render();
    });
    meta.append(clear);
  }
  root.append(meta);
}

function toggleMetric(name) {
  const cur = new Set(onMetrics());
  if (cur.has(name)) cur.delete(name);
  else cur.add(name);
  const next = cache.metrics.map((m) => m.name).filter((n) => cur.has(n));
  state.metrics = next;
  if (sameList(next, defaultMetricNames())) state.metrics = null;
}

function renderChips() {
  const root = document.getElementById("chips");
  root.replaceChildren();
  const wrap = el("div", { class: "chips" });
  const selected = new Set(onMetrics());
  const groups = [];
  for (const m of visibleMetrics()) {
    let g = groups.find((x) => x.group === m.group);
    if (!g) {
      g = { group: m.group, items: [] };
      groups.push(g);
    }
    g.items.push(m);
  }
  for (const g of groups) {
    const box = el("div", { class: "chip-group" }, [el("div", { class: "g", text: g.group })]);
    for (const m of g.items) {
      const on = selected.has(m.name);
      const btn = el("button", {
        type: "button",
        class: `chip${on ? " on" : ""}`,
        text: m.display_name,
      });
      btn.addEventListener("click", () => {
        toggleMetric(m.name);
        state.page = 0;
        render();
      });
      box.append(btn);
    }
    wrap.append(box);
  }
  const frameBox = el("div", { class: "chip-group" }, [el("div", { class: "g", text: "Values" })]);
  const onFrames = new Set(state.frames || []);
  for (const [key, label] of FRAME_TOGGLES) {
    const on = onFrames.has(key);
    const btn = el("button", { type: "button", class: `chip${on ? " on" : ""}`, text: label });
    btn.addEventListener("click", () => {
      const cur = new Set(state.frames || []);
      if (cur.has(key)) cur.delete(key);
      else cur.add(key);
      state.frames = FRAME_TOGGLES.map(([k]) => k).filter((k) => cur.has(k));
      render();
    });
    frameBox.append(btn);
  }
  const rankBtn = el("button", {
    type: "button",
    class: `chip${state.rank ? " on" : ""}`,
    text: "Rank",
  });
  rankBtn.addEventListener("click", () => {
    state.rank = !state.rank;
    render();
  });
  frameBox.append(rankBtn);
  wrap.append(frameBox);
  const all = el("button", {
    type: "button",
    class: "show-all",
    text: state.all ? "Hide extra metrics" : "Show all metrics",
  });
  all.addEventListener("click", () => {
    state.all = !state.all;
    render();
  });
  wrap.append(all);
  root.append(wrap);
}

function renderPager(n, pages) {
  const root = document.getElementById("pager");
  root.replaceChildren();
  const prev = el("button", { type: "button", text: "Prev" });
  prev.disabled = state.page <= 0;
  prev.addEventListener("click", () => {
    state.page = Math.max(0, state.page - 1);
    render();
  });
  const next = el("button", { type: "button", text: "Next" });
  next.disabled = state.page >= pages - 1;
  next.addEventListener("click", () => {
    state.page = Math.min(pages - 1, state.page + 1);
    render();
  });
  root.append(
    prev,
    el("span", { text: `${n} players · page ${state.page + 1} / ${pages}` }),
    next,
  );
}

function renderTable(pageRows, cols) {
  const wrap = document.getElementById("table-wrap");
  wrap.replaceChildren();
  const table = el("table");
  const thead = el("thead");
  const gRow = el("tr", { class: "groups" });
  const mRow = el("tr", { class: "metrics" });
  let i = 0;
  while (i < cols.length) {
    if (!cols[i].block) {
      gRow.append(el("th", { class: cols[i].sticky ? "sticky" : "" }));
      i += 1;
      continue;
    }
    const g = cols[i].block;
    let span = 1;
    while (i + span < cols.length && cols[i + span].block === g) span += 1;
    gRow.append(el("th", { colspan: String(span), text: g }));
    i += span;
  }
  for (const col of cols) {
    const cls = [
      col.sticky ? "sticky" : "",
      col.kind === "name" ? "name" : "num",
      col.id === "nat" ? "nat" : "",
      col.id === "club" ? "club" : "",
      col.sortable ? "sortable" : "",
      state.sort === col.id ? "sorted" : "",
    ]
      .filter(Boolean)
      .join(" ");
    const th = el("th", { class: cls, text: col.label });
    if (col.sortable) {
      th.addEventListener("click", () => {
        state.sort = col.id;
        state.page = 0;
        render();
      });
    }
    mRow.append(th);
  }
  thead.append(gRow, mRow);
  const tbody = el("tbody");
  for (const row of pageRows) {
    const tr = el("tr");
    for (const col of cols) {
      const v = cellValue(row, col);
      const cls = [
        col.sticky ? "sticky" : "",
        col.kind === "name" ? "name" : "num",
        (col.frame === "p90" || col.frame === "a90" || col.frame === "t") ? "dim" : "",
        col.kind === "rank" ? "rank" : "",
        col.id === "nat" ? "nat" : "",
        col.id === "club" ? "club" : "",
      ]
        .filter(Boolean)
        .join(" ");
      const td = el("td", { class: cls, text: formatCell(v, col) });
      if ((col.kind === "share" || col.frame === "s") && v == null) {
        td.title = "Share of team total while at that club. Blank before 2020-21.";
      }
      tr.append(td);
    }
    tbody.append(tr);
  }
  table.append(thead, tbody);
  wrap.append(table);
}

function relativeAge(iso) {
  if (!iso) return "";
  const ms = Date.now() - Date.parse(iso);
  if (!Number.isFinite(ms)) return iso;
  const hours = ms / 3_600_000;
  if (hours < 1) {
    const m = Math.max(1, Math.round(ms / 60_000));
    return m === 1 ? "1 minute old" : `${m} minutes old`;
  }
  if (hours < 48) {
    const h = Math.max(1, Math.round(hours));
    return h === 1 ? "1 hour old" : `${h} hours old`;
  }
  const d = Math.max(1, Math.round(hours / 24));
  return d === 1 ? "1 day old" : `${d} days old`;
}

function renderFreshness() {
  const btn = document.getElementById("freshness");
  const ageH = cache.generatedAt ? (Date.now() - Date.parse(cache.generatedAt)) / 3_600_000 : 99;
  const stale = cache.offline || ageH > 36;
  btn.classList.toggle("amber", stale);
  if (cache.offline) {
    btn.textContent = `offline — cached data from ${relativeAge(cache.generatedAt).replace(" old", " ago")}`;
  } else {
    btn.textContent = `data ${relativeAge(cache.generatedAt)}`;
  }
}

function render() {
  closeMenus();
  writeHash();
  const cols = columnSpec();
  if (!cols.some((c) => c.id === state.sort)) state.sort = DEFAULT_SORT;
  const rows = filteredRows(cols);
  const pages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
  if (state.page > pages - 1) state.page = pages - 1;
  const slice = rows.slice(state.page * PAGE_SIZE, state.page * PAGE_SIZE + PAGE_SIZE);
  renderFilters();
  renderChips();
  document.getElementById("note").hidden = true;
  renderPager(rows.length, pages);
  const empty = document.getElementById("empty");
  const wrap = document.getElementById("table-wrap");
  if (!rows.length) {
    wrap.replaceChildren();
    empty.hidden = false;
    empty.textContent = "No players match these filters. ";
    const clear = el("button", { type: "button", text: "clear" });
    clear.addEventListener("click", () => {
      state.pos = [];
      state.club = [];
      state.nat = [];
      state.min = DEFAULT_MIN;
      state.metrics = null;
      state.page = 0;
      render();
    });
    empty.append(clear);
  } else {
    empty.hidden = true;
    empty.replaceChildren();
    renderTable(slice, cols);
  }
  renderFreshness();
}

function onKey(e) {
  const tag = (e.target && e.target.tagName) || "";
  const typing = tag === "INPUT" || tag === "SELECT" || tag === "TEXTAREA";
  if (e.key === "/" && !typing) {
    e.preventDefault();
    const btn = document.getElementById("club-filter");
    if (btn) btn.click();
    return;
  }
  if (e.key === "Escape") {
    if (openMenu) {
      closeMenus();
      return;
    }
    if (state.metrics != null) {
      state.metrics = null;
      state.page = 0;
      render();
    }
    return;
  }
  if (typing) return;
  if (e.key === "ArrowRight" || e.key === "ArrowDown") {
    e.preventDefault();
    state.page += 1;
    render();
  } else if (e.key === "ArrowLeft" || e.key === "ArrowUp") {
    e.preventDefault();
    state.page = Math.max(0, state.page - 1);
    render();
  }
}

async function boot() {
  document.addEventListener("click", closeMenus);
  document.addEventListener("keydown", onKey);
  window.addEventListener("hashchange", () => {
    state = readHash();
    render();
  });
  document.getElementById("freshness").addEventListener("click", async () => {
    await loadAll(true);
    render();
  });
  try {
    await loadAll(false);
  } catch (err) {
    document.getElementById("empty").hidden = false;
    document.getElementById("empty").textContent =
      "No data yet. Serve web/ with ?local=1, or wait for GitHub JSON.";
    console.error(err);
    return;
  }
  render();
}

boot();
