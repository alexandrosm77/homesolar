import pytest
from sqlalchemy import select

from homesolar import __version__
from homesolar.config import (
    AppConfig,
    BasicAuthConfig,
    CollectorConfig,
    DatabaseConfig,
    EmailConfig,
    InverterConfig,
    WebConfig,
)
from homesolar.db import models
from homesolar.web.app import PACKAGE_DIR, create_app


@pytest.fixture(autouse=True)
def clear_default_web_auth_env(monkeypatch) -> None:
    monkeypatch.delenv("HOMESOLAR_WEB_USER", raising=False)
    monkeypatch.delenv("HOMESOLAR_WEB_PASSWORD", raising=False)
    monkeypatch.delenv("HOMESOLAR_WEB_BASE_PATH", raising=False)


def _yesterday_reading(timezone: str) -> tuple:
    from datetime import UTC, datetime, time, timedelta
    from zoneinfo import ZoneInfo

    tz = ZoneInfo(timezone)
    yesterday = (datetime.now(tz) - timedelta(days=1)).date()
    observed = datetime.combine(yesterday, time(12, 0), tzinfo=tz).astimezone(UTC)
    return observed, yesterday.isoformat()


def test_health_endpoint_with_collector_disabled(tmp_path) -> None:
    config = AppConfig(
        database=DatabaseConfig(url=f"sqlite:///{tmp_path / 'test.sqlite'}"),
        collector=CollectorConfig(enabled=False),
        inverters=[
            InverterConfig(
                id="test",
                name="Test",
                type="kostal_html",
                base_url="http://example.test",
            )
        ],
    )
    app = create_app(config)

    from fastapi.testclient import TestClient

    assert app.version == __version__

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_dashboard_renders_with_collector_disabled(tmp_path) -> None:
    config = AppConfig(
        database=DatabaseConfig(url=f"sqlite:///{tmp_path / 'test.sqlite'}"),
        collector=CollectorConfig(enabled=False),
        inverters=[
            InverterConfig(
                id="test",
                name="Test",
                type="kostal_html",
                base_url="http://example.test",
            )
        ],
    )
    app = create_app(config)

    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        response = client.get("/")
        latest = client.get("/api/inverters/test/latest")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert "Test" in response.text
    assert 'class="chart-wrap"' in response.text
    assert 'data-auto-refresh-default="false"' in response.text
    assert 'data-auto-refresh-seconds="60"' in response.text
    assert 'id="autoRefreshToggle"' in response.text
    assert 'id="resetDashboard"' in response.text
    assert latest.status_code == 200
    assert latest.json() == {"inverter_id": "test", "reading": None}


def test_dashboard_script_persists_auto_refresh_in_session() -> None:
    script = (PACKAGE_DIR / "static/js/dashboard.js").read_text(encoding="utf-8")

    assert 'const sessionSettingsKey = "homesolar.dashboard.settings";' in script
    assert "sessionStorage.getItem(sessionSettingsKey)" in script
    assert "sessionStorage.setItem(" in script
    assert "autoRefreshEnabled: Boolean(autoRefreshToggle?.checked)" in script
    assert "settings.autoRefreshEnabled" in script
    assert "document.hidden" not in script


def test_dashboard_and_login_render_in_greek(tmp_path) -> None:
    config = AppConfig(
        database=DatabaseConfig(url=f"sqlite:///{tmp_path / 'test.sqlite'}"),
        collector=CollectorConfig(enabled=False),
        inverters=[
            InverterConfig(
                id="test",
                name="Test",
                type="kostal_html",
                base_url="http://example.test",
            )
        ],
    )
    app = create_app(config)

    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        english = client.get("/")
        dashboard = client.get("/?lang=el")
        login = client.get("/login?lang=el")
        cookie_dashboard = client.get("/")

    assert english.status_code == 200
    assert '<html lang="en"' in english.text
    assert "Energy Aggregates" in english.text

    assert dashboard.status_code == 200
    assert '<html lang="el"' in dashboard.text
    assert "Συγκεντρωτικά Ενέργειας" in dashboard.text
    assert "homesolar_lang=el" in dashboard.headers.get("set-cookie", "")

    assert login.status_code == 200
    assert "Σύνδεση" in login.text

    assert '<html lang="el"' in cookie_dashboard.text


def test_inverter_daily_counter_takes_priority_over_zero_interval(tmp_path) -> None:
    config = AppConfig(
        database=DatabaseConfig(url=f"sqlite:///{tmp_path / 'test.sqlite'}"),
        collector=CollectorConfig(enabled=False),
        inverters=[
            InverterConfig(
                id="kostal",
                name="Kostal",
                type="kostal_html",
                base_url="http://example.test",
            )
        ],
    )
    app = create_app(config)

    from datetime import UTC, datetime

    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        session_factory = app.state.session_factory
        with session_factory() as session:
            session.add(
                models.Reading(
                    inverter_id="kostal",
                    observed_at=datetime.now(UTC),
                    current_power_w=5000,
                    energy_today_kwh=10.13,
                    energy_lifetime_kwh=64262,
                    energy_session_kwh=None,
                    status="feed in (MPP)",
                    extra={},
                )
            )
            session.add(
                models.EnergyInterval(
                    inverter_id="kostal",
                    start_at=datetime.now(UTC),
                    end_at=datetime.now(UTC),
                    start_reading_id=1,
                    end_reading_id=1,
                    generated_kwh=0,
                    source_counter="lifetime",
                    confidence="normal",
                )
            )
            session.commit()

        response = client.get("/api/inverters")

    assert response.status_code == 200
    body = response.json()[0]
    assert set(body) == {
        "id",
        "name",
        "type",
        "base_url",
        "enabled",
        "timezone",
        "first_seen_at",
        "last_seen_at",
        "latest",
        "last_poll",
        "latest_alarm",
        "today_kwh",
    }
    assert set(body["latest"]) == {
        "id",
        "inverter_id",
        "observed_at",
        "current_power_w",
        "energy_today_kwh",
        "energy_lifetime_kwh",
        "energy_session_kwh",
        "status",
        "extra",
    }
    assert body["today_kwh"] == 10.13


def test_today_summary_uses_archive_produced_energy_rule(tmp_path) -> None:
    config = AppConfig(
        database=DatabaseConfig(url=f"sqlite:///{tmp_path / 'test.sqlite'}"),
        collector=CollectorConfig(enabled=False),
        inverters=[
            InverterConfig(
                id="kostal",
                name="Kostal",
                type="kostal_html",
                base_url="http://example.test",
            )
        ],
    )
    app = create_app(config)

    from datetime import UTC, datetime, timedelta

    from fastapi.testclient import TestClient

    now = datetime.now(UTC)
    with TestClient(app) as client:
        with app.state.session_factory() as session:
            session.add_all(
                [
                    models.Reading(
                        inverter_id="kostal",
                        observed_at=now - timedelta(minutes=5),
                        current_power_w=4000,
                        energy_today_kwh=10.13,
                        energy_lifetime_kwh=64262,
                        energy_session_kwh=None,
                        status="feed in (MPP)",
                        extra={},
                    ),
                    models.Reading(
                        inverter_id="kostal",
                        observed_at=now,
                        current_power_w=3000,
                        energy_today_kwh=1.0,
                        energy_lifetime_kwh=64263,
                        energy_session_kwh=None,
                        status="feed in (MPP)",
                        extra={},
                    ),
                ]
            )
            session.commit()

        response = client.get("/api/summary?range=today&inverter_id=kostal")

    assert response.status_code == 200
    body = response.json()
    assert body["total_kwh"] == 10.13
    assert body["peak_power_w"] == 4000
    assert body["reading_count"] == 2


def test_filter_and_aggregate_endpoints(tmp_path) -> None:
    config = AppConfig(
        database=DatabaseConfig(url=f"sqlite:///{tmp_path / 'test.sqlite'}"),
        collector=CollectorConfig(enabled=False),
        inverters=[
            InverterConfig(
                id="one",
                name="One",
                type="kostal_html",
                base_url="http://example.test",
            )
        ],
    )
    app = create_app(config)

    from datetime import UTC, datetime, timedelta

    from fastapi.testclient import TestClient

    now = datetime.now(UTC)
    with TestClient(app) as client:
        session_factory = app.state.session_factory
        with session_factory() as session:
            first = models.Reading(
                inverter_id="one",
                observed_at=now - timedelta(minutes=10),
                current_power_w=1000,
                energy_today_kwh=1.0,
                energy_lifetime_kwh=100.0,
                energy_session_kwh=None,
                status="ok",
                extra={},
            )
            second = models.Reading(
                inverter_id="one",
                observed_at=now,
                current_power_w=1500,
                energy_today_kwh=1.25,
                energy_lifetime_kwh=100.25,
                energy_session_kwh=None,
                status="ok",
                extra={},
            )
            session.add_all([first, second])
            session.flush()
            session.add_all(
                [
                    models.ComponentReading(
                        inverter_id="one",
                        observed_at=first.observed_at,
                        reading_id=first.id,
                        component_type="channel",
                        component_name="channel_1",
                        power_w=400,
                        voltage_v=32,
                        current_a=12.5,
                        energy_today_kwh=0.4,
                    ),
                    models.ComponentReading(
                        inverter_id="one",
                        observed_at=first.observed_at,
                        reading_id=first.id,
                        component_type="channel",
                        component_name="channel_2",
                        power_w=600,
                        voltage_v=34,
                        current_a=17.6,
                        energy_today_kwh=0.6,
                    ),
                    models.ComponentReading(
                        inverter_id="one",
                        observed_at=second.observed_at,
                        reading_id=second.id,
                        component_type="channel",
                        component_name="channel_1",
                        power_w=650,
                        voltage_v=33,
                        current_a=19.7,
                        energy_today_kwh=0.55,
                    ),
                    models.ComponentReading(
                        inverter_id="one",
                        observed_at=second.observed_at,
                        reading_id=second.id,
                        component_type="channel",
                        component_name="channel_2",
                        power_w=850,
                        voltage_v=35,
                        current_a=24.3,
                        energy_today_kwh=0.7,
                    ),
                ]
            )
            session.add(
                models.EnergyInterval(
                    inverter_id="one",
                    start_at=first.observed_at,
                    end_at=second.observed_at,
                    start_reading_id=first.id,
                    end_reading_id=second.id,
                    generated_kwh=0.25,
                    source_counter="lifetime",
                    confidence="normal",
                )
            )
            session.add(
                models.PollEvent(
                    inverter_id="one",
                    kind="live",
                    started_at=second.observed_at,
                    finished_at=second.observed_at,
                    duration_ms=50,
                    success=True,
                )
            )
            second_id = second.id
            session.commit()

        readings = client.get("/api/readings?inverter_id=one")
        energy_today = client.get("/api/energy/today")
        intervals = client.get("/api/energy/intervals?inverter_id=one")
        events = client.get("/api/events?inverter_id=one")
        power = client.get("/api/chart/power?range=24h&inverter_id=one")
        today_power = client.get("/api/chart/power?range=today&inverter_id=one")
        today_alias = client.get("/api/chart/today")
        components = client.get("/api/chart/components?range=24h&inverter_id=one")
        component_voltage = client.get(
            "/api/chart/components?range=24h&inverter_id=one&metric=voltage_v"
        )
        summary = client.get("/api/summary?range=today&inverter_id=one")
        latest = client.get("/api/inverters/one/latest")
        missing_latest = client.get("/api/inverters/missing/latest")
        aggregates = client.get("/api/aggregates?period=daily&inverter_id=one&limit=2")
        dashboard = client.get("/")

    assert readings.status_code == 200
    assert readings.json()[-1]["id"] == second_id
    assert energy_today.status_code == 200
    assert energy_today.json()["inverters"][0]["today_kwh"] == 1.25
    assert intervals.status_code == 200
    assert intervals.json()[0]["generated_kwh"] == 0.25
    assert events.status_code == 200
    assert events.json()[0]["success"] is True
    assert power.status_code == 200
    assert power.json()["series"][0]["points"][-1]["y"] == 1500
    assert today_power.status_code == 200
    today_power_body = today_power.json()
    assert today_power_body["range"] == "today"
    assert today_power_body["series"][0]["inverter_id"] == "one"
    assert today_power_body["series"][0]["points"][-1]["y"] == 1500
    assert set(today_power_body["series"][0]["points"][-1]) == {"x", "y"}
    assert today_alias.status_code == 200
    assert today_alias.json()["series"][0]["points"][-1]["y"] == 1500
    assert components.status_code == 200
    assert components.json()["series"][0]["name"] == "Channel 1"
    assert components.json()["series"][0]["points"][-1]["y"] == 650
    assert components.json()["series"][1]["points"][-1]["y"] == 850
    assert components.json()["available_metrics"] == [
        {"metric": "power_w", "label": "Power", "unit": "W"},
        {"metric": "voltage_v", "label": "Voltage", "unit": "V"},
        {"metric": "current_a", "label": "Current", "unit": "A"},
        {"metric": "energy_today_kwh", "label": "Energy", "unit": "kWh"},
    ]
    assert component_voltage.status_code == 200
    assert component_voltage.json()["metric"] == "voltage_v"
    assert component_voltage.json()["unit"] == "V"
    assert component_voltage.json()["series"][0]["points"][-1]["y"] == 33
    assert summary.status_code == 200
    assert summary.json()["total_kwh"] == 1.25
    assert latest.status_code == 200
    latest_body = latest.json()
    assert latest_body["inverter"]["id"] == "one"
    assert latest_body["reading"]["id"] == second_id
    assert latest_body["components"][0]["component_name"] == "channel_1"
    assert latest_body["components"][1]["power_w"] == 850
    assert latest_body["today_kwh"] == 1.25
    assert missing_latest.status_code == 404
    assert aggregates.status_code == 200
    assert aggregates.json()["series"][0]["data"][-1] == 1.25
    assert dashboard.status_code == 200
    assert 'data-component-chart="one"' in dashboard.text
    assert 'data-component-metric="voltage_v"' in dashboard.text


def test_web_auth_protects_dashboard_and_api_but_not_health(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOMESOLAR_WEB_USER", "solar")
    monkeypatch.setenv("HOMESOLAR_WEB_PASSWORD", "secret")
    config = AppConfig(
        database=DatabaseConfig(url=f"sqlite:///{tmp_path / 'test.sqlite'}"),
        collector=CollectorConfig(enabled=False),
        web=WebConfig(
            auth=BasicAuthConfig(
                username_env="HOMESOLAR_WEB_USER",
                password_env="HOMESOLAR_WEB_PASSWORD",
            )
        ),
        inverters=[
            InverterConfig(
                id="test",
                name="Test",
                type="kostal_html",
                base_url="http://example.test",
            )
        ],
    )
    app = create_app(config)

    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        health = client.get("/health")
        dashboard = client.get("/", follow_redirects=False)
        api = client.get("/api/inverters")
        basic_auth_dashboard = client.get("/", auth=("solar", "secret"), follow_redirects=False)
        authorized_api = client.get("/api/inverters", auth=("solar", "secret"))

    assert health.status_code == 200
    assert dashboard.status_code == 303
    assert dashboard.headers["location"] == "login"
    assert api.status_code == 401
    assert api.headers["WWW-Authenticate"] == 'Basic realm="homesolar"'
    assert basic_auth_dashboard.status_code == 303
    assert basic_auth_dashboard.headers["location"] == "login"
    assert authorized_api.status_code == 200


def test_web_auth_login_and_logout_flow(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOMESOLAR_WEB_USER", "solar")
    monkeypatch.setenv("HOMESOLAR_WEB_PASSWORD", "secret")
    config = AppConfig(
        database=DatabaseConfig(url=f"sqlite:///{tmp_path / 'test.sqlite'}"),
        collector=CollectorConfig(enabled=False),
        web=WebConfig(
            auth=BasicAuthConfig(
                username_env="HOMESOLAR_WEB_USER",
                password_env="HOMESOLAR_WEB_PASSWORD",
            )
        ),
        inverters=[
            InverterConfig(
                id="test",
                name="Test",
                type="kostal_html",
                base_url="http://example.test",
            )
        ],
    )
    app = create_app(config)

    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        login_page = client.get("/login")
        bad_login = client.post(
            "/login",
            data={"username": "solar", "password": "wrong"},
            follow_redirects=False,
        )
        good_login = client.post(
            "/login",
            data={"username": "solar", "password": "secret"},
            follow_redirects=False,
        )
        dashboard = client.get("/")
        logout = client.post("/logout", follow_redirects=False)
        logged_out_dashboard = client.get("/", follow_redirects=False)

    assert login_page.status_code == 200
    assert "Sign in" in login_page.text
    assert bad_login.status_code == 401
    assert "Invalid username or password" in bad_login.text
    assert good_login.status_code == 303
    assert good_login.headers["location"] == "."
    assert "homesolar_session" in good_login.headers["set-cookie"]
    assert dashboard.status_code == 200
    assert "Test" in dashboard.text
    assert logout.status_code == 303
    assert logout.headers["location"] == "login"
    assert logged_out_dashboard.status_code == 303


def test_admin_page_manages_users_and_settings(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOMESOLAR_WEB_USER", "solar")
    monkeypatch.setenv("HOMESOLAR_WEB_PASSWORD", "secret")
    config = AppConfig(
        database=DatabaseConfig(url=f"sqlite:///{tmp_path / 'test.sqlite'}"),
        collector=CollectorConfig(enabled=False),
        web=WebConfig(
            auth=BasicAuthConfig(
                username_env="HOMESOLAR_WEB_USER",
                password_env="HOMESOLAR_WEB_PASSWORD",
            )
        ),
        inverters=[
            InverterConfig(
                id="test",
                name="Test",
                type="kostal_html",
                base_url="http://example.test",
            )
        ],
    )
    app = create_app(config)

    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        with app.state.session_factory() as session:
            admin = session.scalar(select(models.AppUser).where(models.AppUser.username == "solar"))
            assert admin is not None
            assert admin.is_admin is True
            assert admin.enabled is True

        client.post(
            "/login",
            data={"username": "solar", "password": "secret"},
            follow_redirects=False,
        )
        admin_page = client.get("/admin")
        create_user = client.post(
            "/admin/users",
            data={
                "username": "viewer",
                "password": "viewer-secret",
                "enabled": "on",
            },
            headers={"referer": "http://testserver/admin"},
            follow_redirects=False,
        )
        save_settings = client.post(
            "/admin/settings",
            data={"app_name": "My Solar", "dashboard_note": "Garage roof"},
            headers={"referer": "http://testserver/admin"},
            follow_redirects=False,
        )
        client.post("/logout", follow_redirects=False)
        viewer_login = client.post(
            "/login",
            data={"username": "viewer", "password": "viewer-secret"},
            follow_redirects=False,
        )
        viewer_admin = client.get("/admin")
        dashboard = client.get("/")

    assert admin_page.status_code == 200
    assert "solar" in admin_page.text
    assert "<built-in method" not in admin_page.text
    assert ">Update</button>" in admin_page.text
    assert f'href="/static/favicon.svg?v={__version__}"' in admin_page.text
    assert f'src="/static/logo.svg?v={__version__}"' in admin_page.text
    assert f'href="/static/css/app.css?v={__version__}"' in admin_page.text
    assert "admin/static/css" not in admin_page.text
    assert create_user.status_code == 303
    assert create_user.headers["location"] == "http://testserver/admin?message=User%20created"
    assert save_settings.status_code == 303
    assert viewer_login.status_code == 303
    assert viewer_admin.status_code == 403
    assert dashboard.status_code == 200
    assert "My Solar" in dashboard.text
    assert "Garage roof" in dashboard.text


def test_per_user_language_preference(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOMESOLAR_WEB_USER", "solar")
    monkeypatch.setenv("HOMESOLAR_WEB_PASSWORD", "secret")
    config = AppConfig(
        database=DatabaseConfig(url=f"sqlite:///{tmp_path / 'test.sqlite'}"),
        collector=CollectorConfig(enabled=False),
        web=WebConfig(
            auth=BasicAuthConfig(
                username_env="HOMESOLAR_WEB_USER",
                password_env="HOMESOLAR_WEB_PASSWORD",
            )
        ),
        inverters=[
            InverterConfig(
                id="test",
                name="Test",
                type="kostal_html",
                base_url="http://example.test",
            )
        ],
    )
    app = create_app(config)

    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        client.post(
            "/login",
            data={"username": "solar", "password": "secret"},
            follow_redirects=False,
        )
        client.post(
            "/admin/users",
            data={
                "username": "greek",
                "password": "greek-secret",
                "enabled": "on",
                "language": "el",
            },
            headers={"referer": "http://testserver/admin"},
            follow_redirects=False,
        )

        with app.state.session_factory() as session:
            greek_user = session.scalar(
                select(models.AppUser).where(models.AppUser.username == "greek")
            )
            assert greek_user is not None
            assert greek_user.language == "el"
            solar_id = session.scalar(
                select(models.AppUser.id).where(models.AppUser.username == "solar")
            )

        client.post(
            f"/admin/users/{solar_id}/update",
            data={"is_admin": "on", "enabled": "on", "language": "el"},
            headers={"referer": "http://testserver/admin"},
            follow_redirects=False,
        )

        # No lang param or cookie: the stored preference drives the language.
        preference_dashboard = client.get("/")
        # Switching via ?lang persists the new choice for the logged-in user.
        client.get("/?lang=en")

        with app.state.session_factory() as session:
            solar = session.get(models.AppUser, solar_id)
            assert solar.language == "en"

    assert preference_dashboard.status_code == 200
    assert '<html lang="el"' in preference_dashboard.text


def test_public_base_path_prefixes_assets(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOMESOLAR_WEB_USER", "solar")
    monkeypatch.setenv("HOMESOLAR_WEB_PASSWORD", "secret")
    monkeypatch.setenv("HOMESOLAR_WEB_BASE_PATH", "/homesolar")
    config = AppConfig(
        database=DatabaseConfig(url=f"sqlite:///{tmp_path / 'test.sqlite'}"),
        collector=CollectorConfig(enabled=False),
        web=WebConfig(
            auth=BasicAuthConfig(
                username_env="HOMESOLAR_WEB_USER",
                password_env="HOMESOLAR_WEB_PASSWORD",
            )
        ),
        inverters=[
            InverterConfig(
                id="test",
                name="Test",
                type="kostal_html",
                base_url="http://example.test",
            )
        ],
    )
    app = create_app(config)

    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        login_page = client.get("/login")
        client.post(
            "/login",
            data={"username": "solar", "password": "secret"},
            follow_redirects=False,
        )
        dashboard = client.get("/")
        admin_page = client.get("/admin")

    assert f'href="/homesolar/static/css/app.css?v={__version__}"' in login_page.text
    assert f'src="/homesolar/static/logo.svg?v={__version__}"' in login_page.text
    assert f'href="/homesolar/static/favicon.svg?v={__version__}"' in dashboard.text
    assert f'src="/homesolar/static/logo.svg?v={__version__}"' in dashboard.text
    assert f'href="/homesolar/static/css/app.css?v={__version__}"' in dashboard.text
    assert f'src="/homesolar/static/js/dashboard.js?v={__version__}"' in dashboard.text
    assert 'data-api-base-path="/homesolar"' in dashboard.text
    assert 'id="powerChartTotal"' in dashboard.text
    assert f'href="/homesolar/static/css/app.css?v={__version__}"' in admin_page.text
    assert "admin/static/css" not in admin_page.text


def test_web_auth_requires_configured_env_vars(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("HOMESOLAR_WEB_USER", raising=False)
    monkeypatch.delenv("HOMESOLAR_WEB_PASSWORD", raising=False)
    config = AppConfig(
        database=DatabaseConfig(url=f"sqlite:///{tmp_path / 'test.sqlite'}"),
        collector=CollectorConfig(enabled=False),
        web=WebConfig(
            auth=BasicAuthConfig(
                username_env="HOMESOLAR_WEB_USER",
                password_env="HOMESOLAR_WEB_PASSWORD",
            )
        ),
        inverters=[
            InverterConfig(
                id="test",
                name="Test",
                type="kostal_html",
                base_url="http://example.test",
            )
        ],
    )

    with pytest.raises(RuntimeError, match="Missing required web auth env var"):
        create_app(config)


def test_default_web_auth_env_vars_enable_auth_without_yaml_config(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOMESOLAR_WEB_USER", "solar")
    monkeypatch.setenv("HOMESOLAR_WEB_PASSWORD", "secret")
    config = AppConfig(
        database=DatabaseConfig(url=f"sqlite:///{tmp_path / 'test.sqlite'}"),
        collector=CollectorConfig(enabled=False),
        inverters=[
            InverterConfig(
                id="test",
                name="Test",
                type="kostal_html",
                base_url="http://example.test",
            )
        ],
    )
    app = create_app(config)

    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        anonymous = client.get("/", follow_redirects=False)
        authorized_api = client.get("/api/inverters", auth=("solar", "secret"))

    assert anonymous.status_code == 303
    assert anonymous.headers["location"] == "login"
    assert authorized_api.status_code == 200


def test_default_web_auth_env_vars_fail_closed_if_partially_configured(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("HOMESOLAR_WEB_USER", "solar")
    monkeypatch.delenv("HOMESOLAR_WEB_PASSWORD", raising=False)
    config = AppConfig(
        database=DatabaseConfig(url=f"sqlite:///{tmp_path / 'test.sqlite'}"),
        collector=CollectorConfig(enabled=False),
        inverters=[
            InverterConfig(
                id="test",
                name="Test",
                type="kostal_html",
                base_url="http://example.test",
            )
        ],
    )

    with pytest.raises(RuntimeError, match="HOMESOLAR_WEB_PASSWORD"):
        create_app(config)


def _reporting_config(tmp_path, email_enabled: bool = True) -> AppConfig:
    return AppConfig(
        database=DatabaseConfig(url=f"sqlite:///{tmp_path / 'test.sqlite'}"),
        collector=CollectorConfig(enabled=False),
        email=EmailConfig(enabled=email_enabled, host="localhost"),
        web=WebConfig(
            auth=BasicAuthConfig(
                username_env="HOMESOLAR_WEB_USER",
                password_env="HOMESOLAR_WEB_PASSWORD",
            )
        ),
        inverters=[
            InverterConfig(id="test", name="Test", type="kostal_html", base_url="http://example.test")
        ],
    )


def test_admin_persists_report_preferences(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOMESOLAR_WEB_USER", "solar")
    monkeypatch.setenv("HOMESOLAR_WEB_PASSWORD", "secret")
    app = create_app(_reporting_config(tmp_path))

    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        client.post("/login", data={"username": "solar", "password": "secret"}, follow_redirects=False)
        client.post(
            "/admin/users",
            data={
                "username": "reporter",
                "password": "reporter-secret",
                "enabled": "on",
                "email": "reporter@example.test",
                "reports_enabled": "on",
                "report_language": "el",
                "report_inverter_ids": ["test", "ghost"],
            },
            headers={"referer": "http://testserver/admin"},
            follow_redirects=False,
        )
        with app.state.session_factory() as session:
            user = session.scalar(select(models.AppUser).where(models.AppUser.username == "reporter"))
            assert user.email == "reporter@example.test"
            assert user.reports_enabled is True
            assert user.report_language == "el"
            assert user.report_inverter_ids == ["test"]


def test_send_test_report_emails_user(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOMESOLAR_WEB_USER", "solar")
    monkeypatch.setenv("HOMESOLAR_WEB_PASSWORD", "secret")

    captured: dict = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout=None):
            captured["host"] = host

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def send_message(self, message):
            captured["message"] = message

    monkeypatch.setattr("homesolar.reports.email.smtplib.SMTP", FakeSMTP)
    app = create_app(_reporting_config(tmp_path))

    from fastapi.testclient import TestClient

    observed, _ = _yesterday_reading("Europe/London")
    with TestClient(app) as client:
        with app.state.session_factory() as session:
            session.add(
                models.Reading(
                    inverter_id="test",
                    observed_at=observed,
                    current_power_w=2000,
                    energy_today_kwh=12.5,
                    energy_lifetime_kwh=500.0,
                    energy_session_kwh=None,
                    status="ok",
                    extra={},
                )
            )
            session.commit()
            solar_id = session.scalar(
                select(models.AppUser.id).where(models.AppUser.username == "solar")
            )

        client.post("/login", data={"username": "solar", "password": "secret"}, follow_redirects=False)
        client.post(
            f"/admin/users/{solar_id}/update",
            data={
                "is_admin": "on",
                "enabled": "on",
                "email": "solar@example.test",
                "reports_enabled": "on",
                "report_language": "en",
                "report_inverter_ids": ["test"],
            },
            headers={"referer": "http://testserver/admin"},
            follow_redirects=False,
        )
        response = client.post(
            f"/admin/users/{solar_id}/send-test-report",
            headers={"referer": "http://testserver/admin"},
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert "message=" in response.headers["location"]
    message = captured["message"]
    assert message["To"] == "solar@example.test"
    assert "Solar report" in message["Subject"]
    assert any(part.get_content_type() == "image/png" for part in message.walk())


def test_report_due_logic(tmp_path, monkeypatch) -> None:
    from datetime import UTC, datetime
    from zoneinfo import ZoneInfo

    from homesolar.reports.scheduler import report_due

    monkeypatch.setenv("HOMESOLAR_WEB_USER", "solar")
    monkeypatch.setenv("HOMESOLAR_WEB_PASSWORD", "secret")
    app = create_app(_reporting_config(tmp_path))

    from fastapi.testclient import TestClient

    london = ZoneInfo("Europe/London")
    morning = datetime(2026, 1, 2, 8, 0, tzinfo=london).astimezone(UTC)
    pre_dawn = datetime(2026, 1, 2, 3, 0, tzinfo=london).astimezone(UTC)

    with TestClient(app):
        with app.state.session_factory() as session:
            user = models.AppUser(
                username="r",
                password_hash="x",
                is_admin=False,
                enabled=True,
                created_at=morning,
                updated_at=morning,
                email="r@example.test",
                reports_enabled=True,
                report_inverter_ids=["test"],
            )
            session.add(user)
            session.commit()

            assert report_due(session, user, 5, morning) is True
            assert report_due(session, user, 5, pre_dawn) is False

            user.last_report_sent_at = morning
            assert report_due(session, user, 5, morning) is False


def test_overview_endpoint_scopes_hero_and_reports_whole_system_health(tmp_path) -> None:
    config = AppConfig(
        database=DatabaseConfig(url=f"sqlite:///{tmp_path / 'test.sqlite'}"),
        collector=CollectorConfig(enabled=False),
        inverters=[
            InverterConfig(id="one", name="One", type="kostal_html", base_url="http://example.test"),
            InverterConfig(id="two", name="Two", type="kostal_html", base_url="http://example.test"),
        ],
    )
    app = create_app(config)

    from datetime import UTC, datetime

    from fastapi.testclient import TestClient

    now = datetime.now(UTC)
    with TestClient(app) as client:
        with app.state.session_factory() as session:
            session.add_all(
                [
                    models.Reading(
                        inverter_id="one",
                        observed_at=now,
                        current_power_w=1500,
                        energy_today_kwh=1.25,
                        energy_lifetime_kwh=100.0,
                        energy_session_kwh=None,
                        status="ok",
                        extra={},
                    ),
                    models.Reading(
                        inverter_id="two",
                        observed_at=now,
                        current_power_w=500,
                        energy_today_kwh=0.5,
                        energy_lifetime_kwh=50.0,
                        energy_session_kwh=None,
                        status="ok",
                        extra={},
                    ),
                ]
            )
            session.commit()

        whole = client.get("/api/overview")
        scoped = client.get("/api/overview?inverter_id=one")

    assert whole.status_code == 200
    body = whole.json()
    assert set(body) == {
        "inverter_id",
        "now_power_w",
        "today_kwh",
        "median_kwh",
        "updated_at",
        "health",
    }
    assert body["now_power_w"] == 2000
    assert body["today_kwh"] == 1.75
    assert body["median_kwh"] == 0.0
    assert body["health"]["ok"] is True
    assert body["health"]["message"] == "All systems normal"

    assert scoped.status_code == 200
    scoped_body = scoped.json()
    assert scoped_body["inverter_id"] == "one"
    assert scoped_body["now_power_w"] == 1500
    assert scoped_body["today_kwh"] == 1.25


def test_theme_cookie_persists_and_renders_html_class(tmp_path) -> None:
    config = AppConfig(
        database=DatabaseConfig(url=f"sqlite:///{tmp_path / 'test.sqlite'}"),
        collector=CollectorConfig(enabled=False),
        inverters=[
            InverterConfig(id="test", name="Test", type="kostal_html", base_url="http://example.test")
        ],
    )
    app = create_app(config)

    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        default = client.get("/")
        dark = client.get("/?theme=dark")
        cookie_dashboard = client.get("/")

    assert 'class="theme-system"' in default.text
    assert 'class="theme-dark"' in dark.text
    assert "homesolar_theme=dark" in dark.headers.get("set-cookie", "")
    assert 'class="theme-dark"' in cookie_dashboard.text
