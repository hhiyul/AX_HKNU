"""프로세스 하나에 하나 있는 AI 실행 환경: Provider, 사건 대기열, 이 프로세스에서 도는 실행."""
import asyncio
from dataclasses import dataclass, field

from app.ai.events import RunEvents
from app.ai.provider import DemoProvider, OpenAIProvider, make_provider
from app.config import get_settings


@dataclass
class Runtime:
    provider: OpenAIProvider | DemoProvider
    events: RunEvents = field(default_factory=RunEvents)
    # 실행 id(문자열) → 이 프로세스에서 도는 실행 task
    tasks: dict[str, asyncio.Task] = field(default_factory=dict)
    model: str = "gpt-6-luna"


_runtime: Runtime | None = None


def get_runtime() -> Runtime:
    global _runtime
    if _runtime is None:
        settings = get_settings()
        _runtime = Runtime(make_provider(settings), model=settings.openai_model)
    return _runtime


def set_runtime(runtime: Runtime | None) -> None:
    """테스트에서 가짜 Provider를 끼울 때 쓴다. None이면 다음 get_runtime이 설정으로 다시 만든다."""
    global _runtime
    _runtime = runtime
