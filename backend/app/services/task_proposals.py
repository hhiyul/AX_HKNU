"""AI 할 일 후보(기획서 v2 D절 "할 일 제안에서 기존 일정으로").

AI에게 suggest_tasks 도구만 주고 그 호출을 받는다. 후보는 답변 메시지의 proposals에만 남고 일정이 되지 않는다.
날짜를 확정할 정보가 없으면 null이다. 사용자가 날짜를 골라 POST /tasks로 등록한다.
"""
import datetime as dt
import json

from bson import ObjectId
from pymongo.asynchronous.database import AsyncDatabase

from app.ai.context import Budget, ContextError, prepare
from app.ai.runtime import Runtime
from app.services import ai_chat, doc_assist, home

PURPOSE = "task_proposal"
MAX_PROPOSALS = 5
SUGGEST_TOOL = {
    "type": "function",
    "name": "suggest_tasks",
    "description": (
        "Suggest up to five concrete follow-up tasks for this document section. Use date only when the section or "
        "references state it; otherwise null. Never invent deadlines."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "tasks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "date": {"type": ["string", "null"]},
                        "note": {"type": "string"},
                    },
                    "required": ["title", "date", "note"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["tasks"],
        "additionalProperties": False,
    },
    "strict": True,
}
INSTRUCTION = (
    "From the section text and selected references, suggest concrete tasks the creator must do next "
    "(for example confirming quotes or production schedules). Call suggest_tasks once. Titles in Korean, short. "
    "Use a date only if the material states it; otherwise null. Write dates as YYYY-MM-DD. If the material gives a "
    "relative date (for example 다음 주 금요일), convert it using the supplied today entry. Never suggest a date "
    "before today. If nothing needs doing, answer in Korean without calling the tool."
)


def _valid_date(value) -> bool:
    if value is None:
        return True
    try:
        dt.date.fromisoformat(value)
    except (TypeError, ValueError):
        return False
    return isinstance(value, str) and len(value) == 10


def parse_proposals(arguments: str, today: dt.date | None = None) -> list[dict]:
    """today를 주면 그보다 앞선 날짜는 비운다(지난 날짜는 일정으로 제안하지 않는다)."""
    try:
        args = json.loads(arguments or "{}")
    except (TypeError, ValueError):
        raise ContextError("INVALID_TOOL_ARGUMENTS") from None
    tasks = args.get("tasks") if isinstance(args, dict) and set(args) == {"tasks"} else None
    if not isinstance(tasks, list):
        raise ContextError("INVALID_TOOL_ARGUMENTS")
    proposals = []
    for task in tasks[:MAX_PROPOSALS]:
        title = task.get("title") if isinstance(task, dict) else None
        note = task.get("note", "") if isinstance(task, dict) else None
        if not isinstance(title, str) or not title.strip() or len(title) > 200 or not isinstance(note, str):
            raise ContextError("INVALID_TOOL_ARGUMENTS")
        if not _valid_date(task.get("date")):
            raise ContextError("INVALID_TOOL_ARGUMENTS")
        date = task.get("date")
        if date is not None and today is not None and dt.date.fromisoformat(date) < today:
            date = None
        proposals.append({
            "proposal_id": ObjectId(), "title": title.strip(), "suggested_date": date, "note": note[:1000],
        })
    return proposals


class ProposalJob(doc_assist.AssistJob):
    def _instruction(self) -> str:
        request = self.target["request"].strip()
        return INSTRUCTION + ("\n\nUser request: " + request if request else "")

    async def _generate(self) -> None:
        self.sections, self.proposals = {}, []
        await self.emit("context_ready", "고른 참고 정보를 확인했습니다.")
        self.budget = Budget()
        prepared = await prepare(
            self.provider, self._prefix(), [], self._instruction(), [SUGGEST_TOOL], self.emit, self.runtime.model,
            self.budget, None, False, None, self._observe_count,
        )
        await self.emit("generating", "할 일 후보를 찾고 있습니다.")
        calls = await self._stream_once(prepared.wire)
        if any(call.get("name") != "suggest_tasks" for call in calls) or len(calls) > 1:
            raise ContextError("TOOL_NOT_ALLOWED")
        if calls:
            self.proposals = parse_proposals(calls[0].get("arguments"), home.korean_today())
            found = f"할 일 후보 {len(self.proposals)}개를 찾았습니다. 확인한 것만 일정에 등록하세요."
            self.pieces = [found if self.proposals else "지금 정보로는 제안할 할 일이 없습니다."]

    def _message_extra(self, text: str, completion: str) -> dict:
        t = self.target
        return {
            "purpose": PURPOSE, "target_doc_id": t["doc_id"], "target_section_id": t["section_id"],
            "request_id": self.run_id, "proposals": self.proposals if completion == "COMPLETE" else [],
        }

    async def _after_save(self, message: dict) -> None:
        if message.get("proposals"):
            await self.emit("task_proposals", str(message["_id"]))


async def start_proposals(
    db: AsyncDatabase, runtime: Runtime, user_id: ObjectId, project: dict, doc_id: str, section_id: str, body
) -> ObjectId:
    target = await doc_assist.build_target(db, project, doc_id, section_id, "draft", body.request, body.references)
    shown = doc_assist.shown_text(target, "할 일 제안 받기")
    extra = {"purpose": PURPOSE, "target_doc_id": target["doc_id"], "target_section_id": section_id}

    def make_job(run_id: ObjectId, message: dict) -> ProposalJob:
        return ProposalJob(db, runtime, user_id, project["_id"], run_id, message["_id"], shown, target=target)

    return await ai_chat.launch(db, runtime, user_id, project["_id"], shown, make_job, extra=extra)
