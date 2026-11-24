"""로그인 실패 횟수 제한(비밀번호 무차별 대입 막기). 서버 메모리에 두며, 서버를 다시 띄우면 비워진다.

- 같은 이메일로 15분에 10번, 같은 주소(IP)에서 15분에 30번 틀리면 잠시 막는다.
- 실패만 센다. 로그인에 성공하면 그 이메일의 실패 기록은 지운다(한 PC에서 여러 사람이 써도 막히지 않게).
- IP는 nginx가 X-Forwarded-For 맨 끝에 붙인 값을 쓴다. 백엔드 포트는 서버 안에서만 열려 있어 nginx만 거친다.
"""
import math
import time
from collections import deque

from fastapi import Request

from app.errors import AppError

WINDOW_SECONDS = 15 * 60
EMAIL_FAILURES = 10
IP_FAILURES = 30


class FailureWindow:
    def __init__(self, limit: int, seconds: float = WINDOW_SECONDS, clock=time.monotonic):
        self.limit, self.seconds, self.clock = limit, seconds, clock
        self.hits: dict[str, deque] = {}

    def _recent(self, key: str) -> deque:
        hits = self.hits.get(key)
        if hits is None:
            return deque()
        cutoff = self.clock() - self.seconds
        while hits and hits[0] <= cutoff:
            hits.popleft()
        if not hits:
            self.hits.pop(key, None)
        return hits

    def retry_after(self, key: str) -> int:
        """막혀 있으면 다시 시도할 수 있을 때까지 초, 아니면 0."""
        hits = self._recent(key)
        if len(hits) < self.limit:
            return 0
        return max(1, math.ceil(hits[0] + self.seconds - self.clock()))

    def fail(self, key: str) -> None:
        self._recent(key)
        self.hits.setdefault(key, deque()).append(self.clock())

    def clear(self, key: str) -> None:
        self.hits.pop(key, None)


emails = FailureWindow(EMAIL_FAILURES)
addresses = FailureWindow(IP_FAILURES)


def client_address(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded.strip():
        return forwarded.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"


def check_login(email: str, address: str) -> None:
    wait = max(emails.retry_after(email.lower()), addresses.retry_after(address))
    if wait:
        minutes = math.ceil(wait / 60)
        raise AppError(429, "LOGIN_RATE_LIMITED", f"로그인 시도가 많아 잠시 막았습니다. {minutes}분 뒤에 다시 시도해 주세요.")


def login_failed(email: str, address: str) -> None:
    emails.fail(email.lower())
    addresses.fail(address)


def login_succeeded(email: str) -> None:
    emails.clear(email.lower())
