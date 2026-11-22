"""AI 실행 테스트용 가짜 Provider. 실제 네트워크를 쓰지 않는다."""
import asyncio
import json
from collections.abc import Callable

from app.ai.responses import ProviderError

COMPLETED = {"type": "completed"}


def delta(text: str) -> dict:
    return {"type": "delta", "text": text}


def answer(*texts: str) -> list[dict]:
    return [*(delta(text) for text in texts), COMPLETED]


class FakeProvider:
    """stream을 부를 때마다 turns의 다음 사건 목록을 낸다. gate가 있으면 첫 delta 뒤에 gate가 열릴 때까지 멈춘다."""

    def __init__(
        self,
        turns: list[list[dict]],
        count: int | Callable[[dict], int] = 100,
        summary: str = "요약",
        gate: asyncio.Event | None = None,
        error: ProviderError | None = None,
        search_results: list | None = None,
        search_gate: asyncio.Event | None = None,
    ):
        self.turns = list(turns)
        self._count = count
        self.summary = summary
        self.gate = gate
        self.error = error
        self.calls: list[dict] = []
        self.paused = asyncio.Event()
        # web_search가 차례로 돌려줄 결과. ProviderError면 그 오류를 낸다.
        self.search_results = list(search_results or [])
        self.search_gate = search_gate
        self.searching = asyncio.Event()
        self.queries: list[str] = []

    async def count(self, wire: dict) -> int:
        return self._count(wire) if callable(self._count) else self._count

    async def summarize(self, messages: list[dict]) -> str:
        return self.summary

    async def stream(self, wire: dict):
        self.calls.append(wire)
        if self.error is not None:
            raise self.error
        turn = self.turns.pop(0) if self.turns else [COMPLETED]
        for event in turn:
            yield event
            if self.gate is not None and event["type"] == "delta" and not self.paused.is_set():
                self.paused.set()
                await self.gate.wait()

    async def web_search(self, query: str) -> dict:
        self.queries.append(query)
        self.searching.set()
        if self.search_gate is not None:
            await self.search_gate.wait()
        result = self.search_results.pop(0) if self.search_results else {"text": "검색 결과: " + query, "urls": []}
        if isinstance(result, ProviderError):
            raise result
        return result


def tool_call(name: str, arguments: dict, call_id: str = "c1") -> dict:
    return {"type": "tool", "name": name, "call_id": call_id, "arguments": json.dumps(arguments, ensure_ascii=False)}
