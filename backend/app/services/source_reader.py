"""인증된 현재 프로젝트의 추가 자료 조회. 목록과 원문/부분 읽기를 분리한다."""
import asyncio
import datetime as dt
import json
import re

from bson import ObjectId

from app.config import get_settings
from app.schemas.common import to_api
from app.services import ai_context, reference_scope, structured_docs
from app.services.store import utcnow

PAGE_SIZE = 20
READ_CHARS = 20000


class SourceReader:
    def __init__(self, db, project, user_id, scope, used=None):
        self.db, self.project, self.user_id, self.scope = db, project, user_id, scope
        self.used = used if used is not None else []

    def file_query(self):
        choices = [{"project_id": self.project["_id"]}]
        if self.project.get("brand_id"):
            choices.append({"project_id": None, "brand_id": self.project["brand_id"]})
        if self.project.get("file_refs"):
            choices.append({"_id": {"$in": self.project["file_refs"]}})
        return {"user_id": self.user_id, "$or": choices}

    async def active(self):
        current = await self.db.projects.find_one({"_id": self.project["_id"], "user_id": self.user_id,
                                                   "deleted_at": None, "archived_at": None})
        if current:
            self.project = current
        return current is not None

    async def available(self, key, visiting=None):
        if not reference_scope.allows(self.scope, key):
            return False
        prefix, _, ident = key.partition(":")
        query = {"project_id": self.project["_id"]}
        if prefix == "target":
            parts = ident.split(":", 1)
            if len(parts) != 2 or not ObjectId.is_valid(parts[0]):
                return False
            return await self.db.doc_sections.find_one({**query, "doc_id": ObjectId(parts[0]), "section_id": parts[1]}) is not None and await self.db.structured_docs.find_one({"_id": ObjectId(parts[0]), **query, "deleted_at": None}) is not None
        if prefix == "record":
            record = await self.db.items.find_one({**query, "kind": "record", "key": ident})
            if not record:
                return False
            if not record.get("message_id"):
                return True
            if record.get("source_keys") is None:
                return not reference_scope.restricted(self.scope)
            visiting = set(visiting or [])
            if key in visiting:
                return False
            return all([await self.available(dep, visiting | {key}) for dep in record["source_keys"]])
        if prefix in ("product", "contact"):
            from app.services import catalog
            if not ObjectId.is_valid(ident):
                return False
            kind = "products" if prefix == "product" else "contacts"
            return await self.db[kind].find_one({**catalog.query(self.project, self.user_id, kind), "_id": ObjectId(ident)}) is not None
        if prefix in ("doc", "task", "file", "message"):
            if not ObjectId.is_valid(ident):
                return False
            collection = {"doc": self.db.structured_docs, "task": self.db.tasks, "file": self.db.assets, "message": self.db.messages}[prefix]
            condition = {**query, "_id": ObjectId(ident), "deleted_at": None}
            if prefix == "file":
                condition = {"_id": ObjectId(ident), **self.file_query()}
            obj = await collection.find_one(condition)
            if prefix == "message" and obj:
                return bool(obj.get("conversation_id")) and await self.db.conversations.find_one({"_id": obj["conversation_id"], **query, "deleted_at": None}) is not None
            return obj is not None
        if prefix == "ref":
            project_id = ident.split(":", 1)[0]
            return ObjectId.is_valid(project_id) and ObjectId(project_id) in self.project.get("chip_refs", []) and await self.db.projects.find_one({"_id": ObjectId(project_id), "user_id": self.user_id, "deleted_at": None, "archived_at": None}) is not None
        return key in {"project_basic", "linked_brand", "tasks", "economics", "ledger", "project_memo", "reference_document"} or key in {f"ck_{n:02}" for n in range(1, 7)}

    async def search(self, query="", kinds=None, offset=0):
        if not await self.active():
            return {"status": "unavailable", "items": []}
        kinds = set(kinds or self.scope["kinds"]) & set(self.scope["kinds"])
        match = re.compile(re.escape(query), re.I)
        found = []
        pid = self.project["_id"]
        sources = [
            ("record", self.db.items, {"project_id": pid, "kind": "record"}),
            ("document", self.db.structured_docs, {"project_id": pid, "deleted_at": None}),
            ("task", self.db.tasks, {"project_id": pid, "deleted_at": None}),
            ("file", self.db.assets, self.file_query()),
        ]
        from app.services import catalog
        sources += [("product", self.db.products, catalog.query(self.project, self.user_id, "products")),
                    ("contact", self.db.contacts, catalog.query(self.project, self.user_id, "contacts"))]
        basic = reference_scope.filter_sections(await ai_context.build_sections(self.db, self.project, self.user_id), self.scope)
        for key, value in basic.items():
            kind = reference_scope.kind_of(key)
            if kind not in kinds or kind not in ("project", "brand", "plan", "memo", "economics", "ledger"):
                continue
            title = value.get("title") or value.get("name") or key
            if match.search(title + "\n" + str(value.get("content", ""))):
                found.append({"key": key, "kind": kind, "title": title, "revision": value.get("revision")})
        for kind, collection, condition in sources:
            if kind not in kinds:
                continue
            async for obj in collection.find(condition).sort("_id", 1):
                ident = obj.get("key") if kind == "record" else str(obj["_id"])
                key = f"{'doc' if kind == 'document' else kind}:{ident}"
                if not reference_scope.allows(self.scope, key) or (kind == "record" and not await self.available(key)):
                    continue
                title = obj.get("title") or obj.get("name") or obj.get("file_name") or key
                searchable = title + "\n" + str(obj.get("content") or obj.get("note") or "")
                if kind == "document" and query and not match.search(searchable):
                    texts = [s.get("text", "") async for s in self.db.doc_sections.find({"doc_id": obj["_id"]})]
                    searchable += "\n" + "\n".join(texts)
                if not match.search(searchable):
                    continue
                found.append({"key": key, "kind": kind, "title": title, "revision": obj.get("revision")})
        page = found[offset:offset + PAGE_SIZE]
        self.used.extend({**entry, "status": "metadata", "fetched_at": utcnow().isoformat()} for entry in page)
        return {"status": "partial" if offset + PAGE_SIZE < len(found) else ("complete" if page else "empty"),
                "items": page, "next_offset": offset + PAGE_SIZE if offset + PAGE_SIZE < len(found) else None}

    async def read(self, key, offset=0):
        if not await self.active() or not reference_scope.allows(self.scope, key):
            return {"status": "unavailable"}
        kind, _, ident = key.partition(":")
        pid = self.project["_id"]
        obj, content, title, revision = None, "", key, None
        if kind == "message":
            selected = await self.selected_messages()
            obj = selected.get(key)
            if obj:
                content = obj["content"]
        elif kind in ("product", "contact") and ObjectId.is_valid(ident):
            from app.services import catalog
            collection = "products" if kind == "product" else "contacts"
            obj = await self.db[collection].find_one({**catalog.query(self.project, self.user_id, collection), "_id": ObjectId(ident)})
            if obj:
                content = json.dumps(to_api({k: v for k, v in obj.items() if k not in ("user_id", "deleted_at")}), ensure_ascii=False, default=str)
        elif kind == "target":
            parts = ident.split(":", 1)
            if await self.available(key):
                obj = await self.db.doc_sections.find_one({"doc_id": ObjectId(parts[0]), "section_id": parts[1]})
                content = obj.get("text", "")
        elif kind == "record":
            if not await self.available(key):
                return {"status": "unavailable"}
            obj = await self.db.items.find_one({"project_id": pid, "kind": "record", "key": ident})
            if obj:
                content = obj.get("content", "")
        elif ":" not in key or kind == "ref":
            sections = reference_scope.filter_sections(await ai_context.build_sections(self.db, self.project, self.user_id), self.scope)
            if key in sections:
                obj = sections[key]
                content = json.dumps(obj, ensure_ascii=False, default=str)
        elif kind in ("doc", "task", "file") and ObjectId.is_valid(ident):
            condition = {"_id": ObjectId(ident), "project_id": pid}
            if kind == "doc":
                obj = await self.db.structured_docs.find_one({**condition, "deleted_at": None})
                if obj:
                    view = await structured_docs.doc_view(self.db, obj)
                    content = json.dumps([{"id": s["section_id"], "title": s["title"], "text": s["text"], "revision": s["revision"]}
                                          for s in view["sections"]], ensure_ascii=False)
            elif kind == "task":
                obj = await self.db.tasks.find_one({**condition, "deleted_at": None})
                if obj:
                    content = json.dumps(to_api({k: obj.get(k) for k in ("title", "date", "note", "done", "revision", "purpose_ids", "source_doc_id", "source_section_id", "source_conversation_id")}), ensure_ascii=False)
            else:
                obj = await self.db.assets.find_one({"_id": ObjectId(ident), **self.file_query()})
                if obj:
                    if not get_settings().ai_read_attachments:
                        return {"status": "disabled", "key": key, "title": obj.get("file_name")}
                    content = await asyncio.to_thread(ai_context._read_file, obj)
                    if content is None:
                        return {"status": "failed" if obj.get("mime_type") in (*ai_context.TEXT_TYPES, ai_context.DOCX_TYPE) else "unsupported", "key": key, "title": obj.get("file_name")}
        if obj is None:
            return {"status": "unavailable"}
        title = obj.get("title") or obj.get("name") or obj.get("file_name") or key
        revision = obj.get("revision")
        part = content[offset:offset + READ_CHARS]
        next_offset = offset + len(part) if offset + len(part) < len(content) else None
        entry = {"key": key, "kind": reference_scope.kind_of(key), "title": title, "revision": revision,
                 "fetched_at": utcnow().isoformat(), "offset": offset, "length": len(part),
                 "status": "partial" if next_offset is not None else "read"}
        self.used.append(entry)
        return {**entry, "content": part, "next_offset": next_offset}

    async def selected_messages(self):
        result = {}
        for ident in self.scope.get("message_ids", []):
            key = f"message:{ident}"
            if not await self.available(key):
                continue
            message = await self.db.messages.find_one({"_id": ObjectId(ident), "project_id": self.project["_id"]})
            if "source_keys" not in message and reference_scope.restricted(self.scope):
                continue
            if not all([await self.available(dep) for dep in message.get("source_keys", [])]):
                continue
            result[key] = {"kind": "message", "title": "선택한 지난 대화", "content": message["content"], "revision": None}
            self.used.extend(message.get("source_manifest", []))
        return result

    async def tasks(self, date_from=None, date_to=None, done=None, offset=0):
        if not await self.active() or not reference_scope.allows(self.scope, "tasks"):
            return {"status": "unavailable", "items": []}
        query = {"project_id": self.project["_id"], "deleted_at": None}
        if date_from or date_to:
            query["date"] = {}
            for operator, value in (("$gte", date_from), ("$lte", date_to)):
                if value:
                    dt.date.fromisoformat(value)
                    query["date"][operator] = value
        if done is not None:
            query["done"] = done
        rows = []
        async for task in self.db.tasks.find(query).sort([("date", 1), ("_id", 1)]):
            key = f"task:{task['_id']}"
            if reference_scope.allows(self.scope, key):
                rows.append({"key": key, "id": str(task["_id"]), **{k: task.get(k) for k in
                             ("title", "date", "note", "done", "revision", "purpose_ids", "source_doc_id", "source_section_id", "source_conversation_id")}})
        page = rows[offset:offset + PAGE_SIZE]
        self.used.extend({"key": t["key"], "kind": "task", "title": t["title"], "revision": t["revision"],
                          "fetched_at": utcnow().isoformat(), "status": "read"} for t in page)
        return to_api({"status": "partial" if offset + PAGE_SIZE < len(rows) else ("complete" if page else "empty"),
                       "date_basis": "scheduled_date", "timezone": "Asia/Seoul", "items": page,
                       "next_offset": offset + PAGE_SIZE if offset + PAGE_SIZE < len(rows) else None})


def tool(name, description, properties):
    return {"type": "function", "name": name, "description": description,
            "parameters": {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}, "strict": True}


TOOLS = [
    tool("search_sources", "Find saved records/documents/tasks/file metadata in the allowed project. Paginate with offset. Search is not reading the original.",
         {"query": {"type": "string"}, "kinds": {"type": "array", "items": {"type": "string", "enum": ["record", "document", "task", "file", "product", "contact", "project", "brand", "plan", "memo", "economics", "ledger"]}}, "offset": {"type": "integer", "minimum": 0}}),
    tool("read_source", "Read a permitted source by its exact search key. Continue at next_offset if partial. Unavailable/unsupported is not an empty original.",
         {"key": {"type": "string"}, "offset": {"type": "integer", "minimum": 0}}),
    tool("query_tasks", "Read tasks by scheduled date and completion state, not date of completion. Dates are YYYY-MM-DD or null. This never changes tasks.",
         {"date_from": {"type": ["string", "null"]}, "date_to": {"type": ["string", "null"]}, "done": {"type": ["boolean", "null"]}, "offset": {"type": "integer", "minimum": 0}}),
]


async def dispatch(reader, name, args):
    spec = next(t for t in TOOLS if t["name"] == name)
    if not isinstance(args, dict) or set(args) != set(spec["parameters"]["properties"]):
        raise ValueError("invalid arguments")
    if type(args.get("offset")) is not int or not 0 <= args["offset"] <= 1000000:
        raise ValueError("invalid offset")
    if name == "search_sources":
        if not isinstance(args["query"], str) or len(args["query"]) > 500 or not isinstance(args["kinds"], list) or any(k not in ["record", "document", "task", "file", "product", "contact", "project", "brand", "plan", "memo", "economics", "ledger"] for k in args["kinds"]):
            raise ValueError("invalid search")
        return await reader.search(**args)
    if name == "read_source":
        if not isinstance(args["key"], str) or len(args["key"]) > 200:
            raise ValueError("invalid source")
        return await reader.read(**args)
    if args["done"] is not None and type(args["done"]) is not bool:
        raise ValueError("invalid completion")
    return await reader.tasks(**args)

