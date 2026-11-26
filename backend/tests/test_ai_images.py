"""채팅 사진을 AI 이미지 입력으로 보내기: 줄이기, 요청 모양, 토큰 어림, 설정, 장수 제한."""
import asyncio
import base64
import io

import pytest
from PIL import Image

from ai_fakes import FakeProvider, answer
from app.ai.context import IMAGE_POLICY, build_wire
from app.ai.events import RunEvents
from app.ai.provider import IMAGE_TOKENS, DemoProvider, OpenAIProvider, without_images
from app.ai.runtime import Runtime, set_runtime
from app.config import get_settings
from app.services import ai_images


def png_bytes(width: int, height: int, color=(30, 60, 200)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (width, height), color).save(out, format="PNG")
    return out.getvalue()


@pytest.fixture
def use_runtime():
    def install(provider) -> Runtime:
        runtime = Runtime(provider, RunEvents(), {}, "gpt-6-luna")
        set_runtime(runtime)
        return runtime

    yield install
    set_runtime(None)


@pytest.fixture
async def ctx(client, login):
    await login(client)
    project = (await client.post("/projects", json={"name": "칠보 팔찌"})).json()
    return {"client": client, "url": f"/projects/{project['id']}", "project_id": project["id"]}


async def upload(ctx, name, data, mime, asset_type="product_image"):
    res = await ctx["client"].post("/assets", data={"project_id": ctx["project_id"], "asset_type": asset_type},
                                   files={"file": (name, data, mime)})
    assert res.status_code == 201, res.text
    return res.json()["id"]


async def send(ctx, runtime, text, asset_ids):
    res = await ctx["client"].post(f"{ctx['url']}/runs", json={"text": text, "mode": "chat", "asset_ids": asset_ids})
    assert res.status_code == 202, res.text
    await asyncio.wait_for(runtime.tasks[res.json()["run_id"]], 5)


def question_of(provider: FakeProvider) -> object:
    return provider.calls[0]["input"][-1]["content"]


def test_build_wire_puts_photos_with_the_question():
    image = {"type": "input_image", "image_url": "data:image/png;base64,AAAA", "detail": "auto"}
    wire = build_wire([], [], "이 팔찌 색은?", [], "m", images=[image])
    assert wire["input"][-1]["content"] == [{"type": "input_text", "text": "이 팔찌 색은?"}, image]
    assert IMAGE_POLICY in wire["input"][0]["content"]
    plain = build_wire([], [], "질문", [], "m")
    assert plain["input"][-1]["content"] == "질문" and IMAGE_POLICY not in plain["input"][0]["content"]


async def test_token_estimate_counts_photos_not_their_data():
    big = "data:image/png;base64," + "A" * 2_000_000
    wire = build_wire([], [], "질문", [], "m", images=[{"type": "input_image", "image_url": big}])
    text, images = without_images(wire)
    assert images == 1 and "AAAA" not in str(text)
    gateway = OpenAIProvider("sk-test", "m", base_url="https://api.baze.co/v1/gateway")
    plain = await gateway.count(build_wire([], [], "질문", [], "m"))
    with_photo = await gateway.count(wire)
    # 사진 데이터 200만 글자(약 100만 토큰)가 아니라 장당 정해 둔 토큰과 사진 규칙 한 문단만 더해진다.
    assert IMAGE_TOKENS <= with_photo - plain < IMAGE_TOKENS + len(IMAGE_POLICY)
    assert await DemoProvider().count(wire) < 10_000


def test_large_photos_are_shrunk_and_small_ones_kept():
    small = png_bytes(400, 300)
    assert ai_images.shrink(small, "image/png") == (small, "image/png")
    data, mime = ai_images.shrink(png_bytes(4000, 1000), "image/png")
    with Image.open(io.BytesIO(data)) as image:
        assert max(image.size) == ai_images.MAX_SIDE and mime == "image/jpeg"
    broken = b"\x89PNG\r\n\x1a\nnot really"
    assert ai_images.shrink(broken, "image/png") == (broken, "image/png")


async def test_chat_photo_is_sent_to_the_ai(ctx, use_runtime):
    photo = await upload(ctx, "팔찌.png", png_bytes(3000, 2000), "image/png")
    note = await upload(ctx, "견적.txt", "은 체인 2,800원".encode(), "text/plain", asset_type="document")
    provider = FakeProvider([answer("파란 사진이에요")])
    await send(ctx, use_runtime(provider), "이 팔찌 색은?", [photo, note])

    question = question_of(provider)
    assert question[0] == {"type": "input_text", "text": "이 팔찌 색은?"}
    image = question[1]
    assert image["type"] == "input_image" and image["image_url"].startswith("data:image/jpeg;base64,")
    with Image.open(io.BytesIO(base64.b64decode(image["image_url"].split(",", 1)[1]))) as sent:
        assert max(sent.size) == ai_images.MAX_SIDE
    # GD-048: 명시한 글 파일은 기본으로 원문을 읽는다. 사진은 이미지로 읽었으므로 미독 목록에서 뺀다.
    prefix = provider.calls[0]["input"][1]["content"]
    assert '은 체인 2,800원' in prefix and "팔찌.png" not in prefix and 'attachments_not_read' not in prefix


async def test_photos_can_be_turned_off(ctx, use_runtime, monkeypatch):
    monkeypatch.setattr(get_settings(), "ai_read_images", False)
    photo = await upload(ctx, "팔찌.png", png_bytes(200, 200), "image/png")
    provider = FakeProvider([answer("답")])
    await send(ctx, use_runtime(provider), "이 팔찌 색은?", [photo])
    assert question_of(provider) == "이 팔찌 색은?"
    assert '"files": ["팔찌.png"]' in provider.calls[0]["input"][1]["content"]


async def test_at_most_four_photos_per_message(ctx, use_runtime):
    photos = [await upload(ctx, f"사진{n}.png", png_bytes(50, 50, (n * 40, 0, 0)), "image/png") for n in range(5)]
    provider = FakeProvider([answer("답")])
    await send(ctx, use_runtime(provider), "다 봐 줘", photos)
    question = question_of(provider)
    assert sum(1 for part in question if part.get("type") == "input_image") == ai_images.MAX_IMAGES
    assert '"files": ["사진4.png"]' in provider.calls[0]["input"][1]["content"]


async def test_only_the_current_message_photos_are_sent(ctx, use_runtime):
    photo = await upload(ctx, "팔찌.png", png_bytes(100, 100), "image/png")
    provider = FakeProvider([answer("첫 답"), answer("둘째 답")])
    runtime = use_runtime(provider)
    await send(ctx, runtime, "이 팔찌 색은?", [photo])
    await send(ctx, runtime, "고마워", [])
    later = provider.calls[1]["input"]
    assert later[-1]["content"] == "고마워"
    assert not any(isinstance(item["content"], list) for item in later)
