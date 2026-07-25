(function () {
  const form = document.getElementById("historyFilters");
  const fromInput = document.getElementById("historyFrom");
  const toInput = document.getElementById("historyTo");
  const periodInput = document.getElementById("historyPeriod");
  const inverterInput = document.getElementById("historyInverter");
  const chartEl = document.getElementById("historyChart");
  const statusEl = document.getElementById("historyStatus");
  const csvButton = document.getElementById("historyCsv");
  const dayInput = document.getElementById("historyDay");
  const previousDayButton = document.getElementById("historyPreviousDay");
  const nextDayButton = document.getElementById("historyNextDay");
  const dayPowerEl = document.getElementById("historyDayPowerChart");
  const dayComponentEl = document.getElementById("historyDayComponentChart");
  const componentMetricButtons = Array.from(
    document.querySelectorAll("[data-history-component-metric]"),
  );
  const presets = Array.from(document.querySelectorAll("[data-history-days]"));
  const apiBasePath = document.body.dataset.apiBasePath || "";
  const i18n = window.HOMESOLAR_HISTORY_I18N || {};
  const palette = ["#13795b", "#d68c22", "#315f92", "#7b5b2e"];
  let chart = null;
  let dayPowerChart = null;
  let dayComponentChart = null;
  let currentPayload = null;

  function localDateValue(date) {
    const adjusted = new Date(date.getTime() - date.getTimezoneOffset() * 60_000);
    return adjusted.toISOString().slice(0, 10);
  }

  function dateDaysBefore(endDate, days) {
    const value = new Date(`${endDate}T12:00:00`);
    value.setDate(value.getDate() - days + 1);
    return localDateValue(value);
  }

  function formatEnergy(value) {
    return `${Number(value || 0).toFixed(2)} kWh`;
  }

  function formatPower(value) {
    return value == null ? "--" : `${Math.round(Number(value))} W`;
  }

  function formatTime(value) {
    return value
      ? new Date(value).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
      : "--";
  }

  function setStatus(message, state) {
    statusEl.textContent = message || "";
    statusEl.className = `history-status${state ? ` ${state}` : ""}`;
  }

  function selectedParams() {
    const params = new URLSearchParams({
      from: fromInput.value,
      to: toInput.value,
      period: periodInput.value,
    });
    if (inverterInput.value) {
      params.set("inverter_id", inverterInput.value);
    }
    return params;
  }

  function selectedComponentMetric() {
    return (
      componentMetricButtons.find((button) => button.classList.contains("active"))?.dataset
        .historyComponentMetric || "power_w"
    );
  }

  function syncUrl(params) {
    const url = new URL(window.location.href);
    if (dayInput.value) {
      params.set("day", dayInput.value);
    }
    params.set("component_metric", selectedComponentMetric());
    url.search = params.toString();
    window.history.replaceState({}, "", url);
  }

  function restoreFilters() {
    const params = new URLSearchParams(window.location.search);
    const today = localDateValue(new Date());
    toInput.max = today;
    fromInput.max = today;
    toInput.value = params.get("to") || today;
    fromInput.value = params.get("from") || dateDaysBefore(toInput.value, 365);
    periodInput.value = params.get("period") || "monthly";
    dayInput.max = today;
    dayInput.value = params.get("day") || toInput.value;
    const inverterId = params.get("inverter_id") || "";
    if (Array.from(inverterInput.options).some((option) => option.value === inverterId)) {
      inverterInput.value = inverterId;
    }
    const componentMetric = params.get("component_metric") || "power_w";
    componentMetricButtons.forEach((button) => {
      button.classList.toggle(
        "active",
        button.dataset.historyComponentMetric === componentMetric,
      );
    });
  }

  function updateSummary(payload) {
    const total = payload.totals.reduce((sum, value) => sum + Number(value), 0);
    const dayCount =
      Math.round(
        (new Date(`${payload.to}T12:00:00`) - new Date(`${payload.from}T12:00:00`)) /
          86_400_000,
      ) + 1;
    const bestValue = payload.totals.length ? Math.max(...payload.totals) : 0;
    const bestIndex = payload.totals.indexOf(bestValue);
    const activeCount = payload.totals.filter((value) => Number(value) > 0).length;
    document.getElementById("historyTotal").textContent = formatEnergy(total);
    document.getElementById("historyAverage").textContent = formatEnergy(total / dayCount);
    document.getElementById("historyBest").textContent =
      bestIndex >= 0 && bestValue > 0 ? payload.labels[bestIndex] : "--";
    document.getElementById("historyBestValue").textContent =
      bestValue > 0 ? formatEnergy(bestValue) : "";
    document.getElementById("historyActive").textContent =
      `${activeCount} / ${payload.labels.length}`;
    document.getElementById("historyCoverage").textContent =
      `${payload.from} — ${payload.to}`;
  }

  function updateChart(payload) {
    const datasets = payload.series.map((series, index) => ({
      label: series.name,
      data: series.data,
      backgroundColor: palette[index % palette.length],
      borderColor: palette[index % palette.length],
      borderWidth: 1,
      borderRadius: payload.labels.length < 80 ? 3 : 0,
    }));
    if (chart) {
      chart.data.labels = payload.labels;
      chart.data.datasets = datasets;
      chart.update();
      return;
    }
    chart = new Chart(chartEl, {
      type: "bar",
      data: { labels: payload.labels, datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        scales: {
          x: {
            stacked: true,
            ticks: { autoSkip: true, maxTicksLimit: 14, maxRotation: 0 },
          },
          y: {
            stacked: true,
            beginAtZero: true,
            ticks: { callback: (value) => `${value} kWh` },
          },
        },
        plugins: {
          legend: { position: "bottom" },
          tooltip: {
            callbacks: {
              label: (item) => `${item.dataset.label}: ${formatEnergy(item.raw)}`,
            },
          },
        },
        onClick: (_event, elements) => {
          if (currentPayload?.period !== "daily" || !elements.length) {
            return;
          }
          selectDay(currentPayload.labels[elements[0].index], true);
        },
        onHover: (event, elements) => {
          if (event.native?.target) {
            event.native.target.style.cursor =
              currentPayload?.period === "daily" && elements.length ? "pointer" : "default";
          }
        },
      },
    });
  }

  function appendCell(row, value, tagName) {
    const cell = document.createElement(tagName);
    cell.textContent = value;
    row.appendChild(cell);
  }

  function updateTable(payload) {
    const head = document.getElementById("historyTableHead");
    const body = document.getElementById("historyTableBody");
    head.replaceChildren();
    body.replaceChildren();
    const headingRow = document.createElement("tr");
    appendCell(headingRow, i18n.period || "Period", "th");
    payload.series.forEach((series) => appendCell(headingRow, series.name, "th"));
    appendCell(headingRow, i18n.total || "Total", "th");
    head.appendChild(headingRow);
    payload.labels.forEach((label, index) => {
      const row = document.createElement("tr");
      if (payload.period === "daily") {
        const periodCell = document.createElement("td");
        const dayButton = document.createElement("button");
        dayButton.className = "history-day-link";
        dayButton.type = "button";
        dayButton.textContent = label;
        dayButton.addEventListener("click", () => selectDay(label, true));
        periodCell.appendChild(dayButton);
        row.appendChild(periodCell);
      } else {
        appendCell(row, label, "td");
      }
      payload.series.forEach((series) => appendCell(row, formatEnergy(series.data[index]), "td"));
      appendCell(row, formatEnergy(payload.totals[index]), "td");
      body.appendChild(row);
    });
  }

  function csvCell(value) {
    return `"${String(value).replaceAll('"', '""')}"`;
  }

  function downloadCsv() {
    if (!currentPayload) {
      return;
    }
    const rows = [
      [
        i18n.period || "Period",
        ...currentPayload.series.map((series) => series.name),
        i18n.total || "Total",
      ],
      ...currentPayload.labels.map((label, index) => [
        label,
        ...currentPayload.series.map((series) => series.data[index]),
        currentPayload.totals[index],
      ]),
    ];
    const csv = rows.map((row) => row.map(csvCell).join(",")).join("\n");
    const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = `${i18n.csv_filename || "homesolar-energy-history"}-${currentPayload.from}-${currentPayload.to}.csv`;
    link.click();
    URL.revokeObjectURL(url);
  }

  function createDayChart(canvas, datasets, unit, existingChart) {
    if (existingChart) {
      existingChart.data.datasets = datasets;
      existingChart.options.scales.y.ticks.callback = (value) => `${value} ${unit}`;
      existingChart.update();
      return existingChart;
    }
    return new Chart(canvas, {
      type: "line",
      data: { datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "nearest", intersect: false },
        scales: {
          x: {
            type: "linear",
            ticks: {
              maxTicksLimit: 7,
              callback: (value) => formatTime(value),
            },
          },
          y: {
            beginAtZero: true,
            ticks: { callback: (value) => `${value} ${unit}` },
          },
        },
        plugins: {
          legend: { position: "bottom" },
          tooltip: {
            callbacks: {
              title: (items) => (items.length ? formatTime(items[0].parsed.x) : ""),
              label: (item) => `${item.dataset.label}: ${item.formattedValue} ${unit}`,
            },
          },
        },
      },
    });
  }

  function appendDetail(container, label, value) {
    const item = document.createElement("div");
    const term = document.createElement("dt");
    const detail = document.createElement("dd");
    term.textContent = label;
    detail.textContent = value;
    item.append(term, detail);
    container.appendChild(item);
  }

  function updateDayInverters(payload) {
    const container = document.getElementById("historyDayInverters");
    container.replaceChildren();
    payload.inverters.forEach((inverter) => {
      const card = document.createElement("article");
      const title = document.createElement("h3");
      const metrics = document.createElement("dl");
      const productionWindow =
        inverter.first_production_local && inverter.last_production_local
          ? `${formatTime(inverter.first_production_local)} — ${formatTime(inverter.last_production_local)}`
          : "--";
      title.textContent = inverter.inverter_name;
      metrics.className = "history-day-card-metrics";
      appendDetail(metrics, i18n.selected_energy || "Selected energy", formatEnergy(inverter.total_kwh));
      appendDetail(
        metrics,
        (i18n.peak_at || "Peak at {time}").replace(
          "{time}",
          formatTime(inverter.peak_at_local),
        ),
        formatPower(inverter.peak_power_w),
      );
      appendDetail(metrics, i18n.production_window || "Production window", productionWindow);
      appendDetail(metrics, i18n.samples || "Samples", String(inverter.sample_count));
      card.append(title, metrics);
      container.appendChild(card);
    });
  }

  function updateDay(payload) {
    document.getElementById("historyDayEnergy").textContent = formatEnergy(payload.total_kwh);
    document.getElementById("historyDayPeak").textContent = formatPower(payload.peak_power_w);
    document.getElementById("historyDayAverage").textContent = formatPower(
      payload.average_power_w,
    );
    document.getElementById("historyDaySamples").textContent = payload.sample_count;
    document.getElementById("historyDayPowerDate").textContent = payload.date;

    const powerDatasets = payload.inverters.map((inverter, index) => ({
      label: inverter.inverter_name,
      data: inverter.power_points.map((point) => ({ x: Date.parse(point.x), y: point.y })),
      borderColor: palette[index % palette.length],
      backgroundColor: palette[index % palette.length],
      borderWidth: 2,
      pointRadius: 0,
      tension: 0.22,
    }));
    dayPowerChart = createDayChart(dayPowerEl, powerDatasets, "W", dayPowerChart);

    const componentDatasets = [];
    let componentIndex = 0;
    payload.inverters.forEach((inverter) => {
      inverter.components.series.forEach((series) => {
        componentDatasets.push({
          label: `${inverter.inverter_name} · ${series.name}`,
          data: series.points.map((point) => ({ x: Date.parse(point.x), y: point.y })),
          borderColor: palette[componentIndex % palette.length],
          backgroundColor: palette[componentIndex % palette.length],
          borderWidth: 2,
          pointRadius: 0,
          tension: 0.22,
        });
        componentIndex += 1;
      });
    });
    const metric = selectedComponentMetric();
    const unitByMetric = {
      power_w: "W",
      voltage_v: "V",
      current_a: "A",
      energy_today_kwh: "kWh",
    };
    const componentUnit = unitByMetric[metric] || "";
    document.getElementById("historyComponentUnit").textContent = componentUnit;
    dayComponentChart = createDayChart(
      dayComponentEl,
      componentDatasets,
      componentUnit,
      dayComponentChart,
    );
    updateDayInverters(payload);
  }

  async function loadDay() {
    if (!dayInput.value) {
      return;
    }
    const params = new URLSearchParams({
      date: dayInput.value,
      component_metric: selectedComponentMetric(),
    });
    if (inverterInput.value) {
      params.set("inverter_id", inverterInput.value);
    }
    syncUrl(selectedParams());
    const dayStatus = document.getElementById("historyDayStatus");
    dayStatus.textContent = i18n.day_loading || "Loading day detail…";
    dayStatus.className = "history-status loading";
    try {
      const response = await fetch(`${apiBasePath}/api/history/day?${params}`);
      if (!response.ok) {
        throw new Error(`Day request failed with ${response.status}`);
      }
      const payload = await response.json();
      updateDay(payload);
      const hasData = payload.sample_count > 0;
      dayStatus.textContent = hasData
        ? ""
        : i18n.day_no_data || "No power readings were recorded for this day.";
      dayStatus.className = `history-status${hasData ? "" : " empty"}`;
    } catch {
      dayStatus.textContent = i18n.day_error || "Day detail could not be loaded.";
      dayStatus.className = "history-status bad";
    }
  }

  function selectDay(value, scrollToDetail) {
    dayInput.value = value;
    loadDay();
    if (scrollToDetail) {
      document.getElementById("historyDaySection").scrollIntoView({
        behavior: "smooth",
        block: "start",
      });
    }
  }

  function moveSelectedDay(offset) {
    const value = new Date(`${dayInput.value}T12:00:00`);
    value.setDate(value.getDate() + offset);
    const nextValue = localDateValue(value);
    if (nextValue <= dayInput.max) {
      selectDay(nextValue, false);
    }
  }

  async function loadHistory() {
    if (!form.reportValidity() || fromInput.value > toInput.value) {
      setStatus(i18n.error || "Historical energy could not be loaded.", "bad");
      return;
    }
    const params = selectedParams();
    syncUrl(params);
    setStatus(i18n.loading || "Loading historical energy…", "loading");
    csvButton.disabled = true;
    try {
      const response = await fetch(`${apiBasePath}/api/history/energy?${params}`);
      if (!response.ok) {
        throw new Error(`History request failed with ${response.status}`);
      }
      const payload = await response.json();
      currentPayload = payload;
      updateSummary(payload);
      updateChart(payload);
      updateTable(payload);
      const hasData = payload.totals.some((value) => Number(value) > 0);
      setStatus(hasData ? "" : i18n.no_data || "No produced energy was recorded.", hasData ? "" : "empty");
      csvButton.disabled = payload.labels.length === 0;
      if (dayInput.value < payload.from || dayInput.value > payload.to) {
        dayInput.value = payload.to;
      }
      await loadDay();
    } catch {
      currentPayload = null;
      setStatus(i18n.error || "Historical energy could not be loaded.", "bad");
    }
  }

  presets.forEach((button) => {
    button.addEventListener("click", () => {
      toInput.value = localDateValue(new Date());
      fromInput.value = dateDaysBefore(toInput.value, Number(button.dataset.historyDays));
      periodInput.value = button.dataset.period || "monthly";
      loadHistory();
    });
  });
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    loadHistory();
  });
  csvButton.addEventListener("click", downloadCsv);
  dayInput.addEventListener("change", () => loadDay());
  previousDayButton.addEventListener("click", () => moveSelectedDay(-1));
  nextDayButton.addEventListener("click", () => moveSelectedDay(1));
  componentMetricButtons.forEach((button) => {
    button.addEventListener("click", () => {
      componentMetricButtons.forEach((item) => item.classList.toggle("active", item === button));
      loadDay();
    });
  });

  restoreFilters();
  if (window.Chart && chartEl) {
    loadHistory();
  } else {
    setStatus(i18n.error || "Historical energy could not be loaded.", "bad");
  }
})();
