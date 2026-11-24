"""AI가 실행 중에 쓰는 도구: 프로젝트 섹션 읽기, 웹 검색, 문서 작성으로 넘기기(route_document).

웹 검색은 프로젝트의 검색 허용(search_enabled)이 켜져 있을 때만 준다. 검색어는 정해진 일반 개념이거나,
사용자가 이번 요청에 적은 공개 검색어의 단어만 쓸 수 있다. 검색 직전과 결과가 온 뒤에 허용을 다시 확인해서,
실행 중에 검색을 끄면 늦게 온 결과는 쓰지 않는다.
"""
import json
import re
from collections.abc import Awaitable, Callable

from app.ai.context import ContextError
from app.ai.responses import ProviderError
from app.ai.search_policy import GENERAL_CONCEPTS, generalized_query
from app.services import ai_context

PREVIEW_CHARS = 2000
SEARCH_TOOL = {
    "type": "function",
    "name": "web_search",
    "description": "Search the web using only the user-supplied public search terms",
    "parameters": {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
        "additionalProperties": False,
    },
    "strict": True,
}
GENERAL_SEARCH_TOOL = {
    "type": "function",
    "name": "web_search_generalized",
    "description": (
        "Search public information using only broad generic concepts from the enum. Select one to four concepts "
        "relevant to the request. Never include a private name, number, source text, or free-form query. If the "
        "available concepts cannot represent the request, ask the user for explicit public search terms instead."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "concepts": {
                "type": "array",
                "items": {"type": "string", "enum": list(GENERAL_CONCEPTS)},
                "minItems": 1,
                "maxItems": 4,
            }
        },
        "required": ["concepts"],
        "additionalProperties": False,
    },
    "strict": True,
}
SEARCH_TOOL_NAMES = {"web_search", "web_search_generalized"}
ROUTE_TOOL = {
    "type": "function",
    "name": "route_document",
    "description": (
        "Route only when the user requests creating a new standalone document or deliverable. Do not route a "
        "request merely to search, find, investigate, cite, summarize, answer in chat, or edit a checklist. "
        "A request to search and create a document may be routed."
    ),
    "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
    "strict": True,
}
RECORD_TOOL = {
    "type": "function",
    "name": "propose_record",
    "description": (
        "Prepare a reviewable additional-record proposal only when the user asks to save or record a discussion. "
        "This does not save anything. Never use it to change a checklist or a document."
    ),
    "parameters": {
        "type": "object",
        "properties": {"title": {"type": "string"}, "body": {"type": "string"}},
        "required": ["title", "body"],
        "additionalProperties": False,
    },
    "strict": True,
}
RECORD_REVIEW = {
    "status": "review_required",
    "message": "검토안만 준비되었고 기록은 저장되지 않았습니다. 사용자가 검토하고 기록 저장을 직접 승인해야 합니다.",
}
MAX_RECORD_TITLE = 200
MAX_RECORD_BODY = 5000
ROUTE_REFUSED = {
    "error": "DOCUMENT_ROUTE_NOT_REQUESTED",
    "message": "검색 요청에 문서 생성은 요청되지 않았습니다. 대화로 답하세요.",
}

# 조원 파이프라인의 문서 요청 판별 규칙 그대로. "작성법", "만들지 말고" 같은 표현은 문서 요청이 아니다.
_ARTIFACT = r"(?:문서|기획서|계획서|보고서|제안서|상세\s*페이지|소개\s*자료|발표\s*자료|정리본|document|report|proposal)"
_CREATE = (
    r"(?:작성(?!자|\s*(?:법|방법|요령|가이드|하지\s*말))|만들(?!지\s*말)|생성(?!하지\s*말)|제작(?!하지\s*말)"
    r"|써\s*(?!주지\s*말)|create|write|draft)"
)


def has_document_creation_intent(text: str) -> bool:
    pattern = rf"{_ARTIFACT}.{{0,30}}{_CREATE}|{_CREATE}.{{0,30}}{_ARTIFACT}|{_ARTIFACT}\s*(?:로|형태로|형식으로)\s*정리"
    return bool(re.search(pattern, text, re.IGNORECASE))


def is_document_request(text: str) -> bool:
    if re.search(r"추가\s*기록", text) and not re.search(r"사업계획서|상품기획서|상세페이지", text):
        return False
    return has_document_creation_intent(text) and bool(re.search(r"문서|기획서|계획서|상세\s*페이지", text))


def is_search_only_request(text: str) -> bool:
    searching = re.search(r"검색|찾아|조사|\bsearch\b|\blook\s+up\b", text, re.IGNORECASE)
    return bool(searching) and not has_document_creation_intent(text)
SEARCH_FAILED = {
    "error": "SEARCH_FAILED",
    "message": "웹 검색 결과를 확인하지 못했습니다. 검색하지 않은 사실을 확인된 근거로 사용하지 마세요.",
}

Emit = Callable[[str, str], Awaitable[None]]


def _preview(text: str) -> str:
    return text[:PREVIEW_CHARS] + (" ... (일부 표시)" if len(text) > PREVIEW_CHARS else "")


class ToolBox:
    """이번 실행에서 AI에게 준 도구와 그 호출 처리."""

    def __init__(
        self,
        emit: Emit,
        provider,
        sections: dict[str, dict],
        search_enabled: bool,
        public_search_terms: str,
        search_allowed: Callable[[], Awaitable[bool]],
        route_offered: bool = False,
        record_offered: bool = False,
        read_offered: bool = True,
    ):
        self.emit = emit
        self.provider = provider
        self.sections = sections
        self.search_allowed = search_allowed
        self.public_search_terms = public_search_terms
        self.allowed_words = set(re.findall(r"\w+", public_search_terms.casefold()))
        self.tools: list[dict] = [ai_context.READ_TOOL] if read_offered else []
        self.extra_prefix: list[dict] = []
        if search_enabled:
            self.tools.append(GENERAL_SEARCH_TOOL)
            if self.allowed_words:
                self.tools.append(SEARCH_TOOL)
                self.extra_prefix.append({"key": "public_search_terms", "content": public_search_terms})
        if route_offered:
            self.tools.append(ROUTE_TOOL)
        if record_offered:
            self.tools.append(RECORD_TOOL)
        # AI가 만든 기록 검토안 {title, content}. 답변 메시지에 함께 남긴다.
        self.record_draft: dict | None = None
        # 검색으로 확인한 출처 주소. 문서의 출처 기록에 쓴다.
        self.web_urls: list[str] = []

    def names(self) -> set[str]:
        return {tool["name"] for tool in self.tools}

    def remove(self, names: set[str]) -> None:
        self.tools = [tool for tool in self.tools if tool["name"] not in names]
        if names & SEARCH_TOOL_NAMES:
            self.extra_prefix = []

    async def handle(self, calls: list[dict]) -> list[dict]:
        """도구 호출을 처리하고 모델에 돌려줄 function_call/function_call_output 목록을 만든다."""
        tail = []
        for call in calls:
            if call.get("name") not in self.names() or not call.get("call_id"):
                raise ContextError("TOOL_NOT_ALLOWED")
            try:
                args = json.loads(call.get("arguments") or "{}")
            except (TypeError, ValueError):
                raise ContextError("INVALID_TOOL_ARGUMENTS") from None
            if call["name"] == "read_project_sections":
                result = await self._read(args)
            elif call["name"] == "propose_record":
                result = await self._propose_record(args)
            else:
                result = await self._search(call["name"], args)
            tail.extend([
                {"type": "function_call", "name": call["name"], "call_id": call["call_id"],
                 "arguments": call.get("arguments") or "{}"},
                {"type": "function_call_output", "call_id": call["call_id"],
                 "output": json.dumps(result, ensure_ascii=False, default=str)},
            ])
        return tail

    async def _propose_record(self, args) -> dict:
        """기록 검토안을 화면에 보여 주기만 한다. 저장은 사용자가 검수한 뒤 POST /items/record로 한다."""
        valid = (
            isinstance(args, dict) and set(args) == {"title", "body"}
            and isinstance(args["title"], str) and isinstance(args["body"], str)
            and args["title"].strip() and args["body"].strip()
            and len(args["title"]) <= MAX_RECORD_TITLE and len(args["body"]) <= MAX_RECORD_BODY
        )
        if not valid:
            raise ContextError("INVALID_TOOL_ARGUMENTS")
        self.record_draft = {"title": args["title"].strip(), "content": args["body"]}
        await self.emit("record_candidate", json.dumps({"title": args["title"], "body": args["body"]}, ensure_ascii=False))
        return dict(RECORD_REVIEW)

    async def _read(self, args) -> list[dict]:
        keys = args.get("keys") if isinstance(args, dict) else None
        if not isinstance(keys, list) or any(not isinstance(key, str) for key in keys):
            raise ContextError("INVALID_TOOL_ARGUMENTS")
        await self.emit("tool_started", "프로젝트 정보를 조회하고 있습니다.")
        result = ai_context.read_sections(self.sections, keys)
        await self.emit("tool_finished", "프로젝트 정보 조회를 마쳤습니다.")
        return result

    async def _query(self, name: str, args) -> str:
        if name == "web_search_generalized":
            try:
                if not isinstance(args, dict) or set(args) != {"concepts"}:
                    raise ValueError("SEARCH_INPUT_DENIED")
                query = generalized_query(args["concepts"])
            except ValueError:
                raise ContextError("SEARCH_NOT_ALLOWED") from None
            await self.emit("tool_query", "일반화된 검색어: " + query)
            return query
        query = args.get("query") if isinstance(args, dict) else None
        words = re.findall(r"\w+", query.casefold()) if isinstance(query, str) else []
        if not self.allowed_words or not words or not set(words) <= self.allowed_words:
            raise ContextError("SEARCH_NOT_ALLOWED")
        query = " ".join(words)
        await self.emit("tool_query", "직접 허용한 검색어: " + query)
        return query

    def _refusal(self, name: str) -> dict:
        if name == "web_search_generalized":
            message = "정해진 일반 개념 목록에 있는 값만 쓸 수 있습니다. 개인 이름·숫자·원문은 넣지 마세요."
        else:
            message = (
                f"사용자가 허용한 공개 검색어({self.public_search_terms})의 단어만 그대로 쓸 수 있습니다. "
                "조사나 다른 단어를 붙이지 마세요."
            )
        return {"error": "SEARCH_NOT_ALLOWED", "message": message}

    async def _search(self, name: str, args) -> dict:
        try:
            query = await self._query(name, args)
        except ContextError:
            # 실행 전체를 끝내지 않고, 검색하지 않은 채 AI에게 허용 범위를 알려 다시 고르게 한다.
            await self.emit("tool_finished", "허용 범위를 벗어난 검색어라 검색하지 않았습니다.")
            return self._refusal(name)
        if not await self.search_allowed():
            return await self._disabled()
        await self.emit("tool_started", "허용한 검색어로 웹을 검색하고 있습니다.")
        try:
            result = dict(await self.provider.web_search(query))
        except ProviderError:
            # 결과를 모르는 실패는 Provider 쪽에서 실행 전체의 '결과 모름'으로 올라간다.
            self.remove(SEARCH_TOOL_NAMES)
            await self.emit("tool_finished", "웹 검색을 완료하지 못했습니다. 확인된 검색 근거 없이 답변합니다.")
            return dict(SEARCH_FAILED)
        if not await self.search_allowed():
            return await self._disabled()
        await self.emit("tool_finished", "웹 검색 결과를 확인했습니다.")
        urls = [url for url in result.get("urls", []) if isinstance(url, str)]
        self.web_urls += urls
        if not urls:
            await self.emit("tool_finished", "출처 링크가 제공되지 않아 외부 주장을 검증할 수 없습니다.")
        if result.get("text"):
            sources = "\n출처: " + ", ".join(urls) if urls else ""
            await self.emit("tool_result", _preview(str(result["text"])) + sources)
        return result

    async def _disabled(self) -> dict:
        self.remove(SEARCH_TOOL_NAMES)
        await self.emit("tool_finished", "검색이 꺼져 있어 검색 결과를 사용하지 않습니다.")
        return {"error": "SEARCH_DISABLED"}
