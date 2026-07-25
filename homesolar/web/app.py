from __future__ import annotations

import base64
import binascii
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
import hashlib
import hmac
import os
from pathlib import Path
import secrets
from urllib.parse import quote

from fastapi import FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from homesolar import __version__
from homesolar.archive import (
    AlarmSnapshot,
    Archive,
    ComponentSnapshot,
    DashboardSnapshot,
    EnergyIntervalSnapshot,
    InverterIdentity,
    InverterSnapshot,
    PollEventSnapshot,
    PowerChartSnapshot,
    ReadingSnapshot,
    TelemetryHealth,
)
from homesolar.collector.processor import ensure_inverter
from homesolar.collector.scheduler import CollectorService
from homesolar.config import AppConfig
from homesolar.db import models
from homesolar.db.session import create_schema, engine_from_url, sessionmaker_from_engine
from homesolar.reports.render import build_user_report, send_user_report
from homesolar.reports.scheduler import ReportScheduler
from homesolar.web.i18n import (
    LANGUAGE_COOKIE_MAX_AGE,
    LANGUAGE_COOKIE_NAME,
    LANGUAGE_NAMES,
    SUPPORTED_LANGUAGES,
    SUPPORTED_THEMES,
    THEME_COOKIE_MAX_AGE,
    THEME_COOKIE_NAME,
    get_translations,
    resolve_language,
    resolve_theme,
)

PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_WEB_USERNAME_ENV = "HOMESOLAR_WEB_USER"
DEFAULT_WEB_PASSWORD_ENV = "HOMESOLAR_WEB_PASSWORD"
PUBLIC_BASE_PATH_ENV = "HOMESOLAR_WEB_BASE_PATH"
SESSION_COOKIE_NAME = "homesolar_session"
SESSION_MAX_AGE_SECONDS = 60 * 60 * 24 * 14
PASSWORD_HASH_ITERATIONS = 210_000
MANAGED_SETTINGS = {
    "app_name": "homesolar",
    "dashboard_note": "",
}
COMPONENT_CHART_METRICS = {
    "power_w": {"label": "Power", "unit": "W"},
    "voltage_v": {"label": "Voltage", "unit": "V"},
    "current_a": {"label": "Current", "unit": "A"},
    "energy_today_kwh": {"label": "Energy", "unit": "kWh"},
}
templates = Jinja2Templates(directory=str(PACKAGE_DIR / "templates"))


def create_app(config: AppConfig) -> FastAPI:
    engine = engine_from_url(config.database.url)
    create_schema(engine)
    session_factory = sessionmaker_from_engine(engine)
    archive = Archive(session_factory)
    collector = CollectorService(config, session_factory)
    report_scheduler = ReportScheduler(config, session_factory)
    web_credentials = _web_credentials(config)
    public_base_path = _public_base_path(config)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.config = config
        app.state.session_factory = session_factory
        with session_factory() as session:
            for inverter in config.inverters:
                ensure_inverter(session, inverter)
            if web_credentials is not None:
                _ensure_bootstrap_admin(session, web_credentials)
            _ensure_default_settings(session)
            session.commit()
        if config.collector.enabled:
            await collector.start()
        await report_scheduler.start()
        try:
            yield
        finally:
            await report_scheduler.stop()
            await collector.stop()

    app = FastAPI(title="homesolar", version=__version__, lifespan=lifespan)

    if web_credentials is not None:

        @app.middleware("http")
        async def require_web_auth(request: Request, call_next):
            if _is_public_path(request.url.path):
                return await call_next(request)

            if request.url.path.startswith("/api"):
                with session_factory() as session:
                    if _request_is_authorized(session, request, web_credentials):
                        return await call_next(request)
                return _auth_challenge()

            with session_factory() as session:
                if _request_user(session, request, web_credentials):
                    return await call_next(request)

            return RedirectResponse(url=_login_redirect_url(request), status_code=303)

    app.mount("/static", StaticFiles(directory=str(PACKAGE_DIR / "static")), name="static")

    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request) -> HTMLResponse:
        with session_factory() as session:
            user = (
                _request_user(session, request, web_credentials)
                if web_credentials is not None
                else None
            )
            lang = resolve_language(request, _user_language(user))
            theme = resolve_theme(request)
            t = get_translations(lang)
            _persist_user_language(session, user, request)
            settings = _settings_dict(session)
        view = _archive_dashboard_view(archive.dashboard_snapshot(), t)
        response = templates.TemplateResponse(
            request,
            "dashboard.html",
            {
                "view": view,
                "settings": settings,
                "config": config,
                "title": settings["app_name"],
                "asset_base_path": public_base_path,
                "asset_version": __version__,
                "t": t,
                "lang": lang,
                "theme": theme,
                "languages": LANGUAGE_NAMES,
                "js_i18n": _js_i18n(t),
            },
        )
        _disable_html_cache(response)
        _apply_language_cookie(request, response, lang)
        return _apply_theme_cookie(request, response, theme)

    @app.get("/history", response_class=HTMLResponse)
    def history_page(request: Request) -> HTMLResponse:
        with session_factory() as session:
            user = (
                _request_user(session, request, web_credentials)
                if web_credentials is not None
                else None
            )
            lang = resolve_language(request, _user_language(user))
            theme = resolve_theme(request)
            t = get_translations(lang)
            _persist_user_language(session, user, request)
            settings = _settings_dict(session)
            inverters = session.scalars(
                select(models.Inverter).order_by(models.Inverter.name)
            ).all()
        response = templates.TemplateResponse(
            request,
            "history.html",
            {
                "settings": settings,
                "inverters": inverters,
                "title": f"{settings['app_name']} {t['title_history_suffix']}",
                "asset_base_path": public_base_path,
                "asset_version": __version__,
                "t": t,
                "lang": lang,
                "theme": theme,
                "languages": LANGUAGE_NAMES,
                "history_i18n": _history_js_i18n(t),
            },
        )
        _disable_html_cache(response)
        _apply_language_cookie(request, response, lang)
        return _apply_theme_cookie(request, response, theme)

    @app.get("/login", response_class=HTMLResponse)
    def login_page(request: Request) -> Response:
        lang = resolve_language(request)
        theme = resolve_theme(request)
        t = get_translations(lang)
        with session_factory() as session:
            if web_credentials is not None and _request_user(session, request, web_credentials):
                return RedirectResponse(url=".", status_code=303)
            settings = _settings_dict(session)
        response = templates.TemplateResponse(
            request,
            "login.html",
            {
                "title": f"{settings['app_name']} {t['title_login_suffix']}",
                "settings": settings,
                "error": None,
                "asset_base_path": public_base_path,
                "asset_version": __version__,
                "t": t,
                "lang": lang,
                "theme": theme,
                "languages": LANGUAGE_NAMES,
            },
        )
        _disable_html_cache(response)
        _apply_language_cookie(request, response, lang)
        return _apply_theme_cookie(request, response, theme)

    @app.post("/login")
    async def login(
        request: Request,
        username: str = Form(default=""),
        password: str = Form(default=""),
    ) -> Response:
        if web_credentials is None:
            return RedirectResponse(url=".", status_code=303)

        lang = resolve_language(request)
        theme = resolve_theme(request)
        t = get_translations(lang)
        with session_factory() as session:
            user = _authenticate_user(session, username, password, web_credentials)
            settings = _settings_dict(session)
            if user is not None:
                user.last_login_at = datetime.now(UTC)
                session.commit()
                response = RedirectResponse(url=".", status_code=303)
                _set_session_cookie(response, user)
                return response

        response = templates.TemplateResponse(
            request,
            "login.html",
            {
                "title": f"{settings['app_name']} {t['title_login_suffix']}",
                "settings": settings,
                "error": t["invalid_credentials"],
                "asset_base_path": public_base_path,
                "asset_version": __version__,
                "t": t,
                "lang": lang,
                "theme": theme,
                "languages": LANGUAGE_NAMES,
            },
            status_code=401,
        )
        _disable_html_cache(response)
        return response

    @app.get("/admin", response_class=HTMLResponse)
    def admin_page(request: Request) -> Response:
        with session_factory() as session:
            user = _require_admin(session, request, web_credentials)
            if isinstance(user, Response):
                return user
            lang = resolve_language(request, _user_language(user))
            theme = resolve_theme(request)
            t = get_translations(lang)
            _persist_user_language(session, user, request)
            users = session.scalars(select(models.AppUser).order_by(models.AppUser.username)).all()
            inverters = session.scalars(
                select(models.Inverter).order_by(models.Inverter.name)
            ).all()
            settings = _settings_dict(session)
            response = templates.TemplateResponse(
                request,
                "admin.html",
                {
                    "title": f"{settings['app_name']} {t['title_admin_suffix']}",
                    "settings": settings,
                    "users": users,
                    "inverters": inverters,
                    "email_enabled": config.email.enabled,
                    "current_user": user,
                    "message": request.query_params.get("message"),
                    "error": request.query_params.get("error"),
                    "asset_base_path": public_base_path,
                    "asset_version": __version__,
                    "t": t,
                    "lang": lang,
                    "theme": theme,
                    "languages": LANGUAGE_NAMES,
                },
            )
            _disable_html_cache(response)
            _apply_language_cookie(request, response, lang)
            return _apply_theme_cookie(request, response, theme)

    @app.post("/admin/users")
    def admin_create_user(
        request: Request,
        username: str = Form(default=""),
        password: str = Form(default=""),
        is_admin: str | None = Form(default=None),
        enabled: str | None = Form(default=None),
        language: str | None = Form(default=None),
        email: str = Form(default=""),
        reports_enabled: str | None = Form(default=None),
        report_language: str | None = Form(default=None),
        report_inverter_ids: list[str] = Form(default=[]),
    ) -> Response:
        t = get_translations(resolve_language(request))
        with session_factory() as session:
            user = _require_admin(session, request, web_credentials)
            if isinstance(user, Response):
                return user
            clean_username = username.strip()
            if not clean_username or not password:
                return _admin_redirect(request, error=t["err_username_password_required"])
            existing = session.scalar(
                select(models.AppUser).where(models.AppUser.username == clean_username)
            )
            if existing is not None:
                return _admin_redirect(request, error=t["err_user_exists"])
            now = datetime.now(UTC)
            session.add(
                models.AppUser(
                    username=clean_username,
                    password_hash=_hash_password(password),
                    is_admin=is_admin == "on",
                    enabled=enabled == "on",
                    language=_clean_user_language(language),
                    email=_clean_email(email),
                    reports_enabled=reports_enabled == "on",
                    report_language=_clean_user_language(report_language),
                    report_inverter_ids=_clean_report_inverter_ids(session, report_inverter_ids),
                    created_at=now,
                    updated_at=now,
                )
            )
            session.commit()
        return _admin_redirect(request, message=t["msg_user_created"])

    @app.post("/admin/users/{user_id}/update")
    def admin_update_user(
        request: Request,
        user_id: int,
        is_admin: str | None = Form(default=None),
        enabled: str | None = Form(default=None),
        language: str | None = Form(default=None),
        email: str = Form(default=""),
        reports_enabled: str | None = Form(default=None),
        report_language: str | None = Form(default=None),
        report_inverter_ids: list[str] = Form(default=[]),
    ) -> Response:
        t = get_translations(resolve_language(request))
        with session_factory() as session:
            current_user = _require_admin(session, request, web_credentials)
            if isinstance(current_user, Response):
                return current_user
            user = session.get(models.AppUser, user_id)
            if user is None:
                raise HTTPException(status_code=404, detail="Unknown user")
            user.is_admin = is_admin == "on"
            user.enabled = enabled == "on"
            user.language = _clean_user_language(language)
            user.email = _clean_email(email)
            user.reports_enabled = reports_enabled == "on"
            user.report_language = _clean_user_language(report_language)
            user.report_inverter_ids = _clean_report_inverter_ids(session, report_inverter_ids)
            user.updated_at = datetime.now(UTC)
            if not _has_enabled_admin(session):
                session.rollback()
                return _admin_redirect(
                    request, error=t["err_enabled_admin_required"]
                )
            session.commit()
        return _admin_redirect(request, message=t["msg_user_updated"])

    @app.post("/admin/users/{user_id}/password")
    def admin_update_user_password(
        request: Request,
        user_id: int,
        password: str = Form(default=""),
    ) -> Response:
        t = get_translations(resolve_language(request))
        with session_factory() as session:
            current_user = _require_admin(session, request, web_credentials)
            if isinstance(current_user, Response):
                return current_user
            user = session.get(models.AppUser, user_id)
            if user is None:
                raise HTTPException(status_code=404, detail="Unknown user")
            if not password:
                return _admin_redirect(request, error=t["err_password_required"])
            user.password_hash = _hash_password(password)
            user.updated_at = datetime.now(UTC)
            session.commit()
        return _admin_redirect(request, message=t["msg_password_updated"])

    @app.post("/admin/users/{user_id}/send-test-report")
    def admin_send_test_report(request: Request, user_id: int) -> Response:
        t = get_translations(resolve_language(request))
        if not config.email.enabled:
            return _admin_redirect(request, error=t["err_email_disabled"])
        with session_factory() as session:
            current_user = _require_admin(session, request, web_credentials)
            if isinstance(current_user, Response):
                return current_user
            user = session.get(models.AppUser, user_id)
            if user is None:
                raise HTTPException(status_code=404, detail="Unknown user")
            app_name = _settings_dict(session)["app_name"]
            report = build_user_report(session, user, app_name, archive=archive)
            if report is None:
                return _admin_redirect(request, error=t["err_report_not_configured"])
            try:
                send_user_report(config.email, report)
            except Exception as exc:  # surface SMTP errors to the admin
                return _admin_redirect(
                    request, error=t["err_report_failed"].format(error=exc)
                )
        return _admin_redirect(request, message=t["msg_test_report_sent"])

    @app.post("/admin/settings")
    def admin_update_settings(
        request: Request,
        app_name: str = Form(default="homesolar"),
        dashboard_note: str = Form(default=""),
    ) -> Response:
        t = get_translations(resolve_language(request))
        with session_factory() as session:
            user = _require_admin(session, request, web_credentials)
            if isinstance(user, Response):
                return user
            _set_setting(session, "app_name", app_name.strip() or "homesolar")
            _set_setting(session, "dashboard_note", dashboard_note.strip())
            session.commit()
        return _admin_redirect(request, message=t["msg_settings_saved"])

    @app.post("/logout")
    def logout() -> RedirectResponse:
        response = RedirectResponse(url="login", status_code=303)
        response.delete_cookie(SESSION_COOKIE_NAME, path="/", samesite="lax")
        return response

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "time": datetime.now(UTC).isoformat()}

    @app.get("/api/inverters")
    def list_inverters() -> list[dict]:
        return [_archive_inverter_response(item) for item in archive.dashboard_snapshot().inverters]

    @app.get("/api/inverters/{inverter_id}/latest")
    def latest_for_inverter(inverter_id: str) -> dict:
        snapshot = archive.dashboard_snapshot()
        item = next((item for item in snapshot.inverters if item.inverter.id == inverter_id), None)
        if item is None:
            raise HTTPException(status_code=404, detail="Unknown inverter")
        return _archive_latest_inverter_response(item)

    @app.get("/api/readings")
    def readings(
        inverter_id: str | None = None,
        from_: datetime | None = Query(default=None, alias="from"),
        to: datetime | None = None,
        limit: int = Query(default=500, ge=1, le=5000),
    ) -> list[dict]:
        return [
            _archive_reading_dict(row)
            for row in archive.readings(inverter_id=inverter_id, from_=from_, to=to, limit=limit)
        ]

    @app.get("/api/energy/today")
    def energy_today() -> dict:
        snapshot = archive.dashboard_snapshot()
        items = [
            {
                "inverter_id": item.inverter.id,
                "name": item.inverter.name,
                "today_kwh": item.produced_energy_today_kwh,
            }
            for item in snapshot.inverters
        ]
        return {"total_kwh": snapshot.total_today_kwh, "inverters": items}

    @app.get("/api/energy/intervals")
    def intervals(
        inverter_id: str | None = None,
        from_: datetime | None = Query(default=None, alias="from"),
        to: datetime | None = None,
        limit: int = Query(default=1000, ge=1, le=10000),
    ) -> list[dict]:
        return [
            _archive_interval_dict(interval)
            for interval in archive.energy_intervals(
                inverter_id=inverter_id, from_=from_, to=to, limit=limit
            )
        ]

    @app.get("/api/events")
    def events(
        inverter_id: str | None = None,
        limit: int = Query(default=200, ge=1, le=1000),
    ) -> list[dict]:
        return [
            _archive_poll_event_dict(event)
            for event in archive.poll_events(inverter_id=inverter_id, limit=limit)
        ]

    @app.get("/api/chart/today")
    def chart_today() -> dict:
        return _archive_power_chart(archive.power_chart_today())

    @app.get("/api/chart/power")
    def chart_power(
        range_name: str = Query(default="today", alias="range"),
        inverter_id: str | None = None,
    ) -> dict:
        return _archive_power_chart(archive.power_chart(range_name, inverter_id=inverter_id))

    @app.get("/api/chart/components")
    def chart_components(
        inverter_id: str,
        range_name: str = Query(default="today", alias="range"),
        metric: str = Query(default="power_w"),
    ) -> dict:
        chart = archive.component_chart(inverter_id, range_name, metric, COMPONENT_CHART_METRICS)
        if chart is None:
            raise HTTPException(status_code=404, detail="Unknown inverter")
        return chart

    @app.get("/api/aggregates")
    def aggregates(
        period: str = Query(default="daily"),
        inverter_id: str | None = None,
        limit: int = Query(default=14, ge=1, le=60),
    ) -> dict:
        return archive.aggregate_energy(period=period, inverter_id=inverter_id, limit=limit)

    @app.get("/api/history/energy")
    def energy_history(
        from_: date = Query(alias="from"),
        to: date = Query(),
        period: str = Query(default="monthly"),
        inverter_id: str | None = None,
    ) -> dict:
        if from_ > to:
            raise HTTPException(status_code=422, detail="'from' must not be after 'to'")
        if (to - from_).days > 366 * 20:
            raise HTTPException(status_code=422, detail="Date range cannot exceed 20 years")
        return archive.energy_history(
            period=period,
            start_date=from_,
            end_date=to,
            inverter_id=inverter_id,
        )

    @app.get("/api/history/day")
    def historical_day(
        date_: date = Query(alias="date"),
        inverter_id: str | None = None,
        component_metric: str = Query(default="power_w"),
    ) -> dict:
        if component_metric not in COMPONENT_CHART_METRICS:
            component_metric = "power_w"
        return archive.historical_day(
            local_date=date_,
            inverter_id=inverter_id,
            component_metric=component_metric,
            metric_catalog=COMPONENT_CHART_METRICS,
        )

    @app.get("/api/summary")
    def range_summary(
        range_name: str = Query(default="today", alias="range"),
        inverter_id: str | None = None,
    ) -> dict:
        return archive.summary_for_range(range_name=range_name, inverter_id=inverter_id)

    @app.get("/api/overview")
    def overview(request: Request, inverter_id: str | None = None) -> dict:
        with session_factory() as session:
            user = (
                _request_user(session, request, web_credentials)
                if web_credentials is not None
                else None
            )
            t = get_translations(resolve_language(request, _user_language(user)))
        snapshot = archive.dashboard_snapshot()
        median_kwh = archive.median_daily_kwh(inverter_id=inverter_id, now=snapshot.updated_at)
        return _archive_overview_data(snapshot, t, inverter_id, median_kwh)

    return app


def _web_credentials(config: AppConfig) -> tuple[str, str] | None:
    auth = config.web.auth
    if auth is None:
        username = os.environ.get(DEFAULT_WEB_USERNAME_ENV)
        password = os.environ.get(DEFAULT_WEB_PASSWORD_ENV)
        if not username and not password:
            return None
        if username and password:
            return username, password
        missing = DEFAULT_WEB_PASSWORD_ENV if username else DEFAULT_WEB_USERNAME_ENV
        raise RuntimeError(f"Missing required web auth env var(s): {missing}")

    username = os.environ.get(auth.username_env)
    password = os.environ.get(auth.password_env)
    missing = [
        env_name
        for env_name, value in (
            (auth.username_env, username),
            (auth.password_env, password),
        )
        if not value
    ]
    if missing:
        raise RuntimeError(f"Missing required web auth env var(s): {', '.join(missing)}")

    return username, password


def _public_base_path(config: AppConfig) -> str:
    value = os.environ.get(PUBLIC_BASE_PATH_ENV, config.web.base_path)
    value = value.strip()
    if not value or value == "/":
        return ""
    return f"/{value.strip('/')}"


def _apply_language_cookie(request: Request, response: Response, lang: str) -> Response:
    if request.query_params.get("lang") in SUPPORTED_LANGUAGES:
        response.set_cookie(
            LANGUAGE_COOKIE_NAME,
            lang,
            max_age=LANGUAGE_COOKIE_MAX_AGE,
            samesite="lax",
            path="/",
        )
    return response


def _apply_theme_cookie(request: Request, response: Response, theme: str) -> Response:
    if request.query_params.get("theme") in SUPPORTED_THEMES:
        response.set_cookie(
            THEME_COOKIE_NAME,
            theme,
            max_age=THEME_COOKIE_MAX_AGE,
            samesite="lax",
            path="/",
        )
    return response


def _disable_html_cache(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


def _clean_user_language(value: str | None) -> str | None:
    return value if value in SUPPORTED_LANGUAGES else None


def _clean_email(value: str) -> str | None:
    cleaned = value.strip()
    return cleaned or None


def _clean_report_inverter_ids(session: Session, values: list[str]) -> list[str]:
    if not values:
        return []
    known = set(session.scalars(select(models.Inverter.id)).all())
    seen: list[str] = []
    for value in values:
        if value in known and value not in seen:
            seen.append(value)
    return seen


def _user_language(user: models.AppUser | None) -> str | None:
    return user.language if user is not None else None


def _persist_user_language(
    session: Session, user: models.AppUser | None, request: Request
) -> None:
    if user is None:
        return
    param = request.query_params.get("lang")
    if param in SUPPORTED_LANGUAGES and user.language != param:
        user.language = param
        user.updated_at = datetime.now(UTC)
        session.commit()


def _js_i18n(t: dict[str, str]) -> dict:
    return {
        "metric_labels": {
            "power_w": t["metric_power"],
            "voltage_v": t["metric_voltage"],
            "current_a": t["metric_current"],
            "energy_today_kwh": t["metric_energy"],
        },
        "component_chart_title": t["component_chart_title"],
        "component_no_data": t["component_no_data"],
    }


def _history_js_i18n(t: dict[str, str]) -> dict:
    return {
        "loading": t["history_loading"],
        "error": t["history_error"],
        "no_data": t["history_no_data"],
        "total_produced": t["history_total_produced"],
        "average_per_day": t["history_average_per_day"],
        "best_period": t["history_best_period"],
        "active_periods": t["history_active_periods"],
        "period": t["period"],
        "total": t["total"],
        "csv_filename": t["history_csv_filename"],
        "day_loading": t["history_day_loading"],
        "day_error": t["history_day_error"],
        "day_no_data": t["history_day_no_data"],
        "production_window": t["history_production_window"],
        "peak_at": t["history_peak_at"],
        "selected_energy": t["selected_energy"],
        "samples": t["samples"],
        "metric_labels": {
            "power_w": t["metric_power"],
            "voltage_v": t["metric_voltage"],
            "current_a": t["metric_current"],
            "energy_today_kwh": t["metric_energy"],
        },
    }


def _ensure_bootstrap_admin(session: Session, credentials: tuple[str, str]) -> None:
    username, password = credentials
    now = datetime.now(UTC)
    user = session.scalar(select(models.AppUser).where(models.AppUser.username == username))
    if user is None:
        session.add(
            models.AppUser(
                username=username,
                password_hash=_hash_password(password),
                is_admin=True,
                enabled=True,
                created_at=now,
                updated_at=now,
            )
        )
        return

    user.is_admin = True
    user.enabled = True
    user.updated_at = now


def _ensure_default_settings(session: Session) -> None:
    for key, value in MANAGED_SETTINGS.items():
        if session.get(models.AppSetting, key) is None:
            session.add(
                models.AppSetting(key=key, value=value, updated_at=datetime.now(UTC))
            )


def _request_is_authorized(
    session: Session, request: Request, credentials: tuple[str, str]
) -> bool:
    return _request_user(session, request, credentials) is not None or _request_has_valid_basic_auth(
        session, request, credentials
    )


def _request_user(
    session: Session, request: Request, credentials: tuple[str, str]
) -> models.AppUser | None:
    cookie = request.cookies.get(SESSION_COOKIE_NAME)
    if not cookie:
        return None

    try:
        user_id_text, issued_at_text, signature = cookie.split(".", 2)
        user_id = int(user_id_text)
        issued_at = int(issued_at_text)
    except ValueError:
        return None

    now = int(datetime.now(UTC).timestamp())
    if issued_at > now or now - issued_at > SESSION_MAX_AGE_SECONDS:
        return None

    user = session.get(models.AppUser, user_id)
    if user is None or not user.enabled:
        return None

    expected = _session_signature(str(user.id), issued_at_text, user.password_hash, None)
    if not secrets.compare_digest(signature, expected):
        return None

    return user


def _request_has_valid_basic_auth(
    session: Session, request: Request, credentials: tuple[str, str]
) -> bool:
    header = request.headers.get("Authorization")
    if not header:
        return False

    scheme, _, param = header.partition(" ")
    if scheme.lower() != "basic" or not param:
        return False

    try:
        decoded = base64.b64decode(param, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return False

    username, separator, password = decoded.partition(":")
    if not separator:
        return False

    return _authenticate_user(session, username, password, credentials) is not None


def _authenticate_user(
    session: Session, username: str, password: str, credentials: tuple[str, str]
) -> models.AppUser | None:
    clean_username = username.strip()
    user = session.scalar(select(models.AppUser).where(models.AppUser.username == clean_username))
    if user is not None and user.enabled and _verify_password(password, user.password_hash):
        return user

    expected_username, expected_password = credentials
    if secrets.compare_digest(clean_username, expected_username) and secrets.compare_digest(
        password, expected_password
    ):
        _ensure_bootstrap_admin(session, credentials)
        session.flush()
        return session.scalar(select(models.AppUser).where(models.AppUser.username == clean_username))
    return None


def _set_session_cookie(response: Response, user: models.AppUser) -> None:
    issued_at = str(int(datetime.now(UTC).timestamp()))
    value = (
        f"{user.id}.{issued_at}."
        f"{_session_signature(str(user.id), issued_at, user.password_hash, None)}"
    )
    response.set_cookie(
        SESSION_COOKIE_NAME,
        value,
        max_age=SESSION_MAX_AGE_SECONDS,
        httponly=True,
        secure=False,
        samesite="lax",
        path="/",
    )


def _session_signature(
    user_id: str,
    issued_at: str,
    password_hash: str,
    credentials: tuple[str, str] | None,
) -> str:
    secret = password_hash if credentials is None else f"{password_hash}:{credentials[1]}"
    return hmac.new(
        secret.encode("utf-8"),
        f"{user_id}.{issued_at}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PASSWORD_HASH_ITERATIONS
    )
    return (
        f"pbkdf2_sha256${PASSWORD_HASH_ITERATIONS}$"
        f"{base64.urlsafe_b64encode(salt).decode('ascii')}$"
        f"{base64.urlsafe_b64encode(digest).decode('ascii')}"
    )


def _verify_password(password: str, password_hash: str) -> bool:
    try:
        algorithm, iterations_text, salt_text, digest_text = password_hash.split("$", 3)
        iterations = int(iterations_text)
        salt = base64.urlsafe_b64decode(salt_text.encode("ascii"))
        expected = base64.urlsafe_b64decode(digest_text.encode("ascii"))
    except (ValueError, binascii.Error):
        return False
    if algorithm != "pbkdf2_sha256":
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return secrets.compare_digest(actual, expected)


def _require_admin(
    session: Session, request: Request, credentials: tuple[str, str] | None
) -> models.AppUser | Response:
    if credentials is None:
        return RedirectResponse(url=".", status_code=303)
    user = _request_user(session, request, credentials)
    if user is None:
        return RedirectResponse(url=_login_redirect_url(request), status_code=303)
    if not user.is_admin:
        return PlainTextResponse("Admin access required", status_code=403)
    return user


def _has_enabled_admin(session: Session) -> bool:
    return bool(
        session.scalar(
            select(models.AppUser.id)
            .where(models.AppUser.enabled.is_(True))
            .where(models.AppUser.is_admin.is_(True))
            .limit(1)
        )
    )


def _settings_dict(session: Session) -> dict[str, str]:
    settings = dict(MANAGED_SETTINGS)
    rows = session.scalars(select(models.AppSetting)).all()
    settings.update({row.key: row.value for row in rows})
    return settings


def _set_setting(session: Session, key: str, value: str) -> None:
    setting = session.get(models.AppSetting, key)
    now = datetime.now(UTC)
    if setting is None:
        session.add(models.AppSetting(key=key, value=value, updated_at=now))
        return
    setting.value = value
    setting.updated_at = now


def _is_public_path(path: str) -> bool:
    return path == "/health" or path == "/login" or path.startswith("/static/")


def _login_redirect_url(request: Request) -> str:
    return "login" if request.url.path == "/" else "./login"


def _admin_redirect(
    request: Request, message: str | None = None, error: str | None = None
) -> RedirectResponse:
    referer = request.headers.get("referer")
    target = referer.split("?", 1)[0] if referer else "admin"
    if message:
        target = f"{target}?message={quote(message)}"
    if error:
        target = f"{target}?error={quote(error)}"
    return RedirectResponse(url=target, status_code=303)


def _auth_challenge() -> PlainTextResponse:
    return PlainTextResponse(
        "Authentication required",
        status_code=401,
        headers={"WWW-Authenticate": 'Basic realm="homesolar"'},
    )


def _archive_dashboard_view(snapshot: DashboardSnapshot, t: dict[str, str]) -> dict:
    return {
        "total_power_w": snapshot.total_power_w,
        "total_today_kwh": snapshot.total_today_kwh,
        "inverters": [
            {
                "inverter": item.inverter,
                "latest": item.latest,
                "today_kwh": item.produced_energy_today_kwh,
                "last_poll": _archive_poll_event_dict(item.last_poll) if item.last_poll else None,
                "latest_alarm": _archive_alarm_dict(item.latest_alarm) if item.latest_alarm else None,
                "components": item.components,
                "age_label": _duration_label(item.health.age_seconds if item.health else None, t),
                "seen_age_label": _duration_label(item.health.seen_age_seconds if item.health else None, t),
                "is_online": item.health.is_online if item.health else False,
                "state_label": _archive_state_label(item.health, t),
            }
            for item in snapshot.inverters
        ],
        "online_count": snapshot.online_count,
        "alarm_count": snapshot.alarm_count,
        "poll_error_count": snapshot.poll_error_count,
        "health": _build_health(
            snapshot.online_count,
            snapshot.total_count,
            snapshot.alarm_count,
            snapshot.poll_error_count,
            t,
        ),
        "updated_at": snapshot.updated_at,
        "recent_events": [_archive_poll_event_dict(event) for event in snapshot.recent_events],
    }


def _archive_overview_data(
    snapshot: DashboardSnapshot, t: dict[str, str], inverter_id: str | None, median_kwh: float | None
) -> dict:
    inverters = [
        item for item in snapshot.inverters if inverter_id is None or item.inverter.id == inverter_id
    ]
    now_power = sum(
        item.latest.current_power_w
        for item in inverters
        if item.latest and item.latest.current_power_w is not None
    )
    today_kwh = sum(item.produced_energy_today_kwh or 0 for item in inverters)
    return {
        "inverter_id": inverter_id,
        "now_power_w": round(now_power),
        "today_kwh": round(today_kwh, 3),
        "median_kwh": median_kwh,
        "updated_at": snapshot.updated_at.isoformat(),
        "health": _build_health(
            snapshot.online_count,
            snapshot.total_count,
            snapshot.alarm_count,
            snapshot.poll_error_count,
            t,
        ),
    }


def _archive_today_total_kwh(snapshot: DashboardSnapshot, inverter_id: str | None) -> float:
    return round(
        sum(
            item.produced_energy_today_kwh or 0
            for item in snapshot.inverters
            if inverter_id is None or item.inverter.id == inverter_id
        ),
        3,
    )


def _archive_power_chart(snapshot: PowerChartSnapshot) -> dict:
    return {
        "range": snapshot.range_name,
        "series": [
            {
                "inverter_id": series.inverter_id,
                "name": series.name,
                "points": [
                    {"x": point.observed_at.isoformat(), "y": point.power_w}
                    for point in series.points
                ],
            }
            for series in snapshot.series
        ],
    }


def _archive_state_label(health: TelemetryHealth | None, t: dict[str, str]) -> tuple[str, str]:
    state = health.state if health else "waiting"
    if state == "alarm":
        return (t["state_alarm"], "bad")
    if state == "poll_error":
        return (t["state_poll_error"], "bad")
    if state == "online":
        return (t["state_online"], "ok")
    return (t["state_waiting"], "warn")


def _archive_alarm_dict(alarm: AlarmSnapshot) -> dict:
    return {
        "observed_at": alarm.observed_at.isoformat(),
        "status": alarm.status,
        "alarms": alarm.alarms,
    }


def _archive_inverter_response(item: InverterSnapshot) -> dict:
    response = _archive_inverter_dict(item.inverter)
    response.update(
        {
            "latest": _archive_reading_dict(item.latest) if item.latest else None,
            "last_poll": _archive_poll_event_dict(item.last_poll) if item.last_poll else None,
            "latest_alarm": _archive_alarm_dict(item.latest_alarm) if item.latest_alarm else None,
            "today_kwh": item.produced_energy_today_kwh,
        }
    )
    return response


def _archive_latest_inverter_response(item: InverterSnapshot) -> dict:
    if item.latest is None:
        return {"inverter_id": item.inverter.id, "reading": None}
    return {
        "inverter": _archive_inverter_dict(item.inverter),
        "reading": _archive_reading_dict(item.latest),
        "components": [_archive_component_dict(component) for component in item.components],
        "today_kwh": item.produced_energy_today_kwh,
    }


def _archive_inverter_dict(inverter: InverterIdentity) -> dict:
    return {
        "id": inverter.id,
        "name": inverter.name,
        "type": inverter.type,
        "base_url": inverter.base_url,
        "enabled": inverter.enabled,
        "timezone": inverter.timezone,
        "first_seen_at": _archive_iso_or_none(inverter.first_seen_at),
        "last_seen_at": _archive_iso_or_none(inverter.last_seen_at),
    }


def _archive_reading_dict(reading: ReadingSnapshot) -> dict:
    return {
        "id": reading.id,
        "inverter_id": reading.inverter_id,
        "observed_at": reading.observed_at.isoformat(),
        "current_power_w": reading.current_power_w,
        "energy_today_kwh": reading.energy_today_kwh,
        "energy_lifetime_kwh": reading.energy_lifetime_kwh,
        "energy_session_kwh": reading.energy_session_kwh,
        "status": reading.status,
        "extra": reading.extra,
    }


def _archive_component_dict(component: ComponentSnapshot) -> dict:
    return {
        "component_type": component.component_type,
        "component_name": component.component_name,
        "power_w": component.power_w,
        "voltage_v": component.voltage_v,
        "current_a": component.current_a,
        "energy_today_kwh": component.energy_today_kwh,
        "energy_lifetime_kwh": component.energy_lifetime_kwh,
        "energy_session_kwh": component.energy_session_kwh,
    }


def _archive_interval_dict(interval: EnergyIntervalSnapshot) -> dict:
    return {
        "inverter_id": interval.inverter_id,
        "start_at": interval.start_at.isoformat(),
        "end_at": interval.end_at.isoformat(),
        "generated_kwh": interval.generated_kwh,
        "source_counter": interval.source_counter,
        "confidence": interval.confidence,
        "notes": interval.notes,
    }


def _archive_iso_or_none(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _archive_poll_event_dict(event: PollEventSnapshot) -> dict:
    return {
        "inverter_id": event.inverter_id,
        "kind": event.kind,
        "started_at": event.started_at.isoformat(),
        "finished_at": event.finished_at.isoformat(),
        "duration_ms": round(event.duration_ms, 1),
        "success": event.success,
        "status_code": event.status_code,
        "error": event.error,
    }


def _build_health(
    online_count: int, total_count: int, alarm_count: int, poll_error_count: int, t: dict[str, str]
) -> dict:
    problems = []
    if alarm_count:
        problems.append(t["status_alarms"].format(n=alarm_count))
    if poll_error_count:
        problems.append(t["status_poll_errors"].format(n=poll_error_count))
    ok = not problems
    return {
        "ok": ok,
        "message": t["status_ok"] if ok else " · ".join(problems),
        "online_count": online_count,
        "total_count": total_count,
        "alarm_count": alarm_count,
        "poll_error_count": poll_error_count,
    }


def _duration_label(seconds: int | None, t: dict[str, str]) -> str:
    if seconds is None:
        return t["never"]
    if seconds < 60:
        return t["secs_ago"].format(n=seconds)
    minutes = seconds // 60
    if minutes < 60:
        return t["mins_ago"].format(n=minutes)
    hours = minutes // 60
    return t["hours_ago"].format(n=hours)
