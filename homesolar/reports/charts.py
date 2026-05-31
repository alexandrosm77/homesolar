from __future__ import annotations

from datetime import datetime
from io import BytesIO

try:  # matplotlib is optional; charts degrade gracefully when missing.
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt

    _AVAILABLE = True
except Exception:  # pragma: no cover - exercised only without matplotlib
    _AVAILABLE = False

_LINE_COLOR = "#f59e0b"
_BAR_COLOR = "#2563eb"


def charts_available() -> bool:
    return _AVAILABLE


def _finish(fig) -> bytes:
    buffer = BytesIO()
    fig.savefig(buffer, format="png", dpi=110, bbox_inches="tight")
    plt.close(fig)
    return buffer.getvalue()


def power_curve_png(
    points: list[tuple[datetime, float]], title: str, ylabel: str
) -> bytes | None:
    if not _AVAILABLE or not points:
        return None
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    fig, ax = plt.subplots(figsize=(6.4, 2.6))
    ax.plot(xs, ys, color=_LINE_COLOR, linewidth=1.8)
    ax.fill_between(xs, ys, color=_LINE_COLOR, alpha=0.18)
    ax.set_title(title, fontsize=11)
    ax.set_ylabel(ylabel, fontsize=9)
    ax.set_ylim(bottom=0)
    ax.grid(True, alpha=0.25)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    fig.autofmt_xdate(rotation=0)
    return _finish(fig)


def energy_history_png(
    labels: list[str], values: list[float], title: str, ylabel: str
) -> bytes | None:
    if not _AVAILABLE or not any(values):
        return None
    short_labels = [label[5:] for label in labels]  # strip year for readability
    fig, ax = plt.subplots(figsize=(6.4, 2.6))
    ax.bar(range(len(values)), values, color=_BAR_COLOR, alpha=0.85)
    ax.set_title(title, fontsize=11)
    ax.set_ylabel(ylabel, fontsize=9)
    ax.set_ylim(bottom=0)
    ax.grid(True, axis="y", alpha=0.25)
    ax.set_xticks(range(len(values)))
    ax.set_xticklabels(short_labels, rotation=60, fontsize=7)
    return _finish(fig)
