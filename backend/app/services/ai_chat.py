"""AI 채팅 실행: 사용자 메시지를 받아 AI 답변을 스트리밍으로 만들고 채팅에 저장한다.

조원 파이프라인(P5 server.py의 run_task)을 우리 저장 구조에 맞게 옮긴 것이다. 1단계라 웹 검색,
문서 초안, 첨부, 기록 검토안 도구는 없고 섹션 읽기 도구 하나만 쓴다.
"""
import asyncio
import contextlib
import json
import logging
import re
import time

from bson import ObjectId
from pymongo.asynchronous.database import AsyncDatabase

from app.ai.context import (
    MODEL_WINDOW,
    OUTPUT_RESERVE,
    SAFETY_RESERVE,
    THRESHOLD,
    Budget,
    ContextError,
    history_summary_text,
    prepare,
)
from app.ai.provider import OpenAIProvider
from app.ai.responses import Delivery, ProviderError
from app.ai.runtime import Runtime
from app.ai.usage import UsageTracker, valid_usage
from app.services import ai_context, ai_runs, chat
from app.services.ai_runs import RunCheck

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 6
CHECK_INTERVAL_SECONDS = 1.0
UNKNOWN = "PROVIDER_OUTCOME_UNKNOWN"
ERROR_CODE = re.compile(r"[A-Z0-9_]{1,80}")
COMPRESSION_ERRORS = {"COMPRESSION_FAILED", "EMPTY_SUMMARY", "INSUFFICIENT_COMPRESSION", "CAPSULE_CONFLICT"}
REASONS = {
    "MODEL_LIMIT_EXCEEDED": "모델의 실제 입력 한도를 넘어 요청을 중단했습니다. 자료를 줄여 주세요.",
    "KEY_MISSING_OR_INVALID": "OpenAI 연결 설정을 확인해 주세요.",
    "CAPSULE_CONFLICT": "컨텍스트 저장이 충돌하여 요청을 중단했습니다.",
    "PROJECT_NOT_ACTIVE": "프로젝트가 보관함이나 휴지통으로 옮겨져 답변을 저장하지 않았습니다.",
}
DEFAULT_REASON = "요청을 완료하지 못했습니다. 입력과 연결 상태를 확인해 주세요."


class _Halt(Exception):
    """실행을 계속할 수 없는 상태(중지 요청, 프로젝트 비활성, 잠금 잃음)."""

    def __init__(self, state: RunCheck):
        self.state = state
        super().__init__(state.value)


async def start_run(db: AsyncDatabase, runtime: Runtime, user_id: ObjectId, project: dict, text: str) -> ObjectId:
    """프로젝트를 잠그고 사용자 메시지를 저장한 뒤 백그라운드 실행을 시작한다."""
    project_id = project["_id"]
    run_id = await ai_runs.begin_run(db, user_id, project_id)
    try:
        message = await chat.add_message(db, user_id, project_id, "USER", text)
        await ai_runs.attach_user_message(db, run_id, message["_id"])
    except BaseException:
        await ai_runs.finish_run(db, run_id, project_id, "failed", "MESSAGE_SAVE_FAILED")
        raise
    job = _RunJob(db, runtime, user_id, project_id, run_id, message["_id"], text)
    runtime.tasks[str(run_id)] = asyncio.create_task(job.run())
    return run_id


async def run_task(
    db: AsyncDatabase, runtime: Runtime, user_id: ObjectId, project_id: ObjectId,
    run_id: ObjectId, user_message_id: ObjectId, text: str,
) -> None:
    await _RunJob(db, runtime, user_id, project_id, run_id, user_message_id, text).run()


async def stop_run(db: AsyncDatabase, runtime: Runtime, user_id: ObjectId, project_id: ObjectId, run_id: str) -> str:
    """이 프로세스에서 도는 실행이면 바로 취소하고, 아니면 DB에 중지 요청을 남긴다."""
    run = await ai_runs.get_run(db, user_id, project_id, run_id)
    if run["status"] != "running":
        return run["status"]
    task = runtime.tasks.get(str(run["_id"]))
    if task is not None:
        # 두 번째 취소가 정리 중인 실행에 닿으면 잠금 해제가 끊길 수 있어 한 번만 보낸다.
        if not task.cancelling():
            task.cancel()
    else:
        await ai_runs.request_stop(db, run["_id"])
    return "stop_requested"


def _apply_prepared_history(existing: list[dict], prepared) -> list[dict]:
    if not prepared.summary:
        return existing
    covered = set(prepared.covered_ids)
    capsule = {
        "id": "capsule",
        "role": "user",
        "content": history_summary_text(prepared.summary),
        "covered_ids": prepared.covered_ids,
    }
    return [capsule] + [m for m in existing if not set(m.get("covered_ids", [m["id"]])) <= covered]


def _error_details(exc: BaseException) -> tuple[str, str, str | None]:
    """(사건용 코드, 저장용 error_code, delivery). 원인 사슬에서 ProviderError를 찾는다."""
    code = exc.code if isinstance(exc, ProviderError) else str(exc)
    cause: BaseException | None = exc
    while cause is not None and not isinstance(cause, ProviderError):
        cause = cause.__cause__
    raw = cause.code if isinstance(cause, ProviderError) else code
    error_code = raw if ERROR_CODE.fullmatch(raw) else "INTERNAL_ERROR"
    if isinstance(cause, ProviderError):
        delivery = cause.delivery.value
    else:
        delivery = Delivery.UNKNOWN.value if code == UNKNOWN else None
    return code, error_code, delivery


class _TrackedProvider:
    """호출마다 사용량을 세고, 원격 결과를 모르는 실패를 PROVIDER_OUTCOME_UNKNOWN으로 올린다."""

    def __init__(self, provider, tracker: UsageTracker, job: "_RunJob"):
        self.provider = provider
        self.tracker = tracker
        self.job = job

    async def count(self, wire: dict) -> int:
        await self.job.ensure_ok(force=True)
        return await self.provider.count(wire)

    async def summarize(self, messages: list[dict]):
        await self.job.ensure_ok(force=True)
        await self.tracker.start("compression")
        try:
            result = await self.provider.summarize(messages)
        except BaseException as exc:
            await self.tracker.finish("compression", getattr(exc, "usage", None), _not_dispatched(exc))
            raise
        await self.tracker.finish("compression", getattr(result, "usage", None))
        return result

    async def stream(self, wire: dict):
        await self.job.ensure_ok(force=True)
        await self.tracker.start("generation")
        usage, not_dispatched = None, False
        try:
            async for event in self.provider.stream(wire):
                if event.get("type") == "usage" and usage is None:
                    usage = valid_usage(event)
                yield event
        except ProviderError as exc:
            not_dispatched = _not_dispatched(exc)
            if exc.delivery == Delivery.UNKNOWN:
                raise ContextError(UNKNOWN) from exc
            raise
        finally:
            await self.tracker.finish("generation", usage, not_dispatched)


def _not_dispatched(exc: BaseException) -> bool:
    return isinstance(exc, ProviderError) and exc.delivery == Delivery.NOT_DISPATCHED


class _RunJob:
    def __init__(self, db, runtime: Runtime, user_id, project_id, run_id, user_message_id, text: str):
        self.db, self.runtime = db, runtime
        self.user_id, self.project_id, self.run_id = user_id, project_id, run_id
        self.user_message_id, self.text = user_message_id, text
        self.key = str(run_id)
        self.pieces: list[str] = []
        self.status, self.error_code, self.delivery = "failed", None, None
        self.assistant_message_id = None
        # 답변 저장을 시작했으면 취소가 와도 부분 답변을 다시 저장하지 않는다.
        self.saving = False
        self.terminal: tuple[str, str] | None = None
        self.last_check = 0.0
        measurement = "openai" if isinstance(runtime.provider, OpenAIProvider) else "synthetic"
        self.tracker = UsageTracker(self.emit, measurement)
        self.provider = _TrackedProvider(runtime.provider, self.tracker, self)

    async def emit(self, kind: str, content: str) -> None:
        self.runtime.events.publish(self.key, kind, content)

    async def ensure_ok(self, force: bool = False) -> None:
        """실행을 계속해도 되는지 DB로 확인한다. 스트리밍 중에는 1초에 한 번까지만 확인한다."""
        now = time.monotonic()
        if not force and now - self.last_check < CHECK_INTERVAL_SECONDS:
            return
        self.last_check = now
        state = await ai_runs.check(self.db, self.run_id, self.project_id)
        if state != RunCheck.OK:
            raise _Halt(state)

    async def run(self) -> None:
        beat = asyncio.create_task(self._heartbeat())
        try:
            await self.emit("started", "요청을 접수했습니다.")
            await self._generate()
            await self._finish_answer()
        except asyncio.CancelledError:
            await self._stopped()
        except _Halt as halt:
            await self._halted(halt.state)
        except (ContextError, ProviderError) as exc:
            await self._failed(exc)
        except Exception:
            logger.exception("AI run %s failed", self.key)
            self.error_code = "INTERNAL_ERROR"
            await self._save_partial("PARTIAL")
            self.terminal = ("failed", "예상하지 못한 오류로 요청을 완료하지 못했습니다.")
        finally:
            beat.cancel()
            await self._close()

    async def _heartbeat(self) -> None:
        while True:
            await asyncio.sleep(ai_runs.HEARTBEAT_SECONDS)
            try:
                await ai_runs.heartbeat(self.db, self.run_id)
            except Exception:
                # 한 번 실패해도 다음 갱신을 계속한다. 60초 넘게 멈추면 죽은 실행으로 정리된다.
                logger.warning("AI run %s heartbeat failed", self.key, exc_info=True)

    async def _observe_count(self, count: int) -> None:
        await self.emit("context_usage", json.dumps({
            "input_tokens": count, "model_window": MODEL_WINDOW, "output_reserve": OUTPUT_RESERVE,
            "safety_reserve": SAFETY_RESERVE, "compression_threshold": THRESHOLD,
            "measurement": self.tracker.measurement,
        }))

    async def _prepare(self, sections: dict, history: list[dict], tail: list[dict]):
        async def save_summary(text: str, covered_ids: list[str], _sources: list[str]) -> None:
            self.capsule_revision = await ai_runs.save_capsule(
                self.db, self.project_id, self.run_id, self.capsule_revision, text, covered_ids
            )

        prepared = await prepare(
            self.provider, ai_context.prefix_from(sections), history, self.text, [ai_context.READ_TOOL],
            self.emit, self.runtime.model, self.budget, save_summary, False, tail, self._observe_count,
        )
        return prepared, _apply_prepared_history(history, prepared)

    async def _generate(self) -> None:
        project = await self.db.projects.find_one({"_id": self.project_id})
        sections = await ai_context.build_sections(self.db, project, self.user_id)
        history, self.capsule_revision = await ai_context.load_history(self.db, self.project_id, self.user_message_id)
        await self.emit("context_ready", "프로젝트 자료와 이전 대화를 확인했습니다.")
        self.budget, tail = Budget(), []
        prepared, history = await self._prepare(sections, history, tail)
        await self.emit("generating", "답변을 생성하고 있습니다.")
        for _ in range(MAX_TOOL_ROUNDS):
            tool_calls = await self._stream_once(prepared.wire)
            if not tool_calls:
                return
            tail.extend(await self._tool_outputs(tool_calls, sections))
            prepared, history = await self._prepare(sections, history, tail)
        raise ContextError("TOOL_BUDGET_EXHAUSTED")

    async def _stream_once(self, wire: dict) -> list[dict]:
        tool_calls, completed = [], False
        # 중간에 멈추면 Provider 스트림(OpenAI 연결과 사용량 집계)을 바로 닫는다.
        async with contextlib.aclosing(self.provider.stream(wire)) as stream:
            async for event in stream:
                await self.ensure_ok()
                if event["type"] == "delta":
                    self.pieces.append(event["text"])
                    await self.emit("delta", event["text"])
                elif event["type"] == "tool":
                    tool_calls.append(event)
                elif event["type"] == "usage":
                    usage = {key: event[key] for key in ("input_tokens", "output_tokens", "total_tokens")}
                    await self.emit("generation_usage", json.dumps(usage))
                elif event["type"] == "completed":
                    completed = True
        if not completed:
            raise ContextError(UNKNOWN)
        return tool_calls

    async def _tool_outputs(self, tool_calls: list[dict], sections: dict) -> list[dict]:
        tail = []
        for call in tool_calls:
            if call.get("name") != "read_project_sections" or not call.get("call_id"):
                raise ContextError("TOOL_NOT_ALLOWED")
            try:
                args = json.loads(call.get("arguments") or "{}")
            except (TypeError, ValueError):
                raise ContextError("INVALID_TOOL_ARGUMENTS") from None
            keys = args.get("keys") if isinstance(args, dict) else None
            if not isinstance(keys, list) or any(not isinstance(key, str) for key in keys):
                raise ContextError("INVALID_TOOL_ARGUMENTS")
            await self.emit("tool_started", "프로젝트 정보를 조회하고 있습니다.")
            result = ai_context.read_sections(sections, keys)
            await self.emit("tool_finished", "프로젝트 정보 조회를 마쳤습니다.")
            tail.extend([
                {"type": "function_call", "name": call["name"], "call_id": call["call_id"],
                 "arguments": call.get("arguments") or "{}"},
                {"type": "function_call_output", "call_id": call["call_id"],
                 "output": json.dumps(result, ensure_ascii=False, default=str)},
            ])
        return tail

    async def _save_answer(self, completion: str) -> bool:
        """답변을 저장할 수 있는 상태일 때만 저장한다. 중지 요청 중이면 부분 답변으로 저장한다."""
        state = await ai_runs.check(self.db, self.run_id, self.project_id)
        if state not in (RunCheck.OK, RunCheck.STOP_REQUESTED) or not "".join(self.pieces).strip():
            return False
        self.saving = True
        message = await chat.add_message(
            self.db, self.user_id, self.project_id, "ASSISTANT", "".join(self.pieces), completion=completion
        )
        self.assistant_message_id = message["_id"]
        return True

    async def _save_partial(self, completion: str) -> None:
        if self.pieces and not self.saving:
            await self._save_answer(completion)

    async def _finish_answer(self) -> None:
        state = await ai_runs.check(self.db, self.run_id, self.project_id)
        if state != RunCheck.OK:
            raise _Halt(state)
        if not "".join(self.pieces).strip():
            raise ContextError("EMPTY_OUTPUT")
        await self._save_answer("COMPLETE")
        self.status, self.terminal = "completed", ("completed", "완료했습니다.")

    async def _stopped(self) -> None:
        await self._save_partial("PARTIAL")
        self.status = "stopped"
        self.terminal = ("stopped", "중단했습니다. 원격 처리의 취소 여부는 확인되지 않았습니다.")

    async def _halted(self, state: RunCheck) -> None:
        if state == RunCheck.STOP_REQUESTED:
            await self._stopped()
        elif state == RunCheck.PROJECT_NOT_ACTIVE:
            self.error_code = "PROJECT_NOT_ACTIVE"
            self.terminal = ("failed", REASONS["PROJECT_NOT_ACTIVE"])
        else:
            self.status = "lost"

    async def _failed(self, exc: BaseException) -> None:
        code, self.error_code, self.delivery = _error_details(exc)
        if code == UNKNOWN:
            await self._save_partial("UNKNOWN")
            self.status = "unknown"
            self.terminal = ("outcome_unknown", "원격 처리 결과는 확인되지 않았습니다. 대화는 계속 사용할 수 있습니다.")
            return
        await self._save_partial("PARTIAL")
        if code in COMPRESSION_ERRORS:
            await self.emit("compression_failed", "컨텍스트 압축을 완료하지 못했습니다. 기존 대화는 유지됩니다.")
        self.terminal = ("failed", REASONS.get(code, DEFAULT_REASON))

    async def _close(self) -> None:
        """실행 기록을 끝내고 잠금을 푼 뒤 마지막 사건을 보낸다. 잠금을 잃은 실행은 기록을 건드리지 않는다."""
        try:
            if self.status != "lost":
                # 취소가 와도 실행 기록과 잠금 해제는 끝까지 한다.
                await asyncio.shield(ai_runs.finish_run(
                    self.db, self.run_id, self.project_id, self.status, self.error_code, self.delivery,
                    self.tracker.parts, self.assistant_message_id,
                ))
            if self.terminal:
                await self.emit(*self.terminal)
        finally:
            self.runtime.events.close(self.key)
            self.runtime.tasks.pop(self.key, None)
