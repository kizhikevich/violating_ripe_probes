const els = {
  status: document.getElementById("data-status"),
  probeId: document.getElementById("probe-id"),
  days: document.getElementById("lookback-days"),
  buffer: document.getElementById("buffer-toggle"),
  letters: document.getElementById("letters-toggle"),
  check: document.getElementById("check-button"),
  message: document.getElementById("message"),
  results: document.getElementById("results"),
  banner: document.getElementById("result-banner"),
  metricDays: document.getElementById("metric-days"),
  metricViolatingDays: document.getElementById("metric-violating-days"),
  metricRoots: document.getElementById("metric-roots"),
  metricSlots: document.getElementById("metric-slots"),
  probeMeta: document.getElementById("probe-meta"),
  violationSection: document.getElementById("violation-section"),
  ruleDescription: document.getElementById("rule-description"),
  metricLetters: document.getElementById("metric-letters"),
  metricMargin: document.getElementById("metric-margin"),
  violationsBody: document.querySelector("#violations-table tbody"),
  dailyBody: document.querySelector("#daily-table tbody"),
};

let indexData = null;
let map = null;
let mapLayer = null;
const dayCache = new Map();

function showMessage(text) {
  els.message.textContent = text;
  els.message.classList.remove("hidden");
  els.results.classList.add("hidden");
}

function clearMessage() {
  els.message.classList.add("hidden");
}

function formatNumber(value, digits = 1) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
  return Number(value).toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function formatSlots(slots) {
  if (!Array.isArray(slots) || slots.length === 0) return "—";
  return slots.map((slot) => `${String(slot).padStart(2, "0")}:00`).join(", ");
}

function flagKey() {
  return `${els.buffer.checked ? "100" : "0"}_${els.letters.checked ? "2" : "1"}`;
}

function thresholdKm() {
  return els.buffer.checked ? 100 : 0;
}

function minLetters() {
  return els.letters.checked ? 2 : 1;
}

function selectedDates() {
  if (!indexData?.latest) return [];
  const n = Math.max(1, Math.min(30, Number.parseInt(els.days.value || "1", 10)));
  const latest = new Date(`${indexData.latest}T00:00:00Z`);
  const earliest = new Date(latest);
  earliest.setUTCDate(latest.getUTCDate() - (n - 1));

  return indexData.dates.filter((dateString) => {
    const d = new Date(`${dateString}T00:00:00Z`);
    return d >= earliest && d <= latest;
  });
}

async function loadDay(date) {
  if (dayCache.has(date)) return dayCache.get(date);
  const response = await fetch(`data/${date}.json`, { cache: "no-store" });
  if (!response.ok) throw new Error(`Could not load data/${date}.json`);
  const data = await response.json();
  dayCache.set(date, data);
  return data;
}

function buildDailyTable(rows) {
  els.dailyBody.innerHTML = "";
  for (const row of rows) {
    const tr = document.createElement("tr");
    const values = [
      row.date,
      row.probe.counts["0km"],
      row.probe.counts["100km"],
      row.probe.roots_observed ?? "—",
      formatSlots(row.day.slots),
    ];
    values.forEach((value) => {
      const td = document.createElement("td");
      td.textContent = value;
      tr.appendChild(td);
    });
    els.dailyBody.appendChild(tr);
  }
}

function buildViolationsTable(details, bufferKm) {
  els.violationsBody.innerHTML = "";
  const sorted = [...details].sort((a, b) => {
    const rootCmp = (a.root || "").localeCompare(b.root || "");
    if (rootCmp !== 0) return rootCmp;
    return b.date.localeCompare(a.date);
  });

  for (const row of sorted) {
    const tr = document.createElement("tr");
    const margin = Number(row.excess_km) - bufferKm;
    const values = [
      row.date,
      row.root,
      row.root_ns || row.hostname || "—",
      row.slot ? `${String(row.slot).padStart(2, "0")}:00` : "—",
      formatNumber(row.rtt_ms),
      formatNumber(row.distance_km),
      formatNumber(row.max_feasible_km),
      formatNumber(margin),
    ];
    values.forEach((value) => {
      const td = document.createElement("td");
      td.textContent = value;
      tr.appendChild(td);
    });
    els.violationsBody.appendChild(tr);
  }
}

function makeCircleIcon(color, size = 16) {
  return L.divIcon({
    className: "",
    html: `<div class="circle-marker" style="width:${size}px;height:${size}px;background:${color}"></div>`,
    iconSize: [size, size],
    iconAnchor: [size / 2, size / 2],
  });
}

function renderMap(probe, details) {
  const mapEl = document.getElementById("map");
  if (map) {
    map.remove();
    map = null;
  }

  map = L.map(mapEl, { worldCopyJump: true });
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 18,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
  }).addTo(map);

  mapLayer = L.layerGroup().addTo(map);
  const bounds = [];

  if (probe.latitude !== null && probe.longitude !== null) {
    const lat = Number(probe.latitude);
    const lon = Number(probe.longitude);
    L.marker([lat, lon], { icon: makeCircleIcon("#2563eb", 18) })
      .bindTooltip(`Probe — reported location`)
      .addTo(mapLayer);
    bounds.push([lat, lon]);
  }

  const seen = new Set();
  for (const row of details) {
    if (row.dns_lat === null || row.dns_lon === null) continue;
    const key = `${row.root}|${row.root_ns || row.hostname}|${row.dns_lat}|${row.dns_lon}`;
    if (seen.has(key)) continue;
    seen.add(key);

    const lat = Number(row.dns_lat);
    const lon = Number(row.dns_lon);
    const label = `${row.root}-root — ${row.root_ns || row.hostname || "instance"}`;
    L.marker([lat, lon], { icon: makeCircleIcon("#ef4444", 15) })
      .bindTooltip(label)
      .addTo(mapLayer);
    bounds.push([lat, lon]);
  }

  if (bounds.length > 1) {
    map.fitBounds(bounds, { padding: [35, 35], maxZoom: 6 });
  } else if (bounds.length === 1) {
    map.setView(bounds[0], 5);
  } else {
    map.setView([20, 0], 2);
  }

  setTimeout(() => map.invalidateSize(), 50);
}

async function checkProbe() {
  clearMessage();
  els.check.disabled = true;
  els.check.textContent = "Checking…";

  try {
    const probeId = String(Number.parseInt(els.probeId.value, 10));
    if (!probeId || probeId === "NaN" || Number(probeId) < 1) {
      showMessage("Enter a valid RIPE Atlas probe ID.");
      return;
    }

    const dates = selectedDates();
    if (dates.length === 0) {
      showMessage("No processed data is available in that date window.");
      return;
    }

    const dayData = await Promise.all(dates.map(loadDay));
    const rows = dayData
      .map((day) => ({ date: day.date, day, probe: day.probes[probeId] }))
      .filter((row) => row.probe);

    if (rows.length === 0) {
      showMessage(`Probe ${probeId} does not appear in the processed data for the selected window.`);
      return;
    }

    rows.sort((a, b) => b.date.localeCompare(a.date));
    const key = flagKey();
    const violatingRows = rows.filter((row) => Boolean(row.probe.flags[key]));
    const violates = violatingRows.length > 0;
    const latestRow = rows[0];

    els.results.classList.remove("hidden");
    els.banner.className = `result-banner ${violates ? "danger" : "success"}`;
    els.banner.textContent = violates
      ? `SOI violation detected for probe ${probeId} on ${violatingRows.length} day(s) in the selected window.`
      : `No SOI violation detected for probe ${probeId} in the selected window.`;

    els.metricDays.textContent = rows.length;
    els.metricViolatingDays.textContent = violatingRows.length;
    els.metricRoots.textContent = latestRow.probe.roots_observed ?? "—";
    els.metricSlots.textContent = formatSlots(latestRow.day.slots);

    const meta = [];
    if (latestRow.probe.country) meta.push(`country: ${latestRow.probe.country}`);
    if (latestRow.probe.asn_v4 !== null && latestRow.probe.asn_v4 !== undefined) {
      meta.push(`IPv4 ASN: AS${Math.trunc(Number(latestRow.probe.asn_v4))}`);
    }
    els.probeMeta.textContent = meta.join(" · ");
    buildDailyTable(rows);

    if (!violates) {
      els.violationSection.classList.add("hidden");
      return;
    }

    const bufferKm = thresholdKm();
    const details = [];
    for (const row of violatingRows) {
      for (const violation of row.probe.violations || []) {
        if (Number(violation.excess_km) > bufferKm) {
          details.push({ ...violation, date: row.date });
        }
      }
    }

    els.violationSection.classList.remove("hidden");
    els.ruleDescription.textContent = `Rule applied: ≥${minLetters()} root letter${minLetters() > 1 ? "s" : ""}, ${bufferKm} km tolerance.`;

    const letters = [...new Set(details.map((row) => row.root).filter(Boolean))].sort();
    els.metricLetters.textContent = letters.join(", ") || "—";
    const margins = details.map((row) => Number(row.excess_km) - bufferKm).filter(Number.isFinite);
    els.metricMargin.textContent = margins.length ? `${Math.max(...margins).toLocaleString(undefined, { maximumFractionDigits: 0 })} km` : "—";

    buildViolationsTable(details, bufferKm);
    renderMap(latestRow.probe, details);
  } catch (error) {
    console.error(error);
    showMessage(`The checker could not load its data: ${error.message}`);
  } finally {
    els.check.disabled = false;
    els.check.textContent = "Check probe";
  }
}

async function init() {
  try {
    const response = await fetch("data/index.json", { cache: "no-store" });
    if (!response.ok) throw new Error("data/index.json is missing");
    indexData = await response.json();

    if (!indexData.latest) {
      throw new Error("No processed daily data has been published yet");
    }

    els.status.textContent = `Newest data: ${indexData.latest} UTC`;
    els.days.max = Math.min(30, Math.max(1, indexData.dates.length));
  } catch (error) {
    console.error(error);
    els.status.textContent = "No processed data available";
    showMessage("No processed RIPE Atlas data is available yet. Run the data-update workflow first.");
    els.check.disabled = true;
  }
}

els.check.addEventListener("click", checkProbe);
els.probeId.addEventListener("keydown", (event) => {
  if (event.key === "Enter") checkProbe();
});

init();
