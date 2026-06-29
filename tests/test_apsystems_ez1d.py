from datetime import UTC, datetime

from homesolar.adapters.apsystems_ez1d import parse_output_data


def test_parse_output_data_stores_e_counters_as_session_energy() -> None:
    reading = parse_output_data(
        {
            "message": "success",
            "deviceId": "EZ1D",
            "data": {
                "p1": "310",
                "p2": "202",
                "e1": "1.25",
                "e2": "1.80",
                "te1": "4.5",
                "te2": "3.5",
            },
        },
        datetime(2026, 5, 26, 12, 0, tzinfo=UTC),
    )

    assert reading.current_power_w == 512
    assert reading.energy_today_kwh is None
    assert reading.energy_session_kwh == 3.05
    assert reading.energy_lifetime_kwh == 8.0

    by_name = {component.component_name: component for component in reading.components}
    assert by_name["channel_1"].energy_today_kwh is None
    assert by_name["channel_1"].energy_session_kwh == 1.25
    assert by_name["channel_2"].energy_session_kwh == 1.8
