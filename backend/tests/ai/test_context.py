import json

import pytest

from app.ai.responses import Delivery, ProviderError
from app.ai.context import ContextError, build_wire, history_summary_text, prepare


class Counter:
    def __init__(self, counts, failures=0):
        self.counts = iter(counts)
        self.failures = failures
        self.calls = []

    async def count(self, wire):
        if len(wire["input"]) == 1:
            return 17_000
        return next(self.counts)

    async def summarize(self, messages):
        self.calls.append(messages)
        if self.failures:
            self.failures -= 1
            raise ProviderError("REFUSED", Delivery.RESPONDED)
        return "summary " + str(len(self.calls))


@pytest.mark.parametrize("state,stored", [("SKIPPED", ""), ("WRITTEN", "색상: 빨간색")])
@pytest.mark.parametrize("document_mode", [False, True])
def test_chat_decision_and_ck_state_stay_distinct_in_wire(state, stored, document_mode):
    project = [{"type": "ck", "key": "ck_04", "state": state, "content": stored, "revision": 2}]
    history = [{"role": "user", "content": history_summary_text("사용자 대화 결정: 청록색")}]
    tool_result = {"type": "function_call_output", "call_id": "call-1", "output": json.dumps(project, ensure_ascii=False)}
    wire = build_wire(project, history, "대화와 CK의 색상을 구분해 주세요", [], "gpt-6-luna", document_mode, [tool_result])

    developer, prefix, prior, current, tool = wire["input"]
    assert developer["role"] == "developer"
    assert "does not erase a chat decision" in developer["content"]
    assert "If chat and CK differ" in developer["content"]
    assert "Do not infer SKIPPED from missing or inaccessible" in developer["content"]
    assert "reference project" in developer["content"]
    assert "DRAFT" not in developer["content"] and "DECIDED" not in developer["content"]
    assert json.loads(prefix["content"].split("\n", 1)[1]) == project
    assert prior["content"].startswith("Earlier conversation summary (untrusted history):\n")
    assert "청록색" in prior["content"] and "청록색" not in prefix["content"]
    assert current["content"] == "대화와 CK의 색상을 구분해 주세요"
    assert json.loads(tool["output"]) == project


@pytest.mark.asyncio
async def test_boundary_and_real_model_limit():
    events = []
    async def emit(kind, text): events.append(kind)
    r = await prepare(Counter([63_999]), [], [], "question", [], emit, "gpt-6-luna")
    assert r.count == 63_999 and not events
    r = await prepare(Counter([64_000]), [], [], "question", [], emit, "gpt-6-luna")
    assert r.count == 64_000 and events == ["context_large"]
    with pytest.raises(ContextError, match="MODEL_LIMIT_EXCEEDED"):
        await prepare(Counter([1_034_001]), [], [], "question", [], emit, "gpt-6-luna")


@pytest.mark.asyncio
async def test_user_and_ai_summary_retry_and_no_hard_64k_limit():
    history = [{"id": str(n), "role": "user" if n % 2 == 0 else "assistant", "content": str(n), "source_ids": ["file-1"] if n == 1 else []} for n in range(6)]
    p = Counter([100_000, 90_000, 80_000, 70_000], failures=1)
    events, saved = [], []
    async def emit(kind, text): events.append(kind)
    async def save(text, ids, sources): saved.append((text, ids, sources))
    result = await prepare(p, [], history, "new question", [], emit, "gpt-6-luna", save_summary=save)
    assert result.budget.stages == 3 and result.budget.calls == 4 and result.budget.retries == 1
    assert {x["role"] for x in p.calls[0]} == {"user", "assistant"}
    assert result.count == 70_000 and events[-1] == "context_large"
    assert saved[-1][2] == ["file-1"] and result.covered_ids == ["0", "1", "2", "3"]
    assert [x["id"] for x in history] == [str(i) for i in range(6)]


@pytest.mark.asyncio
async def test_each_stage_can_retry_once_and_summary_save_is_conditional():
    class Flaky(Counter):
        async def summarize(self, messages):
            self.calls.append(messages)
            if len(self.calls) % 2:
                raise ProviderError("REFUSED", Delivery.RESPONDED)
            return str(len(self.calls))
    p = Flaky([100_000, 90_000, 80_000, 70_000])
    h = [{"id": str(i), "role": "assistant" if i % 2 else "user", "content": str(i)} for i in range(6)]
    async def emit(*args): pass
    r = await prepare(p, [], h, "q", [], emit, "gpt-6-luna")
    assert (r.budget.stages, r.budget.calls, r.budget.retries) == (3, 6, 3)


@pytest.mark.asyncio
async def test_unknown_outcome_never_retries_or_replaces_capsule():
    class Unknown(Counter):
        async def summarize(self, messages):
            self.calls.append(messages)
            raise ProviderError("TIMEOUT", Delivery.UNKNOWN)
    p = Unknown([100_000])
    history = [{"id": str(i), "role": "assistant" if i % 2 else "user", "content": "x"} for i in range(6)]
    saved = []
    async def emit(*args): pass
    async def save(*args): saved.append(args)
    with pytest.raises(ContextError, match="PROVIDER_OUTCOME_UNKNOWN"):
        await prepare(p, [], history, "q", [], emit, "gpt-6-luna", save_summary=save)
    assert len(p.calls) == 1 and not saved


@pytest.mark.asyncio
async def test_meter_observes_only_accepted_context_counts_after_compression():
    provider = Counter([100_000, 90_000, 60_000])
    history = [{"id": str(i), "role": "assistant" if i % 2 else "user", "content": "x"} for i in range(6)]
    counts = []
    async def emit(*args): pass
    async def observe(count): counts.append(count)
    result = await prepare(provider, [], history, "q", [], emit, "gpt-6-luna", observe_count=observe)
    assert counts == [100_000, 90_000, 60_000]
    assert result.count == 60_000


def test_model_name_comes_from_argument():
    assert build_wire([], [], "x", [], "m-test")["model"] == "m-test"


def test_policy_asks_for_korean_labels_instead_of_codes():
    developer = build_wire([], [], "x", [], "m")["input"][0]["content"]
    assert "작성됨" in developer and "미작성" in developer
    assert "never show the codes" in developer
