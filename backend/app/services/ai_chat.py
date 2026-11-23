"""AI 채팅 실행: 사용자 메시지를 받아 AI 답변을 스트리밍으로 만들고 채팅에 저장한다.

조원 파이프라인(P5 server.py의 run_task)을 우리 저장 구조에 맞게 옮긴 것이다. 도구(섹션 읽기, 웹 검색,
문서로 넘기기)는 services/ai_tools.py에 있다. 첨부 파일과 기록 검토안 도구는 없다.

문서 모드(mode="document", 자동 판별, 또는 AI가 route_document를 부른 경우)에서 끝까지 만든 답변은
메시지의 doc에 {title, content}로 남는다. 사용자가 "산출물로 저장"을 누르면 산출물이 된다.
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
from app.config import get_settings
from app.errors import AppError
from app.services import ai_context, ai_runs, ai_tools, chat, conversations, doc_sources, items
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
    "SEARCH_NOT_ALLOWED": "허용된 일반 개념 또는 공개 검색어 범위를 벗어나 검색을 중단했습니다.",
    "CREDIT_EXHAUSTED": "AI 크레딧이 부족합니다. 관리자에게 알려 주세요.",
    "RATE_LIMITED": "요청이 많아 잠시 막혔습니다. 1분쯤 뒤에 다시 시도해 주세요.",
    "ACCESS_DENIED": "이 키로 쓸 수 없는 모델이거나 접근이 막혔습니다. AI 설정을 확인해 주세요.",
}
DEFAULT_REASON = "요청을 완료하지 못했습니다. 입력과 연결 상태를 확인해 주세요."
DEFAULT_DOC_TITLE = "생성 문서"
HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$")
FENCE = re.compile(r"^\s{0,3}(```|~~~)")
INLINE_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
INLINE_MARKS = re.compile(r"[*_`~]")


def _search_turns(tail: list[dict]) -> list[dict]:
    """문서로 넘어갈 때 앞서 받은 웹 검색 결과는 남겨서, 같은 검색을 다시 하지 않게 한다."""
    calls = {
        item["call_id"] for item in tail
        if item.get("type") == "function_call" and item.get("name") in ai_tools.SEARCH_TOOL_NAMES
    }
    return [item for item in tail if item.get("call_id") in calls]


def _clean_title(raw: str) -> str:
    """제목에서 링크 주소와 강조 기호를 빼고 글만 남긴다."""
    text = INLINE_LINK.sub(lambda match: match.group(1), raw)
    return INLINE_MARKS.sub("", text).strip(" #\t")[:200]


def _doc_title(text: str) -> str:
    """답변의 첫 제목 줄(코드 블록 안은 빼고)을 문서 제목으로 쓴다. 없거나 비면 "생성 문서"."""
    in_code = False
    for line in text.splitlines():
        if FENCE.match(line):
            in_code = not in_code
            continue
        match = None if in_code else HEADING.match(line)
        title = _clean_title(match.group(1)) if match else ""
        if title:
            return title
    return DEFAULT_DOC_TITLE


class _Halt(Exception):
    """실행을 계속할 수 없는 상태(중지 요청, 프로젝트 비활성, 잠금 잃음)."""

    def __init__(self, state: RunCheck):
        self.state = state
        super().__init__(state.value)


async def start_run(
    db: AsyncDatabase, runtime: Runtime, user_id: ObjectId, project: dict, text: str,
    public_search_terms: str = "", mode: str = "auto", include_session: bool | None = None,
    asset_ids: list[str] | None = None, reference_document: str | None = None, reference_body: str | None = None,
    conversation_id: str | None = None,
) -> ObjectId:
    """프로젝트를 잠그고 사용자 메시지를 저장한 뒤 백그라운드 실행을 시작한다.

    public_search_terms는 이번 요청에서 웹 검색에 써도 되는 공개 검색어다(검색 허용이 켜져 있을 때만 쓴다).
    mode는 auto(대화, 문서 요청이면 문서), chat(대화만), document(문서). 문서 모드는 include_session일 때만
    이전 대화를 넣는다.
    """
    project_id = project["_id"]
    reference = await _reference(db, project_id, mode, reference_document, reference_body)
    conversation = await conversations.resolve(db, user_id, project_id, conversation_id)

    def make_job(run_id: ObjectId, message: dict) -> "_RunJob":
        return _RunJob(
            db, runtime, user_id, project_id, run_id, message["_id"], text, public_search_terms, mode,
            include_session, message["asset_ids"], reference, conversation,
        )

    return await launch(db, runtime, user_id, project_id, text, make_job, asset_ids=asset_ids, conversation_id=conversation)


async def launch(
    db: AsyncDatabase, runtime: Runtime, user_id: ObjectId, project_id: ObjectId, text: str,
    make_job, asset_ids: list[str] | None = None, extra: dict | None = None, conversation_id: ObjectId | None = None,
) -> ObjectId:
    """프로젝트를 잠그고 사용자 메시지를 남긴 뒤 make_job(run_id, message)이 만든 실행을 뒤에서 돌린다."""
    run_id = await ai_runs.begin_run(db, user_id, project_id)
    try:
        message_extra = {**(extra or {}), "request_id": run_id} if extra is not None else None
        message = await chat.add_message(
            db, user_id, project_id, "USER", text, asset_ids, extra=message_extra, conversation_id=conversation_id
        )
        await ai_runs.attach_user_message(db, run_id, message["_id"])
    except BaseException:
        await ai_runs.finish_run(db, run_id, project_id, "failed", "MESSAGE_SAVE_FAILED")
        raise
    runtime.tasks[str(run_id)] = asyncio.create_task(make_job(run_id, message).run())
    return run_id


async def _reference(
    db: AsyncDatabase, project_id: ObjectId, mode: str, key: str | None, body: str | None
) -> dict | None:
    """문서 모드에서 참고할 기존 산출물. body가 있으면 저장하지 않은 편집 중인 글을 대신 쓴다."""
    if key is None and body is None:
        return None
    if mode != "document":
        raise AppError(422, "REFERENCE_REQUIRES_DOCUMENT_MODE", "기존 산출물 참고는 문서 모드에서만 쓸 수 있습니다.")
    if key is None:
        raise AppError(422, "REFERENCE_REQUIRED", "참고할 산출물을 골라 주세요.")
    document = await items.get_item(db, project_id, "document", key)
    return {
        "kind": "reference_document", "id": key, "title": document["title"], "revision": document["revision"],
        "content": document["content"] if body is None else body, "source": "saved" if body is None else "editor",
    }


async def _unread_note(db: AsyncDatabase, asset_ids: list[ObjectId]) -> dict:
    """첨부 내용은 읽지 않는다(기획서 v2). 파일이 붙었다는 사실과 이름만 알려 필요한 내용을 붙여 달라고 하게 한다."""
    names = [asset.get("file_name") async for asset in db.assets.find({"_id": {"$in": asset_ids}}, {"file_name": 1})]
    return {
        "source": "system_note",
        "files": names,
        "content": "The user attached these files, but their contents were not read. If their content matters, "
                   "ask the user to paste the relevant text.",
    }


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

    async def web_search(self, query: str):
        await self.job.ensure_ok(force=True)
        await self.tracker.start("web_search")
        try:
            result = await self.provider.web_search(query)
        except BaseException as exc:
            await self.tracker.finish("web_search", getattr(exc, "usage", None), _not_dispatched(exc))
            if isinstance(exc, ProviderError) and exc.delivery == Delivery.UNKNOWN:
                raise ContextError(UNKNOWN) from exc
            raise
        await self.tracker.finish("web_search", getattr(result, "usage", None))
        return result


def _not_dispatched(exc: BaseException) -> bool:
    return isinstance(exc, ProviderError) and exc.delivery == Delivery.NOT_DISPATCHED


class _RunJob:
    def __init__(
        self, db, runtime: Runtime, user_id, project_id, run_id, user_message_id, text: str,
        public_search_terms: str = "", mode: str = "auto", include_session: bool | None = None,
        asset_ids: list[ObjectId] | None = None, reference: dict | None = None, conversation_id: ObjectId | None = None,
    ):
        self.db, self.runtime = db, runtime
        # 이 실행이 속한 채팅 대화. 항목별 AI·할 일 제안 실행은 None이다.
        self.conversation_id = conversation_id
        self.reference = reference
        self.sources: list[dict] = []
        self.asset_ids = list(asset_ids or [])
        # 이번 실행에서 AI에게 넘긴 참고 파일. 답변에 함께 남긴다.
        self.source_ids: list[ObjectId] = []
        self.user_id, self.project_id, self.run_id = user_id, project_id, run_id
        self.user_message_id, self.text = user_message_id, text
        self.public_search_terms = public_search_terms
        self.mode = mode
        # 정하지 않았으면 이전 대화를 넣는다(대화하다가 문서를 만드는 경우가 대부분이라서). false면 빼고 만든다.
        self.include_session = include_session if include_session is not None else True
        self.document_mode = False
        self.toolbox: ai_tools.ToolBox | None = None
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
        async def save_summary(text: str, covered_ids: list[str], sources: list[str]) -> None:
            self.capsule_revision = await ai_runs.save_capsule(
                self.db, self.project_id, self.run_id, self.capsule_revision, text, covered_ids, sources,
                self.conversation_id,
            )

        prefix = ai_context.prefix_from(sections) + self.toolbox.extra_prefix
        prepared = await prepare(
            self.provider, prefix, history, self.text, self.toolbox.tools,
            self.emit, self.runtime.model, self.budget, save_summary, self.document_mode, tail, self._observe_count,
        )
        return prepared, _apply_prepared_history(history, prepared)

    async def _generate(self) -> None:
        project = await self.db.projects.find_one({"_id": self.project_id})
        sections = await ai_context.build_sections(self.db, project, self.user_id)
        files = {}
        if get_settings().ai_read_attachments:
            files = await ai_context.file_sections(self.db, project, self.user_id, self.asset_ids)
        elif self.asset_ids:
            sections["attachments_not_read"] = await _unread_note(self.db, self.asset_ids)
        sections.update(files)
        if self.reference:
            sections["reference_document"] = self.reference
        self.sections = sections
        self.source_ids = [ObjectId(key.split(":", 1)[1]) for key in files]
        active = await ai_context.active_file_ids(self.db, project, self.user_id, self.source_ids)
        history, self.capsule_revision = await ai_context.load_history(
            self.db, self.project_id, self.user_message_id, active, self.conversation_id
        )
        await self.emit("context_ready", "프로젝트 자료와 이전 대화를 확인했습니다.")
        self.document_mode = self.mode == "document" or (
            self.mode == "auto" and ai_tools.is_document_request(self.text)
        )
        self.search_only = self.mode == "auto" and ai_tools.is_search_only_request(self.text)
        provider = self.runtime.provider
        supports = getattr(provider, "supports_web_search", hasattr(provider, "web_search"))
        can_search = bool(project.get("search_enabled")) and supports
        self.toolbox = ai_tools.ToolBox(
            self.emit, self.provider, sections, can_search, self.public_search_terms, self._search_allowed,
            route_offered=self.mode == "auto" and not self.document_mode and not self.search_only,
            record_offered=not self.document_mode,
        )
        if self.document_mode:
            history = self._document_history(history)
            await self.emit("document_route", "문서 작성 작업으로 전달했습니다.")
        self.budget, tail = Budget(), []
        prepared, history = await self._prepare(sections, history, tail)
        await self.emit("generating", "답변을 생성하고 있습니다.")
        for _ in range(MAX_TOOL_ROUNDS):
            tool_calls = await self._stream_once(prepared.wire)
            if not tool_calls:
                return
            if any(call.get("name") == "route_document" for call in tool_calls):
                history, tail = await self._route(tool_calls, history, tail)
            else:
                tail.extend(await self.toolbox.handle(tool_calls))
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

    def _document_history(self, history: list[dict]) -> list[dict]:
        """문서 모드는 이전 대화를 넣기로 한 경우에만 대화를 쓴다."""
        return history if self.include_session else []

    async def _route(self, calls: list[dict], history: list[dict], tail: list[dict]) -> tuple[list[dict], list[dict]]:
        """AI가 문서 작성으로 넘기면 지금까지의 답변을 버리고 문서 모드로 다시 만든다."""
        call = calls[0]
        if len(calls) != 1 or not call.get("call_id"):
            raise ContextError("TOOL_NOT_ALLOWED")
        arguments = call.get("arguments") or "{}"
        if self.search_only:
            # 검색만 요청했는데 모델이 문서로 넘기려 하면 거절하고 대화로 답하게 한다.
            return history, tail + [
                {"type": "function_call", "name": "route_document", "call_id": call["call_id"], "arguments": arguments},
                {"type": "function_call_output", "call_id": call["call_id"],
                 "output": json.dumps(ai_tools.ROUTE_REFUSED, ensure_ascii=False)},
            ]
        if "route_document" not in self.toolbox.names():
            raise ContextError("TOOL_NOT_ALLOWED")
        try:
            if json.loads(arguments) != {}:
                raise ValueError
        except (TypeError, ValueError):
            raise ContextError("INVALID_TOOL_ARGUMENTS") from None
        self.document_mode, self.pieces = True, []
        self.toolbox.remove({"route_document", "propose_record"})
        # 문서 답변에는 기록 검토안을 붙이지 않는다.
        self.toolbox.record_draft = None
        await self.emit("document_route", "문서 작성 작업으로 전달했습니다.")
        return self._document_history(history), _search_turns(tail)

    async def _search_allowed(self) -> bool:
        """검색 허용은 실행 중에도 바뀔 수 있어 검색할 때마다 프로젝트에서 다시 읽는다."""
        project = await self.db.projects.find_one({"_id": self.project_id}, {"search_enabled": 1})
        return bool((project or {}).get("search_enabled"))

    async def _save_answer(self, completion: str) -> bool:
        """답변을 저장할 수 있는 상태일 때만 저장한다. 중지 요청 중이면 부분 답변으로 저장한다."""
        state = await ai_runs.check(self.db, self.run_id, self.project_id)
        if state not in (RunCheck.OK, RunCheck.STOP_REQUESTED) or not "".join(self.pieces).strip():
            return False
        self.saving = True
        text = "".join(self.pieces)
        # 끝까지 만든 문서만 문서 초안으로 남긴다. 중간에 끊긴 문서는 대화 답변으로만 남는다.
        doc = None
        if self.document_mode and completion == "COMPLETE":
            web_urls = self.toolbox.web_urls if self.toolbox else []
            doc = {"title": _doc_title(text), "content": text, "sources": doc_sources.collect(self.sections, web_urls)}
        message = await chat.add_message(
            self.db, self.user_id, self.project_id, "ASSISTANT", text, completion=completion, doc=doc,
            source_ids=self.source_ids, record_draft=self.toolbox.record_draft if self.toolbox else None,
            extra=self._message_extra(text, completion), conversation_id=self.conversation_id,
        )
        self.assistant_message_id = message["_id"]
        await self._after_save(message)
        return True

    def _message_extra(self, text: str, completion: str) -> dict | None:
        """답변 메시지에 더 붙일 필드. 항목별 AI·할 일 제안 실행이 쓴다."""
        return None

    async def _after_save(self, message: dict) -> None:
        if message.get("doc"):
            await self.emit("document_candidate", str(message["_id"]))

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
