from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import logging
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from homesolar.config import AppConfig
from homesolar.db import models
from homesolar.reports.render import build_user_report, selected_inverters, send_user_report

logger = logging.getLogger(__name__)
DEFAULT_APP_NAME = "homesolar"


def _app_name(session: Session) -> str:
    setting = session.get(models.AppSetting, "app_name")
    return setting.value if setting and setting.value else DEFAULT_APP_NAME


def report_due(session: Session, user: models.AppUser, send_hour_local: int, now: datetime) -> bool:
    if not user.reports_enabled or not user.email:
        return False
    inverters = selected_inverters(session, user)
    if not inverters:
        return False
    tz = ZoneInfo(inverters[0].timezone)
    local_now = now.astimezone(tz)
    if local_now.hour < send_hour_local:
        return False
    last = user.last_report_sent_at
    if last is None:
        return True
    if last.tzinfo is None:
        last = last.replace(tzinfo=UTC)
    return last.astimezone(tz).date() < local_now.date()


class ReportScheduler:
    def __init__(self, config: AppConfig, session_factory: sessionmaker[Session]) -> None:
        self.config = config
        self.session_factory = session_factory
        self._task: asyncio.Task | None = None
        self._stopped = asyncio.Event()

    async def start(self) -> None:
        if not self.config.email.enabled:
            return
        self._stopped.clear()
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._stopped.set()
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    async def _loop(self) -> None:
        while not self._stopped.is_set():
            try:
                await self.run_once()
            except Exception:  # pragma: no cover - defensive
                logger.exception("daily report tick failed")
            try:
                await asyncio.wait_for(
                    self._stopped.wait(), timeout=self.config.email.check_interval_seconds
                )
            except asyncio.TimeoutError:
                continue

    async def run_once(self, now: datetime | None = None) -> int:
        now = now or datetime.now(UTC)
        sent = 0
        with self.session_factory() as session:
            app_name = _app_name(session)
            users = session.scalars(
                select(models.AppUser)
                .where(models.AppUser.enabled.is_(True))
                .where(models.AppUser.reports_enabled.is_(True))
            ).all()
            for user in users:
                if not report_due(session, user, self.config.email.send_hour_local, now):
                    continue
                report = build_user_report(session, user, app_name, now)
                if report is None:
                    continue
                try:
                    await asyncio.to_thread(send_user_report, self.config.email, report)
                except Exception:
                    logger.exception("failed sending daily report to user id=%s", user.id)
                    continue
                user.last_report_sent_at = now
                session.commit()
                sent += 1
        return sent
