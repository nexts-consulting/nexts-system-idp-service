import json
from typing import Any

import redis.asyncio as redis


class RedisStreams:
    PREPROCESS_TASKS = "preprocess.tasks"
    PREPROCESS_RESULTS = "preprocess.results"
    PREPROCESS_GROUP = "preprocess-workers"
    ORCHESTRATOR_GROUP = "orchestrator"

    def __init__(self, url: str) -> None:
        self._client = redis.from_url(url, decode_responses=True)

    @property
    def client(self) -> redis.Redis:
        return self._client

    async def ensure_groups(self) -> None:
        for stream, group in [
            (self.PREPROCESS_TASKS, self.PREPROCESS_GROUP),
            (self.PREPROCESS_RESULTS, self.ORCHESTRATOR_GROUP),
        ]:
            try:
                await self._client.xgroup_create(stream, group, id="0", mkstream=True)
            except redis.ResponseError as e:
                if "BUSYGROUP" not in str(e):
                    raise

    async def publish(self, stream: str, payload: dict[str, Any]) -> str:
        return await self._client.xadd(stream, {"data": json.dumps(payload)})

    async def read_group(
        self,
        stream: str,
        group: str,
        consumer: str,
        count: int = 10,
        block_ms: int = 5000,
    ) -> list[tuple[str, dict[str, Any]]]:
        entries = await self._client.xreadgroup(
            groupname=group,
            consumername=consumer,
            streams={stream: ">"},
            count=count,
            block=block_ms,
        )
        results: list[tuple[str, dict[str, Any]]] = []
        for _stream, messages in entries:
            for msg_id, fields in messages:
                data = json.loads(fields["data"])
                results.append((msg_id, data))
        return results

    async def ack(self, stream: str, group: str, msg_id: str) -> None:
        await self._client.xack(stream, group, msg_id)

    async def get_stream_lag(self, stream: str, group: str) -> int:
        try:
            pending = await self._client.xpending(stream, group)
            return pending.get("pending", 0) if isinstance(pending, dict) else 0
        except Exception:
            return 0

    async def set_key(self, key: str, value: str, ex: int | None = None) -> None:
        await self._client.set(key, value, ex=ex)

    async def get_key(self, key: str) -> str | None:
        return await self._client.get(key)

    async def incr_window(self, key: str, window_seconds: int = 60) -> int:
        pipe = self._client.pipeline()
        pipe.incr(key)
        pipe.expire(key, window_seconds)
        results = await pipe.execute()
        return int(results[0])

    async def close(self) -> None:
        await self._client.aclose()
