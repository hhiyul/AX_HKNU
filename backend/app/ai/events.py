"""실행별 진행 사건을 프로세스 메모리에 모아 SSE로 흘려보낸다.

DB에는 최종 답변과 실행 기록만 남긴다. 끝난 실행의 사건은 keep_seconds 동안만 다시 받을 수 있다.
"""
import asyncio
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

KEEP_SECONDS = 600
WAIT_SECONDS = 1.0


@dataclass
class _Run:
    events: list[dict] = field(default_factory=list)
    # 새 사건이 오면 set하고 새 Event로 바꾼다. 기다리는 쪽은 바꾸기 전 것을 기다린다.
    changed: asyncio.Event = field(default_factory=asyncio.Event)
    closed_at: float | None = None


class RunEvents:
    def __init__(self, keep_seconds: float = KEEP_SECONDS):
        self.keep_seconds = keep_seconds
        self._runs: dict[str, _Run] = {}

    def _expire(self) -> None:
        now = time.monotonic()
        expired = [
            run_id
            for run_id, run in self._runs.items()
            if run.closed_at is not None and now - run.closed_at >= self.keep_seconds
        ]
        for run_id in expired:
            del self._runs[run_id]

    def _wake(self, run: _Run) -> None:
        run.changed.set()
        run.changed = asyncio.Event()

    def publish(self, run_id: str, kind: str, content: str) -> int:
        self._expire()
        run = self._runs.setdefault(run_id, _Run())
        seq = len(run.events) + 1
        run.events.append({"seq": seq, "kind": kind, "content": content})
        self._wake(run)
        return seq

    def close(self, run_id: str) -> None:
        run = self._runs.setdefault(run_id, _Run())
        run.closed_at = time.monotonic()
        self._wake(run)

    async def subscribe(
        self, run_id: str, after: int = 0, ping_seconds: float | None = None
    ) -> AsyncIterator[dict | None]:
        """after 다음 사건부터 낸다. 실행이 닫히고 남은 사건을 다 내면 끝난다. 모르는 실행이면 바로 끝난다.

        ping_seconds를 주면 그동안 사건이 없을 때 None을 낸다(연결 유지용).
        """
        self._expire()
        seq = after
        idle = 0.0
        wait = min(WAIT_SECONDS, ping_seconds) if ping_seconds else WAIT_SECONDS
        while True:
            run = self._runs.get(run_id)
            if run is None:
                return
            waiting = run.changed
            for event in run.events[seq:]:
                seq = event["seq"]
                idle = 0.0
                yield event
            if run.closed_at is not None and seq >= len(run.events):
                return
            try:
                await asyncio.wait_for(waiting.wait(), wait)
            except TimeoutError:
                idle += wait
                if ping_seconds and idle >= ping_seconds:
                    idle = 0.0
                    yield None
