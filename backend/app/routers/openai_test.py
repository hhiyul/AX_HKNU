import httpx
from fastapi import APIRouter, Depends

from app.config import get_settings
from app.deps import current_user
from app.schemas.openai_test import OpenAITestRequest, OpenAITestResult

router = APIRouter(prefix="/openai", tags=["openai"])

TIMEOUT_SECONDS = 10


def get_openai_transport() -> httpx.AsyncBaseTransport | None:
    return None


def models_url(base_url: str) -> str:
    """OpenAI는 /models, 게이트웨이(BAZE)는 문서대로 끝에 /를 붙인 /models/."""
    base = base_url.rstrip("/")
    return base + ("/models" if "api.openai.com" in base else "/models/")


async def check_openai_key(
    api_key: str, transport: httpx.AsyncBaseTransport | None = None, base_url: str | None = None
) -> OpenAITestResult:
    """모델 목록을 한 번 조회해 키를 확인한다. 생성 호출이 아니며 키는 저장하지 않는다."""
    url = models_url(base_url or get_settings().openai_base_url)
    try:
        async with httpx.AsyncClient(transport=transport, timeout=TIMEOUT_SECONDS) as client:
            response = await client.get(url, headers={"Authorization": f"Bearer {api_key}"})
        if response.status_code == 401:
            return OpenAITestResult(ok=False, reason="invalid_key")
        if response.status_code != 200:
            return OpenAITestResult(ok=False, reason="upstream_error")
        data = response.json()
        models = data["data"] if isinstance(data, dict) else data
        return OpenAITestResult(ok=True, model_count=len(models))
    except httpx.TimeoutException:
        return OpenAITestResult(ok=False, reason="timeout")
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        return OpenAITestResult(ok=False, reason="upstream_error")


@router.post("/test", response_model=OpenAITestResult)
async def test_openai_key(
    body: OpenAITestRequest,
    _: dict = Depends(current_user),
    transport: httpx.AsyncBaseTransport | None = Depends(get_openai_transport),
):
    return await check_openai_key(body.api_key.get_secret_value(), transport)
