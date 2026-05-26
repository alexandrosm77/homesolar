from __future__ import annotations

import base64
import binascii
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import UTC, datetime, time, timedelta
import hashlib
import hmac
import os
from pathlib import Path
import secrets
from urllib.parse import quote
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from homesolar.collector.processor import ensure_inverter
from homesolar.collector.scheduler import CollectorService
from homesolar.config import AppConfig
from homesolar.db import models
from homesolar.db.session import create_schema, engine_from_url, sessionmaker_from_engine

PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_WEB_USERNAME_ENV = "HOMESOLAR_WEB_USER"
DEFAULT_WEB_PASSWORD_ENV = "HOMESOLAR_WEB_PASSWORD"
SESSION_COOKIE_NAME = "homesolar_session"
SESSION_MAX_AGE_SECONDS = 60 * 60 * 24 * 14
PASSWORD_HASH_ITERATIONS = 210_000
MANAGED_SETTINGS = {
    "app_name": "homesolar",
    "dashboard_note": "",
}
templates = Jinja2Templates(directory=str(PACKAGE_DIR / "templates"))


def create_app(config: AppConfig) -> FastAPI:
    engine = engine_from_url(config.database.url)
    create_schema(engine)
    session_factory = sessionmaker_from_engine(engine)
    collector = CollectorService(config, session_factory)
    web_credentials = _web_credentials(config)

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
        try:
            yield
        finally:
            await collector.stop()

    app = FastAPI(title="homesolar", version="0.1.0", lifespan=lifespan)

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
            view = _dashboard_view(session)
            settings = _settings_dict(session)
        return templates.TemplateResponse(
            request,
            "dashboard.html",
            {"view": view, "settings": settings, "title": settings["app_name"]},
        )

    @app.get("/login", response_class=HTMLResponse)
    def login_page(request: Request) -> Response:
        with session_factory() as session:
            if web_credentials is not None and _request_user(session, request, web_credentials):
                return RedirectResponse(url=".", status_code=303)
            settings = _settings_dict(session)
        return templates.TemplateResponse(
            request,
            "login.html",
            {"title": f"{settings['app_name']} login", "settings": settings, "error": None},
        )

    @app.post("/login")
    async def login(
        request: Request,
        username: str = Form(default=""),
        password: str = Form(default=""),
    ) -> Response:
        if web_credentials is None:
            return RedirectResponse(url=".", status_code=303)

        with session_factory() as session:
            user = _authenticate_user(session, username, password, web_credentials)
            settings = _settings_dict(session)
            if user is not None:
                user.last_login_at = datetime.now(UTC)
                session.commit()
                response = RedirectResponse(url=".", status_code=303)
                _set_session_cookie(response, user)
                return response

        return templates.TemplateResponse(
            request,
            "login.html",
            {
                "title": f"{settings['app_name']} login",
                "settings": settings,
                "error": "Invalid username or password",
            },
            status_code=401,
        )

    @app.get("/admin", response_class=HTMLResponse)
    def admin_page(request: Request) -> Response:
        with session_factory() as session:
            user = _require_admin(session, request, web_credentials)
            if isinstance(user, Response):
                return user
            users = session.scalars(select(models.AppUser).order_by(models.AppUser.username)).all()
            settings = _settings_dict(session)
            return templates.TemplateResponse(
                request,
                "admin.html",
                {
                    "title": f"{settings['app_name']} admin",
                    "settings": settings,
                    "users": users,
                    "current_user": user,
                    "message": request.query_params.get("message"),
                    "error": request.query_params.get("error"),
                },
            )

    @app.post("/admin/users")
    def admin_create_user(
        request: Request,
        username: str = Form(default=""),
        password: str = Form(default=""),
        is_admin: str | None = Form(default=None),
        enabled: str | None = Form(default=None),
    ) -> Response:
        with session_factory() as session:
            user = _require_admin(session, request, web_credentials)
            if isinstance(user, Response):
                return user
            clean_username = username.strip()
            if not clean_username or not password:
                return _admin_redirect(request, error="Username and password are required")
            existing = session.scalar(
                select(models.AppUser).where(models.AppUser.username == clean_username)
            )
            if existing is not None:
                return _admin_redirect(request, error="User already exists")
            now = datetime.now(UTC)
            session.add(
                models.AppUser(
                    username=clean_username,
                    password_hash=_hash_password(password),
                    is_admin=is_admin == "on",
                    enabled=enabled == "on",
                    created_at=now,
                    updated_at=now,
                )
            )
            session.commit()
        return _admin_redirect(request, message="User created")

    @app.post("/admin/users/{user_id}/update")
    def admin_update_user(
        request: Request,
        user_id: int,
        is_admin: str | None = Form(default=None),
        enabled: str | None = Form(default=None),
    ) -> Response:
        with session_factory() as session:
            current_user = _require_admin(session, request, web_credentials)
            if isinstance(current_user, Response):
                return current_user
            user = session.get(models.AppUser, user_id)
            if user is None:
                raise HTTPException(status_code=404, detail="Unknown user")
            user.is_admin = is_admin == "on"
            user.enabled = enabled == "on"
            user.updated_at = datetime.now(UTC)
            if not _has_enabled_admin(session):
                session.rollback()
                return _admin_redirect(
                    request, error="At least one enabled admin is required"
                )
            session.commit()
        return _admin_redirect(request, message="User updated")

    @app.post("/admin/users/{user_id}/password")
    def admin_update_user_password(
        request: Request,
        user_id: int,
        password: str = Form(default=""),
    ) -> Response:
        with session_factory() as session:
            current_user = _require_admin(session, request, web_credentials)
            if isinstance(current_user, Response):
                return current_user
            user = session.get(models.AppUser, user_id)
            if user is None:
                raise HTTPException(status_code=404, detail="Unknown user")
            if not password:
                return _admin_redirect(request, error="Password is required")
            user.password_hash = _hash_password(password)
            user.updated_at = datetime.now(UTC)
            session.commit()
        return _admin_redirect(request, message="Password updated")

    @app.post("/admin/settings")
    def admin_update_settings(
        request: Request,
        app_name: str = Form(default="homesolar"),
        dashboard_note: str = Form(default=""),
    ) -> Response:
        with session_factory() as session:
            user = _require_admin(session, request, web_credentials)
            if isinstance(user, Response):
                return user
            _set_setting(session, "app_name", app_name.strip() or "homesolar")
            _set_setting(session, "dashboard_note", dashboard_note.strip())
            session.commit()
        return _admin_redirect(request, message="Settings saved")

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
        with session_factory() as session:
            inverters = session.scalars(select(models.Inverter).order_by(models.Inverter.name)).all()
            return [
                {
                    "id": inverter.id,
                    "name": inverter.name,
                    "type": inverter.type,
                    "base_url": inverter.base_url,
                    "enabled": inverter.enabled,
                    "timezone": inverter.timezone,
                    "first_seen_at": _iso_or_none(inverter.first_seen_at),
                    "last_seen_at": _iso_or_none(inverter.last_seen_at),
                    "latest": _latest_reading_dict(session, inverter.id),
                    "last_poll": _last_poll_dict(session, inverter.id),
                    "latest_alarm": _latest_alarm_dict(session, inverter.id),
                    "today_kwh": _today_energy_kwh(session, inverter),
                }
                for inverter in inverters
            ]

    @app.get("/api/inverters/{inverter_id}/latest")
    def latest_for_inverter(inverter_id: str) -> dict:
        with session_factory() as session:
            inverter = session.get(models.Inverter, inverter_id)
            if inverter is None:
                raise HTTPException(status_code=404, detail="Unknown inverter")
            latest = _latest_reading(session, inverter_id)
            if latest is None:
                return {"inverter_id": inverter_id, "reading": None}
            components = session.scalars(
                select(models.ComponentReading)
                .where(models.ComponentReading.reading_id == latest.id)
                .order_by(models.ComponentReading.component_type, models.ComponentReading.component_name)
            ).all()
            return {
                "inverter": _inverter_dict(inverter),
                "reading": _reading_dict(latest),
                "components": [_component_dict(component) for component in components],
                "today_kwh": _today_energy_kwh(session, inverter),
            }

    @app.get("/api/readings")
    def readings(
        inverter_id: str | None = None,
        from_: datetime | None = Query(default=None, alias="from"),
        to: datetime | None = None,
        limit: int = Query(default=500, ge=1, le=5000),
    ) -> list[dict]:
        with session_factory() as session:
            stmt = select(models.Reading).order_by(models.Reading.observed_at.desc()).limit(limit)
            if inverter_id:
                stmt = stmt.where(models.Reading.inverter_id == inverter_id)
            if from_:
                stmt = stmt.where(models.Reading.observed_at >= from_)
            if to:
                stmt = stmt.where(models.Reading.observed_at <= to)
            rows = session.scalars(stmt).all()
            return [_reading_dict(row) for row in reversed(rows)]

    @app.get("/api/energy/today")
    def energy_today() -> dict:
        with session_factory() as session:
            inverters = session.scalars(select(models.Inverter).order_by(models.Inverter.name)).all()
            items = [
                {
                    "inverter_id": inverter.id,
                    "name": inverter.name,
                    "today_kwh": _today_energy_kwh(session, inverter),
                }
                for inverter in inverters
            ]
        return {"total_kwh": sum(item["today_kwh"] or 0 for item in items), "inverters": items}

    @app.get("/api/energy/intervals")
    def intervals(
        inverter_id: str | None = None,
        from_: datetime | None = Query(default=None, alias="from"),
        to: datetime | None = None,
        limit: int = Query(default=1000, ge=1, le=10000),
    ) -> list[dict]:
        with session_factory() as session:
            stmt = (
                select(models.EnergyInterval)
                .order_by(models.EnergyInterval.start_at.desc())
                .limit(limit)
            )
            if inverter_id:
                stmt = stmt.where(models.EnergyInterval.inverter_id == inverter_id)
            if from_:
                stmt = stmt.where(models.EnergyInterval.end_at >= from_)
            if to:
                stmt = stmt.where(models.EnergyInterval.start_at <= to)
            rows = session.scalars(stmt).all()
            return [_interval_dict(row) for row in reversed(rows)]

    @app.get("/api/events")
    def events(
        inverter_id: str | None = None,
        limit: int = Query(default=200, ge=1, le=1000),
    ) -> list[dict]:
        with session_factory() as session:
            stmt = select(models.PollEvent).order_by(models.PollEvent.started_at.desc()).limit(limit)
            if inverter_id:
                stmt = stmt.where(models.PollEvent.inverter_id == inverter_id)
            rows = session.scalars(stmt).all()
            return [_poll_event_dict(row) for row in rows]

    @app.get("/api/chart/today")
    def chart_today() -> dict:
        return _power_chart(session_factory, range_name="today", inverter_id=None)

    @app.get("/api/chart/power")
    def chart_power(
        range_name: str = Query(default="today", alias="range"),
        inverter_id: str | None = None,
    ) -> dict:
        return _power_chart(session_factory, range_name=range_name, inverter_id=inverter_id)

    @app.get("/api/aggregates")
    def aggregates(
        period: str = Query(default="daily"),
        inverter_id: str | None = None,
        limit: int = Query(default=14, ge=1, le=60),
    ) -> dict:
        with session_factory() as session:
            return _aggregate_energy(session, period=period, inverter_id=inverter_id, limit=limit)

    @app.get("/api/summary")
    def range_summary(
        range_name: str = Query(default="today", alias="range"),
        inverter_id: str | None = None,
    ) -> dict:
        with session_factory() as session:
            return _summary_for_range(session, range_name=range_name, inverter_id=inverter_id)

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


def _dashboard_view(session: Session) -> dict:
    inverters = session.scalars(select(models.Inverter).order_by(models.Inverter.name)).all()
    items = []
    total_power = 0.0
    total_today = 0.0
    online_count = 0
    alarm_count = 0
    poll_error_count = 0
    now = datetime.now(UTC)
    for inverter in inverters:
        latest = _latest_reading(session, inverter.id)
        today = _today_energy_kwh(session, inverter)
        latest_power = latest.current_power_w if latest and latest.current_power_w is not None else None
        last_poll = _last_poll_event(session, inverter.id)
        alarm = _latest_alarm_dict(session, inverter.id)
        components = _latest_components(session, latest.id) if latest else []
        age_seconds = _age_seconds(latest.observed_at, now) if latest else None
        seen_age_seconds = _age_seconds(inverter.last_seen_at, now) if inverter.last_seen_at else None
        is_online = bool(latest and last_poll and last_poll.success and (age_seconds is None or age_seconds < 900))
        if latest_power is not None:
            total_power += latest_power
        if today is not None:
            total_today += today
        if is_online:
            online_count += 1
        if alarm and alarm["status"] != "normal":
            alarm_count += 1
        if last_poll and not last_poll.success:
            poll_error_count += 1
        items.append(
            {
                "inverter": inverter,
                "latest": latest,
                "today_kwh": today,
                "last_poll": _poll_event_dict(last_poll) if last_poll else None,
                "latest_alarm": alarm,
                "components": components,
                "age_label": _duration_label(age_seconds),
                "seen_age_label": _duration_label(seen_age_seconds),
                "is_online": is_online,
                "state_label": _state_label(is_online, last_poll, alarm),
            }
        )
    return {
        "total_power_w": total_power,
        "total_today_kwh": total_today,
        "inverters": items,
        "online_count": online_count,
        "alarm_count": alarm_count,
        "poll_error_count": poll_error_count,
        "updated_at": now,
        "recent_events": [_poll_event_dict(event) for event in _recent_poll_events(session, 6)],
    }


def _power_chart(session_factory, range_name: str, inverter_id: str | None) -> dict:
    with session_factory() as session:
        inverters = _filtered_inverters(session, inverter_id)
        start = _range_start_utc(range_name, inverters)
        series = []
        for inverter in inverters:
            readings = session.scalars(
                select(models.Reading)
                .where(models.Reading.inverter_id == inverter.id)
                .where(models.Reading.observed_at >= start)
                .order_by(models.Reading.observed_at)
            ).all()
            series.append(
                {
                    "inverter_id": inverter.id,
                    "name": inverter.name,
                    "points": [
                        {
                            "x": _as_utc(row.observed_at).isoformat(),
                            "y": row.current_power_w,
                        }
                        for row in readings
                        if row.current_power_w is not None
                    ],
                }
            )
        return {"range": range_name, "series": series}


def _summary_for_range(session: Session, range_name: str, inverter_id: str | None) -> dict:
    inverters = _filtered_inverters(session, inverter_id)
    start = _range_start_utc(range_name, inverters)
    reading_stmt = select(models.Reading).where(models.Reading.observed_at >= start)
    interval_stmt = select(models.EnergyInterval).where(models.EnergyInterval.end_at >= start)
    if inverter_id:
        reading_stmt = reading_stmt.where(models.Reading.inverter_id == inverter_id)
        interval_stmt = interval_stmt.where(models.EnergyInterval.inverter_id == inverter_id)

    readings = session.scalars(reading_stmt).all()
    intervals = session.scalars(
        interval_stmt.where(models.EnergyInterval.confidence == "normal")
    ).all()
    total_kwh = sum(interval.generated_kwh or 0 for interval in intervals)

    if range_name == "today":
        total_kwh = sum(_today_energy_kwh(session, inverter) or 0 for inverter in inverters)

    power_values = [row.current_power_w for row in readings if row.current_power_w is not None]
    avg_power = sum(power_values) / len(power_values) if power_values else None
    return {
        "range": range_name,
        "inverter_id": inverter_id,
        "total_kwh": round(total_kwh, 3),
        "peak_power_w": max(power_values) if power_values else None,
        "average_power_w": round(avg_power, 1) if avg_power is not None else None,
        "reading_count": len(readings),
    }


def _aggregate_energy(session: Session, period: str, inverter_id: str | None, limit: int) -> dict:
    period = period if period in {"daily", "weekly", "monthly", "yearly"} else "daily"
    inverters = _filtered_inverters(session, inverter_id)
    now = datetime.now(UTC)
    start = _aggregate_start(period, now, limit)
    intervals = session.scalars(
        select(models.EnergyInterval)
        .where(models.EnergyInterval.end_at >= start)
        .where(models.EnergyInterval.confidence == "normal")
        .order_by(models.EnergyInterval.end_at)
    ).all()

    interval_totals: dict[tuple[str, str], float] = defaultdict(float)
    for interval in intervals:
        if inverter_id and interval.inverter_id != inverter_id:
            continue
        key = _bucket_key(_as_utc(interval.end_at), period)
        interval_totals[(interval.inverter_id, key)] += interval.generated_kwh or 0

    daily_counter_totals = _daily_counter_totals(session, inverters, start) if period == "daily" else {}
    labels = _bucket_labels(period, now, limit)
    series = []
    for inverter in inverters:
        data = []
        for label in labels:
            value = daily_counter_totals.get((inverter.id, label))
            if value is None:
                value = interval_totals.get((inverter.id, label), 0.0)
            data.append(round(value, 3))
        series.append({"inverter_id": inverter.id, "name": inverter.name, "data": data})

    totals = [
        round(sum(series_item["data"][index] for series_item in series), 3)
        for index in range(len(labels))
    ]
    return {"period": period, "labels": labels, "series": series, "totals": totals}


def _filtered_inverters(session: Session, inverter_id: str | None) -> list[models.Inverter]:
    stmt = select(models.Inverter).order_by(models.Inverter.name)
    if inverter_id:
        stmt = stmt.where(models.Inverter.id == inverter_id)
    return list(session.scalars(stmt).all())


def _range_start_utc(range_name: str, inverters: list[models.Inverter]) -> datetime:
    if range_name == "today" and inverters:
        return min(_start_of_today_utc(inverter.timezone) for inverter in inverters)
    now = datetime.now(UTC)
    ranges = {
        "24h": timedelta(hours=24),
        "7d": timedelta(days=7),
        "30d": timedelta(days=30),
        "365d": timedelta(days=365),
    }
    return now - ranges.get(range_name, timedelta(days=1))


def _aggregate_start(period: str, now: datetime, limit: int) -> datetime:
    if period == "daily":
        return now - timedelta(days=limit)
    if period == "weekly":
        return now - timedelta(weeks=limit)
    if period == "monthly":
        return now - timedelta(days=32 * limit)
    return now - timedelta(days=370 * limit)


def _bucket_labels(period: str, now: datetime, limit: int) -> list[str]:
    if period == "daily":
        return [
            (now - timedelta(days=offset)).strftime("%Y-%m-%d")
            for offset in reversed(range(limit))
        ]
    if period == "weekly":
        return [
            _bucket_key(now - timedelta(weeks=offset), period)
            for offset in reversed(range(limit))
        ]
    if period == "monthly":
        labels = []
        year = now.year
        month = now.month
        for _ in range(limit):
            labels.append(f"{year:04d}-{month:02d}")
            month -= 1
            if month == 0:
                year -= 1
                month = 12
        return list(reversed(labels))
    return [str(now.year - offset) for offset in reversed(range(limit))]


def _bucket_key(value: datetime, period: str) -> str:
    if period == "daily":
        return value.strftime("%Y-%m-%d")
    if period == "weekly":
        iso = value.isocalendar()
        return f"{iso.year}-W{iso.week:02d}"
    if period == "monthly":
        return value.strftime("%Y-%m")
    return value.strftime("%Y")


def _daily_counter_totals(
    session: Session, inverters: list[models.Inverter], start: datetime
) -> dict[tuple[str, str], float]:
    totals: dict[tuple[str, str], float] = {}
    inverter_ids = [inverter.id for inverter in inverters]
    if not inverter_ids:
        return totals
    readings = session.scalars(
        select(models.Reading)
        .where(models.Reading.inverter_id.in_(inverter_ids))
        .where(models.Reading.observed_at >= start)
        .where(models.Reading.energy_today_kwh.is_not(None))
        .order_by(models.Reading.observed_at)
    ).all()
    for reading in readings:
        key = (reading.inverter_id, _bucket_key(_as_utc(reading.observed_at), "daily"))
        totals[key] = max(totals.get(key, 0.0), reading.energy_today_kwh or 0.0)
    return totals


def _latest_reading(session: Session, inverter_id: str) -> models.Reading | None:
    return session.scalars(
        select(models.Reading)
        .where(models.Reading.inverter_id == inverter_id)
        .order_by(models.Reading.observed_at.desc())
        .limit(1)
    ).first()


def _latest_reading_dict(session: Session, inverter_id: str) -> dict | None:
    latest = _latest_reading(session, inverter_id)
    return _reading_dict(latest) if latest else None


def _last_poll_dict(session: Session, inverter_id: str) -> dict | None:
    event = _last_poll_event(session, inverter_id)
    return _poll_event_dict(event) if event else None


def _last_poll_event(session: Session, inverter_id: str) -> models.PollEvent | None:
    event = session.scalars(
        select(models.PollEvent)
        .where(models.PollEvent.inverter_id == inverter_id)
        .order_by(models.PollEvent.started_at.desc())
        .limit(1)
    ).first()
    return event


def _latest_components(session: Session, reading_id: int) -> list[dict]:
    components = session.scalars(
        select(models.ComponentReading)
        .where(models.ComponentReading.reading_id == reading_id)
        .order_by(models.ComponentReading.component_type, models.ComponentReading.component_name)
    ).all()
    return [_component_dict(component) for component in components]


def _recent_poll_events(session: Session, limit: int) -> list[models.PollEvent]:
    return list(
        session.scalars(
            select(models.PollEvent).order_by(models.PollEvent.started_at.desc()).limit(limit)
        ).all()
    )


def _latest_alarm_dict(session: Session, inverter_id: str) -> dict | None:
    alarm = session.scalars(
        select(models.AlarmSnapshot)
        .where(models.AlarmSnapshot.inverter_id == inverter_id)
        .order_by(models.AlarmSnapshot.observed_at.desc())
        .limit(1)
    ).first()
    if alarm is None:
        return None
    return {
        "observed_at": alarm.observed_at.isoformat(),
        "status": alarm.status,
        "alarms": alarm.alarms,
    }


def _today_energy_kwh(session: Session, inverter: models.Inverter) -> float | None:
    latest = _latest_reading(session, inverter.id)
    if latest and latest.energy_today_kwh is not None:
        return latest.energy_today_kwh

    start = _start_of_today_utc(inverter.timezone)
    total = session.scalar(
        select(func.sum(models.EnergyInterval.generated_kwh))
        .where(models.EnergyInterval.inverter_id == inverter.id)
        .where(models.EnergyInterval.end_at >= start)
        .where(models.EnergyInterval.confidence == "normal")
    )
    if total is not None:
        return round(float(total), 3)
    return None


def _start_of_today_utc(timezone: str) -> datetime:
    tz = ZoneInfo(timezone)
    local_start = datetime.combine(datetime.now(tz).date(), time.min, tzinfo=tz)
    return local_start.astimezone(UTC)


def _inverter_dict(inverter: models.Inverter) -> dict:
    return {
        "id": inverter.id,
        "name": inverter.name,
        "type": inverter.type,
        "base_url": inverter.base_url,
        "enabled": inverter.enabled,
        "timezone": inverter.timezone,
        "first_seen_at": _iso_or_none(inverter.first_seen_at),
        "last_seen_at": _iso_or_none(inverter.last_seen_at),
    }


def _reading_dict(reading: models.Reading) -> dict:
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


def _iso_or_none(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _component_dict(component: models.ComponentReading) -> dict:
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


def _age_seconds(value: datetime, now: datetime) -> int:
    return max(0, int((now - _as_utc(value)).total_seconds()))


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _duration_label(seconds: int | None) -> str:
    if seconds is None:
        return "never"
    if seconds < 60:
        return f"{seconds}s ago"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    return f"{hours}h ago"


def _state_label(
    is_online: bool, last_poll: models.PollEvent | None, alarm: dict | None
) -> tuple[str, str]:
    if alarm and alarm["status"] != "normal":
        return ("alarm", "bad")
    if last_poll and not last_poll.success:
        return ("poll error", "bad")
    if is_online:
        return ("online", "ok")
    return ("waiting", "warn")


def _interval_dict(interval: models.EnergyInterval) -> dict:
    return {
        "inverter_id": interval.inverter_id,
        "start_at": interval.start_at.isoformat(),
        "end_at": interval.end_at.isoformat(),
        "generated_kwh": interval.generated_kwh,
        "source_counter": interval.source_counter,
        "confidence": interval.confidence,
        "notes": interval.notes,
    }


def _poll_event_dict(event: models.PollEvent) -> dict:
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
