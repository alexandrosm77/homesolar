from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
import logging
from typing import Awaitable

from sqlalchemy.orm import Session, sessionmaker

from homesolar.adapters.apsystems_ez1d import APsystemsEZ1DAdapter
from homesolar.adapters.base import AdapterResult, InverterAdapter
from homesolar.adapters.kostal_html import KostalHTMLAdapter
from homesolar.archive.read_model import stale_after_seconds
from homesolar.collector.processor import ensure_inverter, store_adapter_result, store_poll_event
from homesolar.config import AppConfig, InverterConfig

logger = logging.getLogger(__name__)
WATCHDOG_INTERVAL_SECONDS = 60


class CollectorService:
    def __init__(self, config: AppConfig, session_factory: sessionmaker[Session]) -> None:
        self.config = config
        self.session_factory = session_factory
        self._tasks: list[asyncio.Task] = []
        self._stopped = asyncio.Event()
        self._locks: dict[str, asyncio.Lock] = {}
        self._last_live_poll_at: dict[str, datetime] = {}

    async def start(self) -> None:
        self._stopped.clear()
        with self.session_factory() as session:
            for inverter in self.config.inverters:
                ensure_inverter(session, inverter)
            session.commit()

        for inverter_config in self.config.inverters:
            if not inverter_config.enabled:
                continue
            self._locks[inverter_config.id] = asyncio.Lock()
            self._last_live_poll_at[inverter_config.id] = datetime.now(UTC)
            adapter = build_adapter(inverter_config, self.config.collector.request_timeout_seconds)
            self._tasks.append(
                asyncio.create_task(
                    self._poll_loop(
                        inverter_config,
                        "live",
                        inverter_config.polling.live_seconds,
                        adapter.fetch_live,
                    )
                )
            )
            if adapter.supports_alarm and inverter_config.polling.alarm_seconds:
                self._tasks.append(
                    asyncio.create_task(
                        self._poll_loop(
                            inverter_config,
                            "alarm",
                            inverter_config.polling.alarm_seconds,
                            adapter.fetch_alarm,
                        )
                    )
                )
            if adapter.supports_info and inverter_config.polling.info_seconds:
                self._tasks.append(
                    asyncio.create_task(
                        self._poll_loop(
                            inverter_config,
                            "info",
                            inverter_config.polling.info_seconds,
                            adapter.fetch_info,
                        )
                    )
                )

        if self._last_live_poll_at:
            self._tasks.append(asyncio.create_task(self._watchdog_loop()))

    async def stop(self) -> None:
        self._stopped.set()
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    async def _poll_loop(
        self,
        inverter_config: InverterConfig,
        kind: str,
        interval_seconds: int,
        fetch: Callable[[], Awaitable[AdapterResult | None]],
    ) -> None:
        while not self._stopped.is_set():
            if kind == "live":
                self._last_live_poll_at[inverter_config.id] = datetime.now(UTC)
            try:
                await self._poll_once(inverter_config, kind, fetch)
            except Exception:  # pragma: no cover - defensive
                logger.exception(
                    "poll tick failed for inverter=%s kind=%s", inverter_config.id, kind
                )
            try:
                await asyncio.wait_for(self._stopped.wait(), timeout=interval_seconds)
            except TimeoutError:
                pass

    async def _watchdog_loop(self) -> None:
        while not self._stopped.is_set():
            try:
                await asyncio.wait_for(
                    self._stopped.wait(), timeout=WATCHDOG_INTERVAL_SECONDS
                )
                continue
            except TimeoutError:
                pass
            try:
                self.overdue_live_polls()
            except Exception:  # pragma: no cover - defensive
                logger.exception("live poll watchdog tick failed")

    def overdue_live_polls(self, now: datetime | None = None) -> list[str]:
        now = now or datetime.now(UTC)
        overdue: list[str] = []
        for inverter_config in self.config.inverters:
            last = self._last_live_poll_at.get(inverter_config.id)
            if last is None:
                continue
            age_seconds = (now - last).total_seconds()
            deadline = stale_after_seconds(inverter_config.polling.live_seconds)
            if age_seconds <= deadline:
                continue
            overdue.append(inverter_config.id)
            logger.error(
                "live poll loop for inverter=%s is not running: "
                "last attempt %.0fs ago, expected every %ss",
                inverter_config.id,
                age_seconds,
                inverter_config.polling.live_seconds,
            )
        return overdue

    async def _poll_once(
        self,
        inverter_config: InverterConfig,
        kind: str,
        fetch: Callable[[], Awaitable[AdapterResult | None]],
    ) -> None:
        started_at = datetime.now(UTC)
        status_code: int | None = None
        try:
            async with self._locks[inverter_config.id]:
                result = await fetch()
            finished_at = datetime.now(UTC)
            if result is not None:
                status_code = result.raw.status_code
                with self.session_factory() as session:
                    store_adapter_result(session, inverter_config, result)
                    store_poll_event(
                        session,
                        inverter_config.id,
                        kind,
                        started_at,
                        finished_at,
                        True,
                        status_code=status_code,
                    )
                    session.commit()
            else:
                with self.session_factory() as session:
                    store_poll_event(
                        session, inverter_config.id, kind, started_at, finished_at, True
                    )
                    session.commit()
        except Exception as exc:
            finished_at = datetime.now(UTC)
            logger.warning(
                "poll failed for inverter=%s kind=%s: %s",
                inverter_config.id,
                kind,
                f"{type(exc).__name__}: {exc}",
            )
            try:
                with self.session_factory() as session:
                    store_poll_event(
                        session,
                        inverter_config.id,
                        kind,
                        started_at,
                        finished_at,
                        False,
                        status_code=status_code,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                    session.commit()
            except Exception:
                logger.exception(
                    "could not record failed poll for inverter=%s kind=%s",
                    inverter_config.id,
                    kind,
                )


def build_adapter(config: InverterConfig, timeout_seconds: float) -> InverterAdapter:
    if config.type == "apsystems_ez1d":
        return APsystemsEZ1DAdapter(config, timeout_seconds)
    if config.type == "kostal_html":
        return KostalHTMLAdapter(config, timeout_seconds)
    raise ValueError(f"Unsupported inverter adapter type: {config.type}")
