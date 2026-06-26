from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import select
from sqlalchemy.orm import Session

from homesolar.archive import Archive
from homesolar.config import EmailConfig
from homesolar.db import models
from homesolar.reports import charts
from homesolar.reports.data import daily_history, inverter_day_metrics, yesterday_window
from homesolar.reports.email import build_message, send_message
from homesolar.web.i18n import DEFAULT_LANGUAGE, SUPPORTED_LANGUAGES, get_translations

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "web" / "templates"
_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATES_DIR)),
    autoescape=select_autoescape(["html"]),
)
HISTORY_DAYS = 14


def resolve_report_language(user: models.AppUser) -> str:
    for candidate in (user.report_language, user.language):
        if candidate in SUPPORTED_LANGUAGES:
            return candidate
    return DEFAULT_LANGUAGE


def selected_inverters(session: Session, user: models.AppUser) -> list[models.Inverter]:
    ids = list(user.report_inverter_ids or [])
    if not ids:
        return []
    found = session.scalars(select(models.Inverter).where(models.Inverter.id.in_(ids))).all()
    order = {inverter_id: index for index, inverter_id in enumerate(ids)}
    return sorted(found, key=lambda inverter: order.get(inverter.id, len(order)))


def _fmt_time(value: datetime | None) -> str | None:
    return value.strftime("%H:%M") if value is not None else None


def _section(metrics: dict, history: list[tuple[str, float]], t: dict, images: dict) -> dict:
    na = t["na"]
    peak = (
        f"{metrics['peak_power_w']:.0f} W ({_fmt_time(metrics['peak_at_local'])})"
        if metrics["peak_power_w"] is not None
        else t["report_no_production"]
    )
    average = f"{metrics['average_power_w']:.0f} W" if metrics["average_power_w"] is not None else na
    first, last = _fmt_time(metrics["first_production_local"]), _fmt_time(metrics["last_production_local"])
    window = f"{first} – {last}" if first and last else na
    lifetime = f"{metrics['lifetime_kwh']:.1f} kWh" if metrics["lifetime_kwh"] is not None else na

    power_cid = history_cid = None
    power_png = charts.power_curve_png(
        metrics["power_points"],
        t["report_power_chart_title"].format(date=metrics["date_label"]),
        t["report_power_chart_y"],
    )
    if power_png is not None:
        power_cid = f"power-{metrics['inverter_id']}"
        images[power_cid] = power_png
    history_png = charts.energy_history_png(
        [label for label, _ in history],
        [value for _, value in history],
        t["report_history_chart_title"].format(days=HISTORY_DAYS),
        t["report_history_chart_y"],
    )
    if history_png is not None:
        history_cid = f"history-{metrics['inverter_id']}"
        images[history_cid] = history_png

    return {
        "name": metrics["inverter_name"],
        "total_kwh": metrics["total_kwh"],
        "peak_power": peak,
        "average_power": average,
        "production_window": window,
        "lifetime": lifetime,
        "sample_count": metrics["sample_count"],
        "power_cid": power_cid,
        "history_cid": history_cid,
    }


def build_user_report(
    session: Session,
    user: models.AppUser,
    app_name: str,
    now: datetime | None = None,
    archive: Archive | None = None,
) -> dict | None:
    inverters = selected_inverters(session, user)
    if not user.email or not inverters:
        return None

    now = now or datetime.now(UTC)
    lang = resolve_report_language(user)
    t = get_translations(lang)
    images: dict[str, bytes] = {}
    sections: list[dict] = []
    total_kwh = 0.0
    header_date = None

    for inverter in inverters:
        start_utc, end_utc, date_label = yesterday_window(inverter.timezone, now)
        metrics = (
            archive.inverter_day_metrics(inverter.id, start_utc, end_utc)
            if archive is not None
            else inverter_day_metrics(session, inverter, start_utc, end_utc)
        )
        if metrics is None:
            continue
        metrics["date_label"] = date_label
        history = (
            archive.daily_history(inverter.id, HISTORY_DAYS, now)
            if archive is not None
            else daily_history(session, inverter, HISTORY_DAYS, now)
        )
        total_kwh += metrics["total_kwh"]
        header_date = header_date or date_label
        sections.append(_section(metrics, history, t, images))

    generated_at = now.astimezone(ZoneInfo(inverters[0].timezone)).strftime("%Y-%m-%d %H:%M")
    context = {
        "lang": lang,
        "t": t,
        "app_name": app_name,
        "date_label": header_date,
        "sections": sections,
        "total_kwh": round(total_kwh, 3),
        "generated_at_label": generated_at,
    }
    html = _env.get_template("email_report.html").render(**context)
    text = _text_body(t, app_name, header_date, sections, round(total_kwh, 3))
    subject = t["report_subject"].format(date=header_date)
    return {"to_address": user.email, "subject": subject, "html": html, "text": text, "images": images}


def _text_body(t: dict, app_name: str, date_label: str, sections: list[dict], total_kwh: float) -> str:
    lines = [f"{app_name} — {t['report_title']}", t["report_for_date"].format(date=date_label), ""]
    lines.append(f"{t['report_total_energy']}: {total_kwh:.2f} kWh")
    for section in sections:
        lines += [
            "",
            section["name"],
            f"  {t['report_total_energy']}: {section['total_kwh']:.2f} kWh",
            f"  {t['report_peak_power']}: {section['peak_power']}",
            f"  {t['report_average_power']}: {section['average_power']}",
            f"  {t['report_production_window']}: {section['production_window']}",
            f"  {t['report_lifetime_energy']}: {section['lifetime']}",
            f"  {t['report_samples']}: {section['sample_count']}",
        ]
    return "\n".join(lines)


def send_user_report(config: EmailConfig, report: dict) -> None:
    message = build_message(
        from_address=config.from_address,
        to_address=report["to_address"],
        subject=report["subject"],
        text_body=report["text"],
        html_body=report["html"],
        images=report["images"],
    )
    send_message(config, message)
