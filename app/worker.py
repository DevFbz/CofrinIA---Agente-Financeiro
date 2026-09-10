from __future__ import annotations

import argparse
import asyncio
import logging
import os
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any

from app.infrastructure.repository import FinanceRepository

logger = logging.getLogger(__name__)
BRAZIL_TIMEZONE = timezone(timedelta(hours=-3))


async def run_once(
    repository: Any,
    current_day: date | None = None,
    now: datetime | None = None,
) -> dict[str, int]:
    await repository.initialize()
    current_day = current_day or datetime.now(BRAZIL_TIMEZONE).date()
    now = now or datetime.now(UTC)
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
