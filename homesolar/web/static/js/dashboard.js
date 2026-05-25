(async function () {
  if (!window.Chart) {
    return;
  }

  const powerEl = document.getElementById("todayChart");
  const aggregateEl = document.getElementById("aggregateChart");
  const inverterFilter = document.getElementById("inverterFilter");
  const aggregatePeriod = document.getElementById("aggregatePeriod");
  const resetDashboard = document.getElementById("resetDashboard");
  const rangeButtons = Array.from(document.querySelectorAll(".range-btn"));
  const storageKey = "homesolar.dashboard.settings";
  const palette = ["#13795b", "#d68c22", "#315f92", "#7b5b2e"];
  let selectedRange = "today";
  let powerChart = null;
  let aggregateChart = null;

  if (!powerEl || !aggregateEl) {
    return;
  }

  function readSettings() {
    try {
      return JSON.parse(sessionStorage.getItem(storageKey) || "{}");
    } catch {
      return {};
    }
  }

  function writeSettings() {
    sessionStorage.setItem(
      storageKey,
      JSON.stringify({
        inverterId: inverterFilter?.value || "",
        range: selectedRange,
        aggregatePeriod: aggregatePeriod?.value || "daily",
      }),
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
    syncRangeButtons();
  }

  function syncRangeButtons() {
    rangeButtons.forEach((button) =>
      button.classList.toggle("active", button.dataset.range === selectedRange),
    );
  }

  function resetSettings() {
    sessionStorage.removeItem(storageKey);
    selectedRange = "today";
    if (inverterFilter) {
      inverterFilter.value = "";
    }
    if (aggregatePeriod) {
      aggregatePeriod.value = "daily";
    }
    syncRangeButtons();
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

  async function loadPowerChart() {
    const params = inverterParams();
    params.set("range", selectedRange);
    const response = await fetch(`/api/chart/power?${params}`);
    const payload = await response.json();

    const datasets = payload.series.map((series, index) => ({
      label: series.name,
      data: series.points.map((point) => ({ x: Date.parse(point.x), y: point.y })),
      borderColor: palette[index % palette.length],
      backgroundColor: palette[index % palette.length],
      tension: 0.25,
      pointRadius: selectedRange === "today" || selectedRange === "24h" ? 0 : 1.5,
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

  async function loadSummary() {
    const params = inverterParams();
    params.set("range", selectedRange);
    const response = await fetch(`/api/summary?${params}`);
    const summary = await response.json();
    document.querySelector('[data-summary="total"]').textContent = formatEnergy(summary.total_kwh);
    document.querySelector('[data-summary="peak"]').textContent = formatPower(summary.peak_power_w);
    document.querySelector('[data-summary="average"]').textContent = formatPower(summary.average_power_w);
    document.querySelector('[data-summary="samples"]').textContent = summary.reading_count ?? "--";
  }

  async function loadAggregateChart() {
    const params = inverterParams();
    const period = aggregatePeriod?.value || "daily";
    const limits = { daily: 14, weekly: 12, monthly: 12, yearly: 5 };
    params.set("period", period);
    params.set("limit", limits[period] || 14);
    const response = await fetch(`/api/aggregates?${params}`);
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
    await Promise.all([loadPowerChart(), loadSummary(), loadAggregateChart()]);
  }

  rangeButtons.forEach((button) => {
    button.addEventListener("click", async () => {
      selectedRange = button.dataset.range || "today";
      syncRangeButtons();
      writeSettings();
      await refreshDashboard();
    });
  });

  inverterFilter?.addEventListener("change", async () => {
    writeSettings();
    await refreshDashboard();
  });
  aggregatePeriod?.addEventListener("change", async () => {
    writeSettings();
    await loadAggregateChart();
  });
  resetDashboard?.addEventListener("click", async () => {
    resetSettings();
    await refreshDashboard();
  });

  restoreSettings();
  await refreshDashboard();
})();
