"""Opt-in, synthetic-only P5 application. Start with uvicorn app.ai.p5.server:app --host 127.0.0.1."""
from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app.ai.responses import Delivery, ProviderError
from app.ai.p5.context import Budget, ContextError, MODEL_WINDOW, OUTPUT_RESERVE, SAFETY_RESERVE, THRESHOLD, history_summary_text, prepare
from app.ai.p5.export import ExportError, export
from app.ai.p5.provider import DemoProvider, OpenAIProvider
from app.ai.p5.search_policy import GENERAL_CONCEPTS, generalized_query
from app.ai.p5.store import Store, StoreError
from app.ai.p5.usage import UsageTracker, valid_usage

READ_TOOL = {"type": "function", "name": "read_project_sections", "description": "Read available project planning sections or active attached text already supplied in context. Use keys attachments or files for all active attachments, or an exact attachment ID or name. Attachments not included in this request are unavailable.", "parameters": {"type": "object", "properties": {"keys": {"type": "array", "items": {"type": "string"}}}, "required": ["keys"], "additionalProperties": False}, "strict": True}
SEARCH_TOOL = {"type": "function", "name": "web_search", "description": "Search the web using only the user-supplied public search terms", "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"], "additionalProperties": False}, "strict": True}
GENERAL_SEARCH_TOOL = {"type": "function", "name": "web_search_generalized", "description": "Search public information using only broad generic concepts from the enum. Select one to four concepts relevant to the request. Never include a private name, number, source text, or free-form query. If the available concepts cannot represent the request, ask the user for explicit public search terms instead.", "parameters": {"type": "object", "properties": {"concepts": {"type": "array", "items": {"type": "string", "enum": list(GENERAL_CONCEPTS)}, "minItems": 1, "maxItems": 4}}, "required": ["concepts"], "additionalProperties": False}, "strict": True}
ROUTE_TOOL = {"type": "function", "name": "route_document", "description": "Route a user's request for a new standalone document to the document generator; do not call for general discussion or checklist edits.", "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False}, "strict": True}
RECORD_TOOL = {"type": "function", "name": "propose_record", "description": "Prepare a reviewable additional-record proposal only when the user asks to save or record a discussion. This does not save anything. Never use it to change a checklist or a 04 document.", "parameters": {"type": "object", "properties": {"title": {"type": "string"}, "body": {"type": "string"}}, "required": ["title", "body"], "additionalProperties": False}, "strict": True}


def decode_attachment(filename: str, data: bytes) -> tuple[str, str]:
    if Path(filename).suffix.lower() not in (".txt", ".md"):
        raise ValueError("UNSUPPORTED_FILE")
    valid = []
    for encoding in ("utf-8-sig", "cp949"):
        try:
            text = data.decode(encoding, errors="strict")
            if "\x00" in text:
                continue
            valid.append((encoding, text))
        except UnicodeDecodeError:
            pass
    if not valid:
        raise ValueError("INVALID_ENCODING")
    if len(valid) == 2 and valid[0][1] != valid[1][1]:
        raise ValueError("AMBIGUOUS_ENCODING")
    return valid[0]


class SessionIn(BaseModel):
    title: str = Field(default="새 대화", min_length=1, max_length=120)


class RunIn(BaseModel):
    text: str = Field(default="", max_length=100_000)
    mode: str = "auto"
    include_session: bool = False
    include_draft: list[str] = []
    reference_document: str | None = None
    reference_body: str | None = None
    search_allowed: bool = False
    public_search_terms: str = Field(default="", max_length=500)


class CKIn(BaseModel):
    content: str
    state: str
    revision: int
    approved: bool = False


class SearchPolicyIn(BaseModel):
    enabled: bool


class DocumentIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    body: str
    revision: int
    approved: bool = False


class DeleteIn(BaseModel):
    revision: int
    approved: bool = False


class CandidateSave(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1)


class RecordIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1)
    revision: int = Field(ge=0)
    approved: bool = False


def is_document_request(text: str) -> bool:
    if re.search(r"추가\s*기록", text) and not re.search(r"사업계획서|상품기획서|상세페이지", text):
        return False
    return bool(re.search(r"(문서|기획서|사업계획서|상세페이지).{0,30}(작성|만들|생성|써)", text))


def create_app(store: Store | None = None, provider=None) -> FastAPI:
    app = FastAPI(title="P5 synthetic prototype")
    app.state.store = store or Store()
    app.state.store.seed()
    app.state.provider = provider or DemoProvider()
    app.state.tasks = {}
    app.state.search_permissions = {}
    owner = "demo"  # This application is synthetic-only, not an authentication boundary.
    # Single-process recovery releases the local fence without assuming remote cancellation.
    app.state.store.recover_orphans()

    @app.exception_handler(StoreError)
    async def store_error(_, exc):
        return JSONResponse(status_code=404 if str(exc) == "NOT_FOUND" else 409, content={"code": str(exc)})

    @app.get("/")
    async def home():
        return FileResponse(Path(__file__).with_name("ui.html"))

    @app.get("/p5/config")
    async def config():
        return {"provider": "openai" if isinstance(app.state.provider, OpenAIProvider) else "synthetic", "storage": "synthetic"}

    @app.get("/p5/projects")
    async def projects():
        return app.state.store.all("SELECT id,name,brand FROM projects WHERE owner=? AND deleted=0", (owner,))

    @app.get("/p5/projects/{pid}/session")
    async def session(pid: str):
        return app.state.store.create_session(pid, owner, "프로젝트 대화")

    @app.get("/p5/projects/{pid}/sessions/{sid}/search-policy")
    async def get_search_policy(pid: str, sid: str):
        return app.state.store.search_policy(pid, sid, owner)

    def apply_search_policy(pid, sid, enabled):
        db = app.state.store
        policy = db.set_search_policy(pid, sid, owner, enabled)
        rid = policy.pop('active_run')
        if not enabled and rid and app.state.search_permissions.get(rid, False):
            app.state.search_permissions[rid] = False
            db.event(sid, rid, 'search_disabled', '검색을 껐습니다. 이후 도착하는 검색 결과는 사용하지 않습니다.')
        return policy

    @app.put("/p5/projects/{pid}/sessions/{sid}/search-policy")
    async def set_search_policy(pid: str, sid: str, body: SearchPolicyIn):
        return apply_search_policy(pid, sid, body.enabled)

    @app.get("/p5/projects/{pid}/sessions/{sid}/messages")
    async def messages(pid: str, sid: str):
        app.state.store.session(pid, sid, owner)
        return app.state.store.messages(sid)

    @app.get("/p5/projects/{pid}/sessions/{sid}/events")
    async def events(pid: str, sid: str):
        app.state.store.session(pid, sid, owner)
        return app.state.store.all("SELECT run_id,seq,kind,content FROM events WHERE session_id=? ORDER BY rowid", (sid,))

    @app.get("/p5/projects/{pid}/sessions/{sid}/attachments")
    async def attachments(pid: str, sid: str):
        app.state.store.session(pid, sid, owner)
        return [{k: a[k] for k in ("id", "name", "revision", "encoding")} for a in app.state.store.active_files(sid)]

    @app.get("/p5/projects/{pid}/sessions/{sid}/attachments/{fid}/content")
    async def attachment_content(pid: str, sid: str, fid: str):
        app.state.store.session(pid, sid, owner)
        attachment = next((a for a in app.state.store.active_files(sid) if a["id"] == fid), None)
        if attachment is None:
            raise HTTPException(404, "NOT_FOUND")
        filename = quote(Path(attachment["name"]).name, safe="")
        return Response(attachment["body"], media_type="application/octet-stream", headers={"Content-Disposition": f"attachment; filename=attachment.txt; filename*=UTF-8''{filename}"})

    @app.post("/p5/projects/{pid}/sessions/{sid}/attachments", status_code=201)
    async def attach(pid: str, sid: str, file: UploadFile = File(...)):
        app.state.store.session(pid, sid, owner)
        data = await file.read()
        if len(data) > 5_000_000:
            raise HTTPException(413, "프로토타입 첨부 한도는 5MB입니다.")
        try:
            encoding, _ = decode_attachment(file.filename or "", data)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        ident = app.state.store.attachment(pid, sid, owner, file.filename, data, encoding)
        return {"id": ident, "name": file.filename, "encoding": encoding}

    @app.delete("/p5/projects/{pid}/sessions/{sid}/attachments/{fid}", status_code=204)
    async def remove_attachment(pid: str, sid: str, fid: str):
        app.state.store.session(pid, sid, owner)
        app.state.store.remove_attachment(sid, fid)

    @app.get("/p5/projects/{pid}/context")
    async def context(pid: str):
        app.state.store.project(pid, owner)
        return app.state.store.snapshot(pid)

    @app.put("/p5/projects/{pid}/ck/{key}")
    async def save_ck(pid: str, key: str, body: CKIn):
        if not body.approved:
            raise HTTPException(403, "최종 승인이 필요합니다.")
        return app.state.store.update_ck(pid, owner, key, body.content, body.state, body.revision)

    @app.get("/p5/projects/{pid}/record")
    async def get_record(pid: str):
        app.state.store.project(pid, owner)
        return app.state.store.record(pid)

    @app.put("/p5/projects/{pid}/record")
    async def save_record(pid: str, body: RecordIn):
        app.state.store.project(pid, owner)
        if not body.approved:
            raise HTTPException(403, "내용을 확인하고 기록 저장을 승인해 주세요.")
        return app.state.store.save_record(pid, body.title, body.body, body.revision)

    @app.get("/p5/projects/{pid}/planning-export/{fmt}")
    async def planning_export(pid: str, fmt: str):
        app.state.store.project(pid, owner)
        if fmt not in ("md", "txt"):
            raise HTTPException(422, "UNSUPPORTED_FORMAT")
        rows, record = app.state.store.planning_export(pid)
        labels = ("상품선정", "고객선정", "구매가치결정", "상품구성결정", "가격설정", "제작가능성")
        chunks = ["# 저장된 상품 기획" if fmt == "md" else "저장된 상품 기획"]
        for row, label in zip(rows, labels):
            title = f"{label} ({row['key']}) · {row['state']}"
            chunks.append(("## " if fmt == "md" else "") + title + "\n\n" + (row["content"] or "(내용 없음)"))
        if record["exists"]:
            chunks.append(("## " if fmt == "md" else "") + "추가 기록: " + record["title"] + "\n\n" + record["body"])
        return Response("\n\n".join(chunks).encode("utf-8"), media_type="text/markdown" if fmt == "md" else "text/plain", headers={"Content-Disposition": f'attachment; filename="planning.{fmt}"'})

    async def run_task(pid, sid, rid, request: RunIn):
        db, underlying_provider = app.state.store, app.state.provider
        class GuardedProvider:
            def check(self):
                if not db.run_current(sid, rid):
                    raise ContextError("RUN_NOT_ACTIVE")
            async def count(self, wire):
                self.check()
                return await underlying_provider.count(wire)
            async def summarize(self, messages):
                self.check()
                await tracker.start("compression")
                try:
                    result = await underlying_provider.summarize(messages)
                except BaseException as exc:
                    await tracker.finish("compression", getattr(exc, "usage", None), not_dispatched=isinstance(exc, ProviderError) and exc.delivery == Delivery.NOT_DISPATCHED)
                    raise
                await tracker.finish("compression", getattr(result, "usage", None))
                return result
            async def stream(self, wire):
                self.check()
                await tracker.start("generation")
                usage = None
                not_dispatched = False
                try:
                    async for event in underlying_provider.stream(wire):
                        self.check()
                        if event.get("type") == "usage" and usage is None:
                            usage = valid_usage(event)
                        yield event
                except BaseException as exc:
                    not_dispatched = isinstance(exc, ProviderError) and exc.delivery == Delivery.NOT_DISPATCHED
                    if isinstance(exc, ProviderError) and exc.delivery == Delivery.UNKNOWN:
                        raise ContextError("PROVIDER_OUTCOME_UNKNOWN") from exc
                    raise
                finally:
                    await tracker.finish("generation", usage, not_dispatched)
            async def web_search(self, query):
                self.check()
                await tracker.start("web_search")
                try:
                    result = await underlying_provider.web_search(query)
                except BaseException as exc:
                    await tracker.finish("web_search", getattr(exc, "usage", None), not_dispatched=isinstance(exc, ProviderError) and exc.delivery == Delivery.NOT_DISPATCHED)
                    if isinstance(exc, ProviderError) and exc.delivery == Delivery.UNKNOWN:
                        raise ContextError("PROVIDER_OUTCOME_UNKNOWN") from exc
                    raise
                await tracker.finish("web_search", getattr(result, "usage", None))
                return result
        provider = GuardedProvider()
        pieces, sources = [], []
        status, error_code, delivery = "failed", None, None
        async def emit(kind, text):
            if db.run_current(sid, rid):
                db.event(sid, rid, kind, text)
        tracker = UsageTracker(emit, "openai" if isinstance(underlying_provider, OpenAIProvider) else "synthetic")
        async def observe_count(count):
            await emit("context_usage", json.dumps({"input_tokens": count, "model_window": MODEL_WINDOW,
                "output_reserve": OUTPUT_RESERVE, "safety_reserve": SAFETY_RESERVE,
                "compression_threshold": THRESHOLD,
                "measurement": "openai" if isinstance(underlying_provider, OpenAIProvider) else "synthetic"}))
        try:
            await emit("started", "요청을 접수했습니다.")
            document_mode = request.mode == "document" or (request.mode == "auto" and is_document_request(request.text))
            if request.mode not in ("auto", "chat", "document"):
                raise ContextError("INVALID_MODE")
            current_text = request.text or "첨부 자료만 보냈습니다. 실제로 읽은 내용을 짧게 설명하고 다음 행동을 제안해 주세요."
            snapshot = db.snapshot(pid)
            project_record = db.record(pid)
            if project_record["exists"]:
                snapshot.append({"type": "record", "key": "record", "content": project_record["body"], "title": project_record["title"], "revision": project_record["revision"]})
            file_snapshot = db.active_files(sid)
            selected = [x for x in snapshot if not document_mode or x["type"] != "ck" or x["state"] == "DECIDED" or (x["state"] == "DRAFT" and x["key"] in request.include_draft)]
            manifest = [{"type": "project", "id": pid, "revision": None}]
            if project_record["exists"]:
                manifest.append({"type": "record", "id": "record", "revision": project_record["revision"]})
            manifest.extend({"type": "ck", "id": x["key"], "revision": x["revision"], "state": x["state"]} for x in selected if x["type"] == "ck")
            for f in file_snapshot if not document_mode or request.include_session else []:
                selected.append({"type": "attachment", "id": f["id"], "name": f["name"], "content": f["body"].decode(f["encoding"]), "revision": f["revision"]})
                sources.append(f["id"])
                manifest.append({"type": "attachment", "id": f["id"], "revision": f["revision"], "session_id": sid})
            history = db.messages(sid, allowed_only=True)
            if document_mode and not request.include_session:
                history = []
            if request.reference_document:
                if not document_mode:
                    raise ContextError("DOCUMENT_REFERENCE_REQUIRES_DOCUMENT_MODE")
                ref = db.document(pid, request.reference_document)
                selected.append({"type": "document", "id": ref["id"], "revision": ref["revision"], "content": ref["body"] if request.reference_body is None else request.reference_body})
                manifest.append({"type": "document", "id": ref["id"], "revision": ref["revision"], "source": "saved" if request.reference_body is None else "editor"})
            elif request.reference_body is not None:
                raise ContextError("DOCUMENT_REFERENCE_REQUIRED")
            if document_mode and request.include_session and history:
                manifest.append({"type": "session", "id": sid, "message_refs": [{"id": m["id"], "ordinal": m["ordinal"]} for m in history]})
            await emit("context_ready", "허용된 프로젝트와 세션 자료를 확인했습니다.")
            display_text = request.text if request.text else "[파일만 전송]"
            current_mid = db.message(sid, "user", display_text, run_id=rid)
            await emit("user_message", display_text)
            session = db.session(pid, sid, owner)
            capsule_rev = session["capsule_rev"]
            # Reuse only a capsule whose source attachments remain active and
            # whose covered messages are still present in this session.
            if history and session["capsule"]:
                ids = json.loads(session["capsule_ids"] or "[]")
                deps = json.loads(session["capsule_sources"] or "[]")
                if set(deps) <= set(sources) and set(ids) <= {m["id"] for m in history}:
                    history = [{"id": "capsule", "role": "user", "content": history_summary_text(session["capsule"]), "source_ids": deps, "covered_ids": ids}] + [m for m in history if m["id"] not in ids]

            async def save_capsule(text, ids, deps):
                nonlocal capsule_rev
                db.save_capsule(sid, capsule_rev, text, ids, deps, rid)
                capsule_rev += 1

            def apply_prepared_history(existing, prepared):
                if not prepared.summary:
                    return existing
                covered = set(prepared.covered_ids)
                capsule = {"id": "capsule", "role": "user", "content": history_summary_text(prepared.summary), "source_ids": prepared.source_ids, "covered_ids": prepared.covered_ids}
                return [capsule] + [m for m in existing if not set(m.get("covered_ids", [m["id"]])) <= covered]

            budget = Budget()
            allowed_words = set(re.findall(r"\w+", request.public_search_terms.casefold()))
            tools = [READ_TOOL]
            if not document_mode:
                tools.append(RECORD_TOOL)
            if request.mode == "auto" and not document_mode:
                tools.append(ROUTE_TOOL)
            if request.search_allowed and hasattr(underlying_provider, "web_search"):
                tools.append(GENERAL_SEARCH_TOOL)
                if allowed_words:
                    tools.append(SEARCH_TOOL)
                    selected.append({"type": "public_search_terms", "content": request.public_search_terms})
            prepared = await prepare(provider, selected, history, current_text, tools, emit, budget, save_capsule, document_mode, observe_count=observe_count)
            wire = prepared.wire
            history = apply_prepared_history(history, prepared)
            tail = []
            if document_mode:
                await emit("document_route", "문서 작성 작업으로 전달했습니다.")
            await emit("generating", "답변을 생성하고 있습니다.")
            for _ in range(6):
                if not db.run_current(sid, rid):
                    raise ContextError("RUN_NOT_ACTIVE")
                tool_calls = []
                completed = False
                await emit("generation_usage_pending", "")
                async for event in provider.stream(wire):
                    if not db.run_current(sid, rid):
                        raise ContextError("RUN_NOT_ACTIVE")
                    if event["type"] == "delta":
                        pieces.append(event["text"])
                        await emit("delta", event["text"])
                    elif event["type"] == "tool":
                        tool_calls.append(event)
                    elif event["type"] == "usage":
                        await emit("generation_usage", json.dumps({k: event[k] for k in ("input_tokens", "output_tokens", "total_tokens")}))
                    elif event["type"] == "completed":
                        completed = True
                if not completed:
                    raise ContextError("PROVIDER_OUTCOME_UNKNOWN")
                if not tool_calls:
                    break
                if any(call.get("name") == "route_document" for call in tool_calls):
                    if document_mode or request.mode != "auto" or len(tool_calls) != 1 or not tool_calls[0].get("call_id"):
                        raise ContextError("TOOL_NOT_ALLOWED")
                    try:
                        if json.loads(tool_calls[0].get("arguments") or "{}") != {}:
                            raise ValueError
                    except (TypeError, ValueError):
                        raise ContextError("INVALID_TOOL_ARGUMENTS") from None
                    document_mode = True
                    selected = [x for x in snapshot if x["type"] != "ck" or x["state"] == "DECIDED" or (x["state"] == "DRAFT" and x["key"] in request.include_draft)]
                    manifest = [{"type": "project", "id": pid, "revision": None}]
                    if project_record["exists"]:
                        manifest.append({"type": "record", "id": "record", "revision": project_record["revision"]})
                    manifest.extend({"type": "ck", "id": x["key"], "revision": x["revision"], "state": x["state"]} for x in selected if x["type"] == "ck")
                    sources = []
                    if request.include_session:
                        for f in file_snapshot:
                            selected.append({"type": "attachment", "id": f["id"], "name": f["name"], "content": f["body"].decode(f["encoding"]), "revision": f["revision"]})
                            sources.append(f["id"])
                            manifest.append({"type": "attachment", "id": f["id"], "revision": f["revision"], "session_id": sid})
                        original_history = [m for m in db.messages(sid, allowed_only=True) if m["id"] != current_mid]
                        if original_history:
                            manifest.append({"type": "session", "id": sid, "message_refs": [{"id": m["id"], "ordinal": m["ordinal"]} for m in original_history]})
                        # Reuse this run's already prepared history; rebuilding it
                        # from raw messages would discard compression and spend
                        # another stage on the same source content.
                    else:
                        history = []
                    tools = [t for t in tools if t["name"] not in ("route_document", "propose_record") and (t["name"] not in ("web_search", "web_search_generalized") or app.state.search_permissions.get(rid, False))]
                    if app.state.search_permissions.get(rid, False) and allowed_words and hasattr(underlying_provider, "web_search"):
                        selected.append({"type": "public_search_terms", "content": request.public_search_terms})
                    pieces = []
                    tail = []
                    await emit("document_route", "문서 작성 작업으로 전달했습니다.")
                    prepared = await prepare(provider, selected, history, current_text, tools, emit, budget, save_capsule, True, observe_count=observe_count)
                    wire = prepared.wire
                    history = apply_prepared_history(history, prepared)
                    continue
                for call in tool_calls:
                    if call.get("name") not in ("read_project_sections", "web_search", "web_search_generalized", "propose_record") or not call.get("call_id"):
                        raise ContextError("TOOL_NOT_ALLOWED")
                    if call['name'] not in {tool['name'] for tool in tools}:
                        raise ContextError("TOOL_NOT_ALLOWED")
                    try:
                        args = json.loads(call.get("arguments") or "{}")
                    except (ValueError, KeyError, TypeError):
                        raise ContextError("INVALID_TOOL_ARGUMENTS") from None
                    if call["name"] == "propose_record":
                        if document_mode or not isinstance(args, dict) or set(args) != {"title", "body"} or not isinstance(args["title"], str) or not isinstance(args["body"], str) or not args["title"].strip() or not args["body"].strip() or len(args["title"]) > 200 or len(args["body"]) > 100_000:
                            raise ContextError("INVALID_TOOL_ARGUMENTS")
                        await emit("record_candidate", json.dumps({"title": args["title"], "body": args["body"]}, ensure_ascii=False))
                        tool_result = {"status": "review_required", "message": "검토안만 준비되었고 기록은 저장되지 않았습니다. 사용자가 검토하고 기록 저장을 직접 승인해야 합니다."}
                    elif call["name"] == "read_project_sections":
                        keys = args.get("keys") if isinstance(args, dict) else None
                        if not isinstance(keys, list) or any(not isinstance(k, str) for k in keys):
                            raise ContextError("INVALID_TOOL_ARGUMENTS")
                        await emit("tool_started", "프로젝트 정보를 조회하고 있습니다.")
                        requested = {k.casefold() for k in keys}
                        tool_result = [x for x in selected if x.get("key") in keys or (x.get("type") == "attachment" and (bool(requested & {"attachments", "attachment", "files", "file", "reference_files"}) or x.get("id") in keys or x.get("name") in keys))]
                        await emit("tool_finished", "프로젝트 정보 조회를 마쳤습니다.")
                        preview = json.dumps(tool_result, ensure_ascii=False)
                        await emit("tool_result", preview[:2000] + (" ... (일부 표시)" if len(preview) > 2000 else ""))
                    else:
                        if not request.search_allowed or not hasattr(underlying_provider, "web_search"):
                            raise ContextError("SEARCH_NOT_ALLOWED")
                        if call['name'] == 'web_search_generalized':
                            try:
                                if not isinstance(args, dict) or set(args) != {'concepts'}:
                                    raise ValueError('SEARCH_INPUT_DENIED')
                                search_query = generalized_query(args['concepts'])
                            except ValueError:
                                raise ContextError('SEARCH_NOT_ALLOWED') from None
                            await emit('tool_query', '일반화된 검색어: ' + search_query)
                        else:
                            query = args.get("query") if isinstance(args, dict) else None
                            words = re.findall(r"\w+", query.casefold()) if isinstance(query, str) else []
                            if not allowed_words or not words or not set(words) <= allowed_words:
                                raise ContextError("SEARCH_NOT_ALLOWED")
                            search_query = ' '.join(words)
                            await emit('tool_query', '직접 허용한 검색어: ' + search_query)
                        if app.state.search_permissions.get(rid, False):
                            await emit("tool_started", "허용한 공개 검색어로 웹을 검색하고 있습니다.")
                            try:
                                tool_result = await provider.web_search(search_query)
                            except ProviderError as exc:
                                # Unknown delivery is already promoted by GuardedProvider;
                                # a confirmed failure can be explained without fabricating results.
                                tool_result = {"error": "SEARCH_FAILED", "message": "웹 검색 결과를 확인하지 못했습니다. 검색하지 않은 사실을 확인된 근거로 사용하지 마세요."}
                                tools = [t for t in tools if t.get("name") not in ("web_search", "web_search_generalized")]
                        else:
                            tool_result = {"error": "SEARCH_DISABLED"}
                        if not app.state.search_permissions.get(rid, False):
                            tool_result = {"error": "SEARCH_DISABLED"}
                            tools = [t for t in tools if t.get("name") not in ("web_search", "web_search_generalized")]
                            selected = [x for x in selected if x.get("type") != "public_search_terms"]
                            await emit("tool_finished", "검색이 꺼져 이후 도착한 결과를 사용하지 않습니다.")
                        elif tool_result.get("error"):
                            await emit("tool_finished", "웹 검색을 완료하지 못했습니다. 확인된 검색 근거 없이 답변합니다.")
                        else:
                            await emit("tool_finished", "웹 검색 결과를 확인했습니다.")
                            if not tool_result.get('urls'):
                                await emit('tool_finished', '출처 링크가 제공되지 않아 외부 주장을 검증할 수 없습니다.')
                        if isinstance(tool_result, dict) and tool_result.get("text"):
                            urls = [u for u in tool_result.get("urls", []) if isinstance(u, str)]
                            result_text = str(tool_result["text"])
                            await emit("tool_result", result_text[:2000] + (" ... (일부 표시)" if len(result_text) > 2000 else "") + ("\n출처: " + ", ".join(urls) if urls else ""))
                            if document_mode:
                                manifest.extend({"type": "web", "id": u, "revision": None} for u in urls)
                    tail.extend([
                        {"type": "function_call", "name": call["name"], "call_id": call["call_id"], "arguments": call["arguments"]},
                        {"type": "function_call_output", "call_id": call["call_id"], "output": json.dumps(tool_result, ensure_ascii=False)},
                    ])
                if not app.state.search_permissions.get(rid, False):
                    tools = [t for t in tools if t.get("name") not in ("web_search", "web_search_generalized")]
                    selected = [x for x in selected if x.get("type") != "public_search_terms"]
                prepared = await prepare(provider, selected, history, current_text, tools, emit, budget, save_capsule, document_mode, tail, observe_count)
                wire = prepared.wire
                history = apply_prepared_history(history, prepared)
            else:
                raise ContextError("TOOL_BUDGET_EXHAUSTED")
            text = "".join(pieces)
            if not text.strip():
                raise ContextError("EMPTY_OUTPUT")
            db.message(sid, "assistant", text, sources, run_id=rid)
            if document_mode:
                cid = db.candidate(pid, sid, "생성 문서", text, manifest, run_id=rid)
                await emit("document_candidate", cid)
            await emit("completed", "완료했습니다.")
            status = "completed"
        except asyncio.CancelledError:
            if pieces and db.run_current(sid, rid):
                db.message(sid, "assistant", "".join(pieces), sources, run_id=rid)
            await emit("stopped", "중단했습니다. 원격 처리의 취소 여부는 확인되지 않았습니다.")
            status = "stopped"
        except (ContextError, StoreError, ProviderError) as exc:
            if pieces and db.run_current(sid, rid):
                db.message(sid, "assistant", "".join(pieces), sources, run_id=rid)
            code = exc.code if isinstance(exc, ProviderError) else str(exc)
            cause = exc
            while cause is not None and not isinstance(cause, ProviderError):
                cause = cause.__cause__
            raw_code = cause.code if cause else code
            error_code = raw_code if re.fullmatch(r"[A-Z0-9_]{1,80}", raw_code) else "INTERNAL_ERROR"
            delivery = cause.delivery.value if cause else ("unknown" if code == "PROVIDER_OUTCOME_UNKNOWN" else None)
            if code == "PROVIDER_OUTCOME_UNKNOWN":
                status = "unknown"
                return
            if code in ("COMPRESSION_FAILED", "EMPTY_SUMMARY", "INSUFFICIENT_COMPRESSION", "CAPSULE_CONFLICT"):
                await emit("compression_failed", "컨텍스트 압축을 완료하지 못했습니다. 기존 대화는 유지됩니다.")
            reason = {
                "MODEL_LIMIT_EXCEEDED": "모델의 실제 입력 한도를 넘어 요청을 중단했습니다. 자료를 줄여 주세요.",
                "KEY_MISSING_OR_INVALID": "OpenAI 연결 설정을 확인해 주세요.",
                "SEARCH_NOT_ALLOWED": "허용된 일반 개념 또는 공개 검색어 범위를 벗어나 검색을 중단했습니다.",
                "CAPSULE_CONFLICT": "컨텍스트 저장이 충돌하여 요청을 중단했습니다.",
            }.get(code, "요청을 완료하지 못했습니다. 입력과 연결 상태를 확인해 주세요.")
            await emit("failed", reason)
        except Exception:
            error_code = "INTERNAL_ERROR"
            if pieces and db.run_current(sid, rid):
                db.message(sid, "assistant", "".join(pieces), sources, run_id=rid)
            await emit("failed", "예상하지 못한 오류로 요청을 완료하지 못했습니다.")
        finally:
            db.finish_run(sid, rid, status, error_code, delivery)
            if status == "unknown":
                db.event(sid, rid, "outcome_unknown", "원격 처리 결과는 확인되지 않았습니다. 대화는 계속 사용할 수 있습니다.")
            app.state.search_permissions.pop(rid, None)

    @app.post("/p5/projects/{pid}/sessions/{sid}/runs", status_code=202)
    async def start_run(pid: str, sid: str, body: RunIn):
        db = app.state.store
        policy = db.search_policy(pid, sid, owner)
        if not body.text.strip():
            if not db.active_files(sid):
                raise HTTPException(422, "메시지 또는 첨부 파일이 필요합니다.")
            body = body.model_copy(update={"text": ""})
        body = body.model_copy(update={'search_allowed': policy['enabled']})
        rid = db.begin_run(pid, sid, owner)
        app.state.search_permissions[rid] = policy['enabled']
        task = asyncio.create_task(run_task(pid, sid, rid, body))
        app.state.tasks[rid] = task
        def cleanup(done):
            app.state.tasks.pop(rid, None)
            if done.cancelled():
                db.event(sid, rid, "stopped", "중단했습니다. 원격 처리의 취소 여부는 확인되지 않았습니다.")
                db.finish_run(sid, rid, "stopped")
        task.add_done_callback(cleanup)
        return {"run_id": rid}

    @app.post("/p5/projects/{pid}/sessions/{sid}/runs/{rid}/search-off")
    async def search_off(pid: str, sid: str, rid: str):
        app.state.store.session(pid, sid, owner)
        if not app.state.store.run_current(sid, rid):
            raise HTTPException(409, "RUN_NOT_ACTIVE")
        apply_search_policy(pid, sid, False)
        return {"search_allowed": False}

    @app.get("/p5/projects/{pid}/sessions/{sid}/runs/{rid}")
    async def run_metadata(pid: str, sid: str, rid: str):
        app.state.store.session(pid, sid, owner)
        result = app.state.store.one("SELECT id,status,created_at,error_code,delivery FROM runs WHERE id=? AND session_id=?", (rid, sid))
        if not result:
            raise HTTPException(404)
        return result

    @app.post("/p5/projects/{pid}/sessions/{sid}/runs/{rid}/stop")
    async def stop_run(pid: str, sid: str, rid: str):
        app.state.store.session(pid, sid, owner)
        run = app.state.store.one("SELECT * FROM runs WHERE id=? AND session_id=?", (rid, sid))
        if not run:
            raise HTTPException(404)
        task = app.state.tasks.get(rid)
        if task:
            task.cancel()
        return {"status": "stop_requested" if task else run["status"]}

    @app.get("/p5/projects/{pid}/sessions/{sid}/runs/{rid}/stream")
    async def stream(pid: str, sid: str, rid: str, after: int = 0):
        app.state.store.session(pid, sid, owner)
        if not app.state.store.one("SELECT id FROM runs WHERE id=? AND session_id=?", (rid, sid)):
            raise HTTPException(404)
        async def iterator():
            seq = after
            while True:
                rows = app.state.store.all("SELECT seq,kind,content FROM events WHERE session_id=? AND run_id=? AND seq>? ORDER BY seq", (sid, rid, seq))
                for row in rows:
                    seq = row["seq"]
                    yield f"id: {seq}\ndata: {json.dumps(row, ensure_ascii=False)}\n\n"
                run = app.state.store.one("SELECT status FROM runs WHERE id=?", (rid,))
                if run["status"] != "running" and not rows:
                    break
                await asyncio.sleep(0.05)
        return StreamingResponse(iterator(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    @app.get("/p5/projects/{pid}/sessions/{sid}/candidates/{cid}")
    async def candidate(pid: str, sid: str, cid: str):
        app.state.store.session(pid, sid, owner)
        c = app.state.store.one("SELECT * FROM candidates WHERE id=? AND project_id=? AND session_id=?", (cid, pid, sid))
        if not c:
            raise HTTPException(404)
        c["manifest"] = json.loads(c["manifest"])
        return c

    @app.post("/p5/projects/{pid}/sessions/{sid}/candidates/{cid}/save", status_code=201)
    async def save_candidate(pid: str, sid: str, cid: str, body: CandidateSave | None = None):
        app.state.store.session(pid, sid, owner)
        doc_id = app.state.store.save_candidate(pid, sid, cid, body.title if body else None, body.body if body else None)
        return app.state.store.document(pid, doc_id)

    @app.get("/p5/projects/{pid}/documents")
    async def documents(pid: str):
        app.state.store.project(pid, owner)
        return app.state.store.all("SELECT id,title,revision FROM documents WHERE project_id=? ORDER BY rowid DESC", (pid,))

    @app.get("/p5/projects/{pid}/documents/{did}")
    async def document(pid: str, did: str):
        app.state.store.project(pid, owner)
        doc = app.state.store.document(pid, did)
        doc["changes"] = app.state.store.document_changes(doc)
        return doc

    @app.put("/p5/projects/{pid}/documents/{did}")
    async def save_document(pid: str, did: str, body: DocumentIn):
        app.state.store.project(pid, owner)
        if not body.approved:
            raise HTTPException(403, "대상과 내용을 확인하고 승인해 주세요.")
        return app.state.store.save_document(pid, did, body.revision, body.title, body.body)

    @app.post("/p5/projects/{pid}/documents/{did}/delete")
    async def delete_document(pid: str, did: str, body: DeleteIn):
        app.state.store.project(pid, owner)
        if not body.approved:
            raise HTTPException(403, "대상과 내용을 확인하고 승인해 주세요.")
        app.state.store.delete_document(pid, did, body.revision)
        return {"deleted": True}

    @app.get("/p5/projects/{pid}/documents/{did}/export/{fmt}")
    async def export_document(pid: str, did: str, fmt: str):
        app.state.store.project(pid, owner)
        doc = app.state.store.document(pid, did)
        try:
            data, mime = await run_in_threadpool(export, doc["body"], fmt)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except ExportError as exc:
            raise HTTPException(503, str(exc)) from exc
        return Response(data, media_type=mime, headers={"Content-Disposition": f'attachment; filename="document.{fmt}"'})

    return app


_provider = OpenAIProvider.from_env() if os.environ.get("P5_PROVIDER") == "openai" else DemoProvider()
_synthetic_path = os.environ.get("P5_SYNTHETIC_DB", ":memory:")
app = create_app(Store(_synthetic_path), _provider)
