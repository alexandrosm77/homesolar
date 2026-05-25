from pathlib import Path

from homesolar.adapters.kostal_html import parse_kostal_html


def test_parse_remote_inverter_sample() -> None:
    html = Path("remote_inverter_scrap.html").read_text(encoding="ISO-8859-1")

    reading = parse_kostal_html(html)

    assert reading.current_power_w == 4588
    assert reading.energy_lifetime_kwh == 64258
    assert reading.energy_today_kwh == 6.32
    assert reading.status == "feed in (MPP)"

    by_name = {component.component_name: component for component in reading.components}
    assert by_name["string_1"].voltage_v == 535
    assert by_name["string_1"].current_a == 3.36
    assert by_name["L1"].voltage_v == 245
    assert by_name["L1"].power_w == 1549
    assert by_name["string_3"].voltage_v == 436
    assert by_name["L3"].power_w == 1523
