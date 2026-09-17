from __future__ import annotations

import argparse
import asyncio
import logging
import os
from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from app.infrastructure.repository import FinanceRepository

logger = logging.getLogger(__name__)
BRAZIL_TIMEZONE = ZoneInfo("America/Sao_Paulo")


async def dispatch_task_digest_if_due(now: datetime) -> bool:
    current = now if now.tzinfo else now.replace(tzinfo=UTC)
    current = current.astimezone(BRAZIL_TIMEZONE)
    if current.hour != 17:
        return False
    token = os.getenv("COFRIN_INTERNAL_TOKEN")
    if not token:
        logger.warning("task digest skipped: COFRIN_INTERNAL_TOKEN is not configured")
        return False
    endpoint = f"{os.getenv('APP_INTERNAL_URL', 'http://app:8000').rstrip('/')}/internal/tasks/digest/dispatch"
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(endpoint, headers={"X-Internal-Token": token})
            response.raise_for_status()
        logger.info("task digest dispatch completed")
        return True
    except httpx.HTTPError:
        logger.exception("task digest dispatch failed")
        return False


async def run_once(
    repository: Any,
    current_day: date | None = None,
    now: datetime | None = None,
) -> dict[str, int]:
    await repository.initialize()
    current_day = current_day or datetime.now(BRAZIL_TIMEZONE).date()
    now = now or datetime.now(UTC)
    await dispatch_task_digest_if_due(now)
    expired = await repository.expire_pending_confirmations(now)
    recurring = await repository.generate_recurring(current_day)
    installments = await repository.generate_installments(current_day)
    result = {
        "expired": expired,
        "recurring": len(recurring),
        "installments": len(installments),
    }
    logger.info(
        "worker cycle completed expired=%s recurring=%s installments=%s",
        result["expired"],
        result["recurring"],
        result["installments"],
    )
    return result


async def run_forever(interval_seconds: int) -> None:
    repository = FinanceRepository()
    try:
        while True:
            try:
                await run_once(repository)
            except Exception:
                logger.exception("worker cycle failed")
            await asyncio.sleep(interval_seconds)
    finally:
        await repository.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="CofrinIA background worker")
    parser.add_argument("--once", action="store_true", help="run one cycle and exit")
    args = parser.parse_args()
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    if args.once:
        repository = FinanceRepository()
        try:
            asyncio.run(run_once(repository))
        finally:
            asyncio.run(repository.close())
        return
    interval = max(10, int(os.getenv("WORKER_INTERVAL_SECONDS", "60")))
    asyncio.run(run_forever(interval))


if __name__ == "__main__":
    main()
