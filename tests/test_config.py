from pathlib import Path

from homesolar.config import load_config


def test_load_example_config() -> None:
    config = load_config(Path("config/example.yaml"))

    assert config.inverters[0].id == "apsystems_home"
    assert config.web.base_path == ""
    assert config.inverters[0].polling.live_seconds == 60
    assert config.inverters[1].type == "kostal_html"
    assert config.inverters[1].polling.alarm_seconds is None
