import hashlib
import hmac
import json
import time
from datetime import datetime, timedelta

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idp_app.db.models import OutboxCallback
from idp_app.config import Settings

logger = structlog.get_logger()


def sign_payload(secret: str, body: bytes, timestamp: str) -> str:
    message = f"{timestamp}.".encode() + body
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


async def enqueue_callback(
    session: AsyncSession,
    job_id,
    callback_url: str,
    payload: dict,
    max_attempts: int = 5,
) -> None:
    outbox = OutboxCallback(
        job_id=job_id,
        callback_url=callback_url,
        payload=payload,
        max_attempts=max_attempts,
    )
    session.add(outbox)
    await session.commit()


async def deliver_pending(settings: Settings, session: AsyncSession) -> int:
    now = datetime.utcnow()
    q = await session.execute(
        select(OutboxCallback).where(
            OutboxCallback.delivered_at.is_(None),
            OutboxCallback.next_retry_at <= now,
            OutboxCallback.attempts < OutboxCallback.max_attempts,
        ).limit(20)
    )
    rows = q.scalars().all()
    delivered = 0
    for row in rows:
        body = json.dumps(row.payload, default=str).encode()
        ts = str(int(time.time()))
        signature = sign_payload(settings.callback_hmac_secret, body, ts)
        headers = {
            "Content-Type": "application/json",
            "X-IDP-Timestamp": ts,
            "X-IDP-Signature": signature,
        }
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(row.callback_url, content=body, headers=headers)
                resp.raise_for_status()
            row.delivered_at = datetime.utcnow()
            delivered += 1
        except Exception as e:
            row.attempts += 1
            row.last_error = str(e)
            row.next_retry_at = datetime.utcnow() + timedelta(seconds=2 ** row.attempts)
            logger.warning("callback_failed", job_id=str(row.job_id), error=str(e))
        await session.commit()
    return delivered
