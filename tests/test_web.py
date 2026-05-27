import pytest
from sqlalchemy import select

from homesolar.config import (
    AppConfig,
    BasicAuthConfig,
    CollectorConfig,
    DatabaseConfig,
    InverterConfig,
    WebConfig,
)
from homesolar.db import models
from homesolar.web.app import create_app


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

    assert response.status_code == 200
    assert "Test" in response.text
    assert 'class="chart-wrap"' in response.text
    assert 'id="resetDashboard"' in response.text


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
    assert response.json()[0]["today_kwh"] == 10.13


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
            session.commit()

        power = client.get("/api/chart/power?range=24h&inverter_id=one")
        components = client.get("/api/chart/components?range=24h&inverter_id=one")
        component_voltage = client.get(
            "/api/chart/components?range=24h&inverter_id=one&metric=voltage_v"
        )
        summary = client.get("/api/summary?range=today&inverter_id=one")
        aggregates = client.get("/api/aggregates?period=daily&inverter_id=one&limit=2")
        dashboard = client.get("/")

    assert power.status_code == 200
    assert power.json()["series"][0]["points"][-1]["y"] == 1500
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
    assert 'href="/static/favicon.svg"' in admin_page.text
    assert 'src="/static/logo.svg"' in admin_page.text
    assert 'href="/static/css/app.css"' in admin_page.text
    assert "admin/static/css" not in admin_page.text
    assert create_user.status_code == 303
    assert create_user.headers["location"] == "http://testserver/admin?message=User%20created"
    assert save_settings.status_code == 303
    assert viewer_login.status_code == 303
    assert viewer_admin.status_code == 403
    assert dashboard.status_code == 200
    assert "My Solar" in dashboard.text
    assert "Garage roof" in dashboard.text


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

    assert 'href="/homesolar/static/css/app.css"' in login_page.text
    assert 'src="/homesolar/static/logo.svg"' in login_page.text
    assert 'href="/homesolar/static/favicon.svg"' in dashboard.text
    assert 'src="/homesolar/static/logo.svg"' in dashboard.text
    assert 'href="/homesolar/static/css/app.css"' in dashboard.text
    assert 'src="/homesolar/static/js/dashboard.js"' in dashboard.text
    assert 'data-api-base-path="/homesolar"' in dashboard.text
    assert 'id="powerChartTotal"' in dashboard.text
    assert 'href="/homesolar/static/css/app.css"' in admin_page.text
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
