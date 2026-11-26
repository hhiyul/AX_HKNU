"""Local synthetic UI integration only: isolated test-mongo + deterministic provider.

Run through docker-compose.test.yml, on a separate loopback port. Never real UAT.
"""
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

if urlparse(os.environ.get("MONGO_DETAILS", "")).hostname != "test-mongo":
    raise SystemExit("UI smoke server requires the isolated test-mongo host")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai_fakes import COMPLETED, FakeProvider, answer, tool_call
from app.ai.runtime import Runtime, set_runtime
from app.ai.events import RunEvents
from main import app

set_runtime(Runtime(FakeProvider([
    [tool_call("suggest_tasks", {"tasks": [{"title": "포장 견적 확인", "date": None, "note": "테스트 후보"},
                                          {"title": "제품 사진 촬영", "date": "2026-12-03", "note": ""}]}), COMPLETED],
    answer("할 일 두 건을 제안했습니다. 내용과 날짜를 확인한 뒤 등록해 주세요."),
    answer("작품 소개 후보입니다. 확인 필요: 포장 비용."),
    answer("포장 비용은 아직 확인이 필요합니다. 저장된 내용과 실제 견적을 비교해 주세요."),
]), RunEvents(), {}, "synthetic-ui-only"))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
