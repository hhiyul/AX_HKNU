import httpx
from fastapi import APIRouter, Depends

from app.deps import current_user
from app.schemas.openai_test import OpenAITestRequest, OpenAITestResult

router = APIRouter(prefix="/openai", tags=["openai"])

OPENAI_MODELS_URL = "https://api.openai.com/v1/models"
TIMEOUT_SECONDS = 10


def get_openai_transport() -> httpx.AsyncBaseTransport | None:
    return None


async def check_openai_key(api_key: str, transport: httpx.AsyncBaseTransport | None = None) -> OpenAITestResult:
    """모델 목록을 한 번 조회해 키를 확인한다. 생성 호출이 아니며 키는 저장하지 않는다."""
    try:
        async with httpx.AsyncClient(transport=transport, timeout=TIMEOUT_SECONDS) as client:
            response = await client.get(OPENAI_MODELS_URL, headers={"Authorization": f"Bearer {api_key}"})
        if response.status_code == 401:
            return OpenAITestResult(ok=False, reason="invalid_key")
        if response.status_code != 200:
            return OpenAITestResult(ok=False, reason="upstream_error")
        return OpenAITestResult(ok=True, model_count=len(response.json()["data"]))
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
