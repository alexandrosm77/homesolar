(async function () {
  const powerEl = document.getElementById("todayChart");
  const aggregateEl = document.getElementById("aggregateChart");
  const inverterFilter = document.getElementById("inverterFilter");
  const aggregatePeriod = document.getElementById("aggregatePeriod");
  const autoRefreshToggle = document.getElementById("autoRefreshToggle");
  const resetDashboard = document.getElementById("resetDashboard");
  const powerChartTotal = document.getElementById("powerChartTotal");
  const rangeButtons = Array.from(document.querySelectorAll(".range-btn"));
  const componentPanels = Array.from(document.querySelectorAll("[data-component-panel]"));
  const sessionSettingsKey = "homesolar.dashboard.settings";
  const palette = ["#13795b", "#d68c22", "#315f92", "#7b5b2e"];
  const chartsAvailable = Boolean(window.Chart && powerEl && aggregateEl);
  const apiBasePath = document.body.dataset.apiBasePath || "";
  const autoRefreshDefault = document.body.dataset.autoRefreshDefault === "true";
  const autoRefreshSeconds = Math.max(
    5,
    Number.parseInt(document.body.dataset.autoRefreshSeconds || "60", 10) || 60,
  );
  const i18n = window.HOMESOLAR_I18N || {};
  let selectedRange = "today";
  let powerChart = null;
  let aggregateChart = null;
  let autoRefreshTimer = null;
  const componentCharts = new Map();

  function readSettings() {
    try {
      return JSON.parse(sessionStorage.getItem(sessionSettingsKey) || "{}");
    } catch {
      return {};
    }
  }

  function writeSettings() {
    sessionStorage.setItem(
      sessionSettingsKey,
      JSON.stringify({
        inverterId: inverterFilter?.value || "",
        range: selectedRange,
        aggregatePeriod: aggregatePeriod?.value || "daily",
        autoRefreshEnabled: Boolean(autoRefreshToggle?.checked),
        componentPanels: componentPanelSettings(),
        componentMetrics: componentMetricSettings(),
      }),
    );
  }

  function componentPanelSettings() {
    return Object.fromEntries(
      componentPanels.map((panel) => [panel.dataset.componentPanel, panel.open]),
    );
  }

  function componentMetricSettings() {
    return Object.fromEntries(
      componentPanels.map((panel) => [panel.dataset.componentPanel, selectedComponentMetric(panel)]),
    );
  }

  function restoreSettings() {
    const settings = readSettings();
    if (settings.inverterId && inverterFilter) {
      const optionExists = Array.from(inverterFilter.options).some(
        (option) => option.value === settings.inverterId,
      );
      if (optionExists) {
        inverterFilter.value = settings.inverterId;
      }
    }
    if (settings.aggregatePeriod && aggregatePeriod) {
      const optionExists = Array.from(aggregatePeriod.options).some(
        (option) => option.value === settings.aggregatePeriod,
      );
      if (optionExists) {
        aggregatePeriod.value = settings.aggregatePeriod;
      }
    }
    if (settings.range && rangeButtons.some((button) => button.dataset.range === settings.range)) {
      selectedRange = settings.range;
    }
    if (autoRefreshToggle) {
      autoRefreshToggle.checked =
        typeof settings.autoRefreshEnabled === "boolean"
          ? settings.autoRefreshEnabled
          : autoRefreshDefault;
    }
    if (settings.componentPanels) {
      componentPanels.forEach((panel) => {
        const value = settings.componentPanels[panel.dataset.componentPanel];
        if (typeof value === "boolean") {
          panel.open = value;
        }
      });
    }
    if (settings.componentMetrics) {
      componentPanels.forEach((panel) => {
        const value = settings.componentMetrics[panel.dataset.componentPanel];
        const button = value ? panel.querySelector(`[data-component-metric="${value}"]`) : null;
        if (button) {
          setComponentMetric(panel, value);
        }
      });
    }
    syncRangeButtons();
    syncAutoRefresh();
  }

  function syncRangeButtons() {
    rangeButtons.forEach((button) =>
      button.classList.toggle("active", button.dataset.range === selectedRange),
    );
  }

  function resetSettings() {
    sessionStorage.removeItem(sessionSettingsKey);
    selectedRange = "today";
    if (inverterFilter) {
      inverterFilter.value = "";
    }
    if (aggregatePeriod) {
      aggregatePeriod.value = "daily";
    }
    if (autoRefreshToggle) {
      autoRefreshToggle.checked = autoRefreshDefault;
    }
    componentPanels.forEach((panel) => {
      panel.open = false;
    });
    syncRangeButtons();
    syncAutoRefresh();
  }

  function syncAutoRefresh() {
    window.clearInterval(autoRefreshTimer);
    autoRefreshTimer = null;
    if (!autoRefreshToggle?.checked) {
      return;
    }
    autoRefreshTimer = window.setInterval(() => {
      window.location.reload();
    }, autoRefreshSeconds * 1000);
  }

  function inverterParams() {
    const params = new URLSearchParams();
    if (inverterFilter?.value) {
      params.set("inverter_id", inverterFilter.value);
    }
    return params;
  }

  function formatPower(value) {
    return value == null ? "--" : `${Math.round(value)} W`;
  }

  function formatEnergy(value) {
    return value == null ? "--" : `${Number(value).toFixed(2)} kWh`;
  }

  function formatTime(value) {
    return new Date(value).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  }

  function apiUrl(path, params) {
    return `${apiBasePath}${path}?${params}`;
  }

  function pointRadius() {
    return selectedRange === "today" || selectedRange === "24h" ? 0 : 1.5;
  }

  function selectedComponentMetric(panel) {
    return (
      panel.querySelector("[data-component-metric].active")?.dataset.componentMetric || "power_w"
    );
  }

  function setComponentMetric(panel, metric) {
    panel.querySelectorAll("[data-component-metric]").forEach((button) => {
      button.classList.toggle("active", button.dataset.componentMetric === metric);
    });
  }

  function syncComponentMetricControls(panel, payload) {
    const available = new Set(payload.available_metrics.map((item) => item.metric));
    panel.querySelectorAll("[data-component-metric]").forEach((button) => {
      const isAvailable = available.has(button.dataset.componentMetric);
      button.hidden = !isAvailable;
      button.disabled = !isAvailable;
      button.classList.toggle("active", button.dataset.componentMetric === payload.metric);
    });
  }

  async function loadPowerChart() {
    const params = inverterParams();
    params.set("range", selectedRange);
    const response = await fetch(apiUrl("/api/chart/power", params));
    const payload = await response.json();

    const datasets = payload.series.map((series, index) => ({
      label: series.name,
      data: series.points.map((point) => ({ x: Date.parse(point.x), y: point.y })),
      borderColor: palette[index % palette.length],
      backgroundColor: palette[index % palette.length],
      tension: 0.25,
      pointRadius: pointRadius(),
      borderWidth: 2,
    }));

    if (powerChart) {
      powerChart.data.datasets = datasets;
      powerChart.update();
      return;
    }

    powerChart = new Chart(powerEl, {
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
              maxTicksLimit: 6,
              callback: (value) => formatTime(value),
            },
          },
          y: {
            beginAtZero: true,
            ticks: { callback: (value) => `${value} W` },
          },
        },
        plugins: {
          legend: { position: "bottom" },
          tooltip: {
            callbacks: {
              title: (items) => (items.length ? formatTime(items[0].parsed.x) : ""),
            },
          },
        },
      },
    });
  }

  async function loadComponentChart(panel) {
    const inverterId = panel.dataset.componentPanel;
    const canvas = panel.querySelector("[data-component-chart]");
    if (!inverterId || !canvas) {
      return;
    }

    const params = new URLSearchParams();
    params.set("inverter_id", inverterId);
    params.set("range", selectedRange);
    params.set("metric", selectedComponentMetric(panel));
    const response = await fetch(apiUrl("/api/chart/components", params));
    const payload = await response.json();
    syncComponentMetricControls(panel, payload);
    const datasets = payload.series.map((series, index) => ({
      label: series.name,
      data: series.points.map((point) => ({ x: Date.parse(point.x), y: point.y })),
      borderColor: palette[index % palette.length],
      backgroundColor: palette[index % palette.length],
      tension: 0.25,
      pointRadius: pointRadius(),
      borderWidth: 2,
    }));
    const metricLabel = (i18n.metric_labels && i18n.metric_labels[payload.metric]) || payload.label;
    const status = panel.querySelector("[data-component-chart-status]");
    if (status) {
      const pointCount = payload.series.reduce((sum, series) => sum + series.points.length, 0);
      const noData = (i18n.component_no_data || "no {label} data").replace(
        "{label}",
        metricLabel.toLowerCase(),
      );
      status.textContent = pointCount ? payload.unit : noData;
    }
    const title = panel.querySelector("[data-component-chart-title]");
    if (title) {
      title.textContent = (i18n.component_chart_title || "Component {label}").replace(
        "{label}",
        metricLabel,
      );
    }

    const existing = componentCharts.get(inverterId);
    if (existing) {
      existing.data.datasets = datasets;
      existing.options.scales.y.ticks.callback = (value) => `${value} ${payload.unit}`;
      existing.update();
      return;
    }

    const chart = new Chart(canvas, {
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
              maxTicksLimit: 5,
              callback: (value) => formatTime(value),
            },
          },
          y: {
            beginAtZero: true,
            ticks: { callback: (value) => `${value} ${payload.unit}` },
          },
        },
        plugins: {
          legend: { position: "bottom" },
          tooltip: {
            callbacks: {
              title: (items) => (items.length ? formatTime(items[0].parsed.x) : ""),
            },
          },
        },
      },
    });
    componentCharts.set(inverterId, chart);
  }

  async function loadOpenComponentCharts() {
    await Promise.all(componentPanels.filter((panel) => panel.open).map(loadComponentChart));
  }

  async function loadSummary() {
    const params = inverterParams();
    params.set("range", selectedRange);
    const response = await fetch(apiUrl("/api/summary", params));
    const summary = await response.json();
    document.querySelector('[data-summary="total"]').textContent = formatEnergy(summary.total_kwh);
    document.querySelector('[data-summary="peak"]').textContent = formatPower(summary.peak_power_w);
    document.querySelector('[data-summary="average"]').textContent = formatPower(summary.average_power_w);
    document.querySelector('[data-summary="samples"]').textContent = summary.reading_count ?? "--";
    if (powerChartTotal) {
      powerChartTotal.textContent = formatEnergy(summary.total_kwh);
    }
  }

  async function loadAggregateChart() {
    const params = inverterParams();
    const period = aggregatePeriod?.value || "daily";
    const limits = { daily: 14, weekly: 12, monthly: 12, yearly: 5 };
    params.set("period", period);
    params.set("limit", limits[period] || 14);
    const response = await fetch(apiUrl("/api/aggregates", params));
    const payload = await response.json();

    const datasets = payload.series.map((series, index) => ({
      label: series.name,
      data: series.data,
      backgroundColor: palette[index % palette.length],
      borderRadius: 4,
    }));

    if (aggregateChart) {
      aggregateChart.data.labels = payload.labels;
      aggregateChart.data.datasets = datasets;
      aggregateChart.update();
    } else {
      aggregateChart = new Chart(aggregateEl, {
        type: "bar",
        data: { labels: payload.labels, datasets },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          scales: {
            x: { stacked: true, ticks: { maxTicksLimit: 8 } },
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
                label: (item) => `${item.dataset.label}: ${Number(item.raw).toFixed(2)} kWh`,
              },
            },
          },
        },
      });
    }

    const total = payload.totals.reduce((sum, value) => sum + value, 0);
    document.getElementById("aggregateTotal").textContent = formatEnergy(total);
    const rows = document.getElementById("aggregateRows");
    rows.innerHTML = payload.labels
      .map((label, index) => `<tr><td>${label}</td><td>${formatEnergy(payload.totals[index])}</td></tr>`)
      .join("");
  }

  async function refreshDashboard() {
    await Promise.all([
      loadPowerChart(),
      loadSummary(),
      loadAggregateChart(),
      loadOpenComponentCharts(),
    ]);
  }

  rangeButtons.forEach((button) => {
    button.addEventListener("click", async () => {
      selectedRange = button.dataset.range || "today";
      syncRangeButtons();
      writeSettings();
      if (chartsAvailable) {
        await refreshDashboard();
      }
    });
  });

  inverterFilter?.addEventListener("change", async () => {
    writeSettings();
    if (chartsAvailable) {
      await refreshDashboard();
    }
  });
  aggregatePeriod?.addEventListener("change", async () => {
    writeSettings();
    if (chartsAvailable) {
      await loadAggregateChart();
    }
  });
  autoRefreshToggle?.addEventListener("change", () => {
    writeSettings();
    syncAutoRefresh();
  });
  componentPanels.forEach((panel) => {
    panel.addEventListener("toggle", async () => {
      writeSettings();
      if (chartsAvailable && panel.open) {
        await loadComponentChart(panel);
      }
    });
    panel.querySelectorAll("[data-component-metric]").forEach((button) => {
      button.addEventListener("click", async () => {
        setComponentMetric(panel, button.dataset.componentMetric || "power_w");
        writeSettings();
        if (chartsAvailable) {
          await loadComponentChart(panel);
        }
      });
    });
  });
  resetDashboard?.addEventListener("click", async () => {
    resetSettings();
    if (chartsAvailable) {
      await refreshDashboard();
    }
  });

  restoreSettings();
  if (chartsAvailable) {
    await refreshDashboard();
  }
})();
