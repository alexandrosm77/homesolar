from __future__ import annotations

import os
import re
from datetime import UTC, datetime

import httpx
from bs4 import BeautifulSoup

from homesolar.adapters.base import (
    AdapterResult,
    ComponentReading,
    InverterAdapter,
    NormalizedReading,
    RawContentType,
    RawPayload,
)
from homesolar.config import InverterConfig


def _number(value: str | None) -> float | None:
    if value is None:
        return None
    cleaned = value.strip().replace(",", ".")
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _cells(row) -> list[str]:
    return [cell.get_text(" ", strip=True).replace("\xa0", " ") for cell in row.find_all("td")]


class KostalHTMLAdapter(InverterAdapter):
    def __init__(self, config: InverterConfig, timeout_seconds: float) -> None:
        self.config = config
        self.timeout_seconds = timeout_seconds

    def _auth(self) -> httpx.BasicAuth | None:
        if not self.config.auth:
            return None
        username = os.environ.get(self.config.auth.username_env)
        password = os.environ.get(self.config.auth.password_env)
        if username is None or password is None:
            missing = [
                name
                for name, value in [
                    (self.config.auth.username_env, username),
                    (self.config.auth.password_env, password),
                ]
                if value is None
            ]
            raise RuntimeError(f"Missing required inverter auth env var(s): {', '.join(missing)}")
        return httpx.BasicAuth(username, password)

    async def fetch_live(self) -> AdapterResult:
        async with httpx.AsyncClient(timeout=self.timeout_seconds, auth=self._auth()) as client:
            response = await client.get(self.config.base_url)
            response.raise_for_status()

        raw = RawPayload(
            kind="live",
            content_type=RawContentType.HTML,
            body=response.text,
            status_code=response.status_code,
        )
        return AdapterResult(raw=raw, reading=parse_kostal_html(response.text))


def parse_kostal_html(html: str) -> NormalizedReading:
    soup = BeautifulSoup(html, "lxml")
    observed_at = datetime.now(UTC)
    text = " ".join(soup.get_text("\n", strip=True).replace("\xa0", " ").split())

    current_power_w = _regex_number(text, r"AC power\s+energy\s+current\s+([0-9.,]+)\s+W")
    lifetime_kwh = _regex_number(text, r"total energy\s+([0-9.,]+)\s+kWh")
    today_kwh = _regex_number(text, r"daily energy\s+([0-9.,]+)\s+kWh")
    status = _regex_text(text, r"status\s+(.+?)\s+PV generator")

    components = _parse_components(soup)
    return NormalizedReading(
        observed_at=observed_at,
        current_power_w=current_power_w,
        energy_today_kwh=today_kwh,
        energy_lifetime_kwh=lifetime_kwh,
        status=status,
        components=components,
        extra={"source": "html"},
    )


def _regex_number(text: str, pattern: str) -> float | None:
    match = re.search(pattern, text, flags=re.IGNORECASE)
    return _number(match.group(1)) if match else None


def _regex_text(text: str, pattern: str) -> str | None:
    match = re.search(pattern, text, flags=re.IGNORECASE)
    return match.group(1).strip() if match else None


def _parse_components(soup: BeautifulSoup) -> list[ComponentReading]:
    components: dict[tuple[str, str], ComponentReading] = {}
    current_string: str | None = None
    current_phase: str | None = None

    for row in soup.find_all("tr"):
        cells = [cell for cell in _cells(row) if cell and cell != "&nbsp"]
        if not cells:
            continue

        joined = " ".join(cells)
        string_match = re.search(r"String\s+(\d+)", joined, flags=re.IGNORECASE)
        phase_match = re.search(r"\bL(\d+)\b", joined, flags=re.IGNORECASE)
        if string_match:
            current_string = f"string_{string_match.group(1)}"
            components[("string", current_string)] = ComponentReading("string", current_string)
        if phase_match:
            current_phase = f"L{phase_match.group(1)}"
            components[("phase", current_phase)] = ComponentReading("phase", current_phase)

        if len(cells) >= 6 and cells[0].lower() == "voltage" and cells[3].lower() == "voltage":
            if current_string:
                components[("string", current_string)].voltage_v = _number(cells[1])
            if current_phase:
                components[("phase", current_phase)].voltage_v = _number(cells[4])

        if len(cells) >= 6 and cells[0].lower() == "current" and cells[3].lower() == "power":
            if current_string:
                components[("string", current_string)].current_a = _number(cells[1])
            if current_phase:
                components[("phase", current_phase)].power_w = _number(cells[4])

    return list(components.values())
