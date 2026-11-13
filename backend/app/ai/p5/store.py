"""Disposable SQLite owner for the P5 prototype; no existing data is migrated."""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone


def uid() -> str:
    return uuid.uuid4().hex


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class StoreError(Exception):
    pass


class Store:
    def __init__(self, path: str = ":memory:"):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self.db.executescript("""
        PRAGMA foreign_keys=ON;
        CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY, owner TEXT NOT NULL, name TEXT NOT NULL, brand TEXT NOT NULL, deleted INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS ck(project_id TEXT NOT NULL, key TEXT NOT NULL, content TEXT NOT NULL, state TEXT NOT NULL, revision INTEGER NOT NULL, PRIMARY KEY(project_id,key));
        CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, project_id TEXT NOT NULL, title TEXT NOT NULL, active_run TEXT, capsule TEXT, capsule_ids TEXT, capsule_sources TEXT, capsule_rev INTEGER NOT NULL DEFAULT 0, deleted INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS messages(id TEXT PRIMARY KEY, session_id TEXT NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL, source_ids TEXT NOT NULL, ordinal INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS events(session_id TEXT NOT NULL, run_id TEXT NOT NULL, seq INTEGER NOT NULL, kind TEXT NOT NULL, content TEXT NOT NULL, PRIMARY KEY(session_id,run_id,seq));
        CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, session_id TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, error_code TEXT, delivery TEXT);
        CREATE TABLE IF NOT EXISTS files(id TEXT PRIMARY KEY, project_id TEXT NOT NULL, name TEXT NOT NULL, body BLOB NOT NULL, revision INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS attachments(session_id TEXT NOT NULL, file_id TEXT NOT NULL, active INTEGER NOT NULL, encoding TEXT NOT NULL, PRIMARY KEY(session_id,file_id));
        CREATE TABLE IF NOT EXISTS candidates(id TEXT PRIMARY KEY, project_id TEXT NOT NULL, session_id TEXT NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL, manifest TEXT NOT NULL, saved_doc TEXT);
        CREATE TABLE IF NOT EXISTS documents(id TEXT PRIMARY KEY, project_id TEXT NOT NULL, title TEXT NOT NULL, file_id TEXT NOT NULL, revision INTEGER NOT NULL, manifest TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS project_records(project_id TEXT PRIMARY KEY, title TEXT NOT NULL, body TEXT NOT NULL, revision INTEGER NOT NULL);
        CREATE UNIQUE INDEX IF NOT EXISTS one_session_per_project ON sessions(project_id);
        """)
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(runs)")}
        for column in ("error_code", "delivery"):
            if column not in columns:
                self.db.execute(f"ALTER TABLE runs ADD COLUMN {column} TEXT")
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(sessions)")}
        if 'search_enabled' not in columns:
            self.db.execute("ALTER TABLE sessions ADD COLUMN search_enabled INTEGER NOT NULL DEFAULT 0")
        if 'search_epoch' not in columns:
            self.db.execute("ALTER TABLE sessions ADD COLUMN search_epoch INTEGER NOT NULL DEFAULT 0")
        self.db.commit()

    def close(self):
        self.db.close()

    def execute(self, sql, args=()):
        with self.lock, self.db:
            return self.db.execute(sql, args)

    def one(self, sql, args=()):
        with self.lock:
            row = self.db.execute(sql, args).fetchone()
            return dict(row) if row else None

    def all(self, sql, args=()):
        with self.lock:
            return [dict(row) for row in self.db.execute(sql, args).fetchall()]

    def seed(self):
        self.execute("INSERT OR IGNORE INTO projects VALUES (?,?,?,?,0)", ("demo", "demo", "합성 프로젝트", "합성 브랜드"))
        for n in range(1, 7):
            self.execute("INSERT OR IGNORE INTO ck VALUES (?,?,?,?,?)", ("demo", f"ck_0{n}", "", "UNSET", 0))

    def project(self, project_id, owner):
        project = self.one("SELECT * FROM projects WHERE id=? AND owner=? AND deleted=0", (project_id, owner))
        if not project:
            raise StoreError("NOT_FOUND")
        return project

    def session(self, project_id, session_id, owner):
        self.project(project_id, owner)
        session = self.one("SELECT * FROM sessions WHERE id=? AND project_id=? AND deleted=0", (session_id, project_id))
        if not session:
            raise StoreError("NOT_FOUND")
        return session

    def create_session(self, project_id, owner, title):
        self.project(project_id, owner)
        with self.lock, self.db:
            row = self.db.execute("SELECT id,title,active_run FROM sessions WHERE project_id=?", (project_id,)).fetchone()
            if row:
                return dict(row)
            sid = uid()
            self.db.execute("INSERT INTO sessions(id,project_id,title) VALUES(?,?,?)", (sid, project_id, title))
            return {"id": sid, "title": title, "active_run": None}

    def search_policy(self, project_id, sid, owner):
        row = self.session(project_id, sid, owner)
        return {'enabled': bool(row['search_enabled']), 'epoch': row['search_epoch']}

    def set_search_policy(self, project_id, sid, owner, enabled):
        self.session(project_id, sid, owner)
        with self.lock, self.db:
            self.db.execute("UPDATE sessions SET search_enabled=?,search_epoch=search_epoch+1 WHERE id=? AND search_enabled<>?", (int(enabled), sid, int(enabled)))
            row = self.db.execute("SELECT search_enabled,search_epoch,active_run FROM sessions WHERE id=?", (sid,)).fetchone()
        return {'enabled': bool(row['search_enabled']), 'epoch': row['search_epoch'], 'active_run': row['active_run']}

    def begin_run(self, project_id, sid, owner):
        self.session(project_id, sid, owner)
        rid = uid()
        with self.lock, self.db:
            cur = self.db.execute("UPDATE sessions SET active_run=? WHERE id=? AND active_run IS NULL AND deleted=0", (rid, sid))
            if cur.rowcount != 1:
                raise StoreError("RUN_ACTIVE")
            self.db.execute("INSERT INTO runs(id,session_id,status,created_at) VALUES(?,?,?,?)", (rid, sid, "running", now()))
        return rid

    def finish_run(self, sid, rid, status, error_code=None, delivery=None):
        with self.lock, self.db:
            self.db.execute("UPDATE runs SET status=?,error_code=?,delivery=? WHERE id=? AND session_id=?", (status, error_code, delivery, rid, sid))
            self.db.execute("UPDATE sessions SET active_run=NULL WHERE id=? AND active_run=?", (sid, rid))

    def recover_orphans(self):
        # Single-process prototype: startup means no local task can still commit.
        with self.lock, self.db:
            rows = self.db.execute("SELECT r.id,r.session_id,r.status FROM runs r JOIN sessions s ON s.active_run=r.id WHERE r.status IN ('running','unknown')").fetchall()
            for row in rows:
                self.db.execute("UPDATE runs SET status='unknown',error_code=COALESCE(error_code,?),delivery=COALESCE(delivery,'unknown') WHERE id=?", ('PROCESS_RESTART' if row['status'] == 'running' else 'PROVIDER_OUTCOME_UNKNOWN', row['id']))
                self.db.execute("UPDATE sessions SET active_run=NULL WHERE id=? AND active_run=?", (row['session_id'], row['id']))
                seq = self.db.execute("SELECT COALESCE(MAX(seq),0)+1 FROM events WHERE session_id=? AND run_id=?", (row['session_id'], row['id'])).fetchone()[0]
                self.db.execute("INSERT INTO events VALUES(?,?,?,?,?)", (row['session_id'], row['id'], seq, 'outcome_unknown', '이전 요청의 원격 결과는 확인되지 않았습니다. 대화는 계속 사용할 수 있습니다.'))

    def record(self, project_id):
        row = self.one("SELECT title,body,revision FROM project_records WHERE project_id=?", (project_id,))
        return {"exists": True, **row} if row else {"exists": False, "title": "", "body": "", "revision": 0}

    def save_record(self, project_id, title, body, revision):
        with self.lock, self.db:
            row = self.db.execute("SELECT revision FROM project_records WHERE project_id=?", (project_id,)).fetchone()
            if (row[0] if row else 0) != revision:
                raise StoreError("REVISION_CONFLICT")
            if row:
                self.db.execute("UPDATE project_records SET title=?,body=?,revision=revision+1 WHERE project_id=? AND revision=?", (title, body, project_id, revision))
            else:
                self.db.execute("INSERT INTO project_records VALUES (?,?,?,1)", (project_id, title, body))
        return self.record(project_id)

    def planning_export(self, project_id):
        with self.lock:
            rows = self.all("SELECT key,content,state FROM ck WHERE project_id=? ORDER BY key", (project_id,))
            if [x["key"] for x in rows] != [f"ck_0{n}" for n in range(1, 7)]:
                raise StoreError("REQUIRED_DATA_UNAVAILABLE")
            return rows, self.record(project_id)

    def event(self, sid, rid, kind, content):
        with self.lock, self.db:
            seq = self.db.execute("SELECT COALESCE(MAX(seq),0)+1 FROM events WHERE session_id=? AND run_id=?", (sid, rid)).fetchone()[0]
            self.db.execute("INSERT INTO events VALUES(?,?,?,?,?)", (sid, rid, seq, kind, content))
        return seq

    def run_current(self, sid, rid):
        return bool(self.one("SELECT 1 FROM sessions s JOIN projects p ON p.id=s.project_id JOIN runs r ON r.id=s.active_run WHERE s.id=? AND s.active_run=? AND s.deleted=0 AND p.deleted=0 AND r.status='running'", (sid, rid)))

    def message(self, sid, role, content, sources=(), run_id=None):
        with self.lock, self.db:
            if run_id is not None and not self.run_current(sid, run_id):
                raise StoreError("RUN_NOT_ACTIVE")
            n = self.db.execute("SELECT COALESCE(MAX(ordinal),0)+1 FROM messages WHERE session_id=?", (sid,)).fetchone()[0]
            mid = uid()
            self.db.execute("INSERT INTO messages VALUES(?,?,?,?,?,?)", (mid, sid, role, content, json.dumps(list(sources)), n))
        return mid

    def messages(self, sid, allowed_only=False):
        active = {a["file_id"] for a in self.all("SELECT file_id FROM attachments WHERE session_id=? AND active=1", (sid,))}
        result = self.all("SELECT * FROM messages WHERE session_id=? ORDER BY ordinal", (sid,))
        for item in result:
            item["source_ids"] = json.loads(item["source_ids"])
        return [m for m in result if not allowed_only or set(m["source_ids"]) <= active]

    def save_capsule(self, sid, expected_rev, text, ids, sources, run_id):
        cur = self.execute("UPDATE sessions SET capsule=?, capsule_ids=?, capsule_sources=?, capsule_rev=capsule_rev+1 WHERE id=? AND capsule_rev=? AND active_run=? AND deleted=0 AND EXISTS (SELECT 1 FROM projects p WHERE p.id=sessions.project_id AND p.deleted=0)", (text, json.dumps(ids), json.dumps(sources), sid, expected_rev, run_id))
        if cur.rowcount != 1:
            raise StoreError("CAPSULE_CONFLICT")

    def snapshot(self, project_id):
        p = self.one("SELECT id,name,brand FROM projects WHERE id=? AND deleted=0", (project_id,))
        if not p:
            raise StoreError("NOT_FOUND")
        ck = self.all("SELECT key,content,state,revision FROM ck WHERE project_id=? ORDER BY key", (project_id,))
        return [{"type": "project", **p}, *[{"type": "ck", **c} for c in ck]]

    def update_ck(self, project_id, owner, key, content, state, revision):
        self.project(project_id, owner)
        if state not in ("DRAFT", "DECIDED"):
            raise StoreError("INVALID_STATE")
        cur = self.execute("UPDATE ck SET content=?,state=?,revision=revision+1 WHERE project_id=? AND key=? AND revision=?", (content, state, project_id, key, revision))
        if cur.rowcount != 1:
            raise StoreError("REVISION_CONFLICT")
        return self.one("SELECT * FROM ck WHERE project_id=? AND key=?", (project_id, key))

    def attachment(self, project_id, sid, owner, name, body, encoding):
        self.session(project_id, sid, owner)
        file_id = uid()
        with self.lock, self.db:
            self.db.execute("INSERT INTO files VALUES(?,?,?,?,1)", (file_id, project_id, name, body))
            self.db.execute("INSERT INTO attachments VALUES(?,?,1,?)", (sid, file_id, encoding))
        return file_id

    def active_files(self, sid):
        return self.all("SELECT f.id,f.name,f.body,f.revision,a.encoding FROM files f JOIN attachments a ON a.file_id=f.id WHERE a.session_id=? AND a.active=1", (sid,))

    def remove_attachment(self, sid, file_id):
        cur = self.execute("UPDATE attachments SET active=0 WHERE session_id=? AND file_id=? AND active=1", (sid, file_id))
        if not cur.rowcount:
            raise StoreError("NOT_FOUND")

    def candidate(self, project_id, sid, title, body, manifest, run_id=None):
        with self.lock, self.db:
            if run_id is not None and not self.run_current(sid, run_id):
                raise StoreError("RUN_NOT_ACTIVE")
            ident = uid()
            self.db.execute("INSERT INTO candidates VALUES(?,?,?,?,?,?,NULL)", (ident, project_id, sid, title, body, json.dumps(manifest, ensure_ascii=False)))
            return ident

    def save_candidate(self, project_id, sid, cid, title=None, body=None):
        with self.lock, self.db:
            c = self.db.execute("SELECT * FROM candidates WHERE id=? AND project_id=? AND session_id=?", (cid, project_id, sid)).fetchone()
            if not c:
                raise StoreError("NOT_FOUND")
            if c["saved_doc"]:
                return c["saved_doc"]
            if not self.db.execute("SELECT 1 FROM projects WHERE id=? AND deleted=0", (project_id,)).fetchone():
                raise StoreError("NOT_FOUND")
            final_title = c["title"] if title is None else title
            final_body = c["body"] if body is None else body
            doc_id, file_id = uid(), uid()
            self.db.execute("INSERT INTO files VALUES(?,?,?,?,1)", (file_id, project_id, final_title + ".md", final_body.encode("utf-8")))
            self.db.execute("INSERT INTO documents VALUES(?,?,?,?,1,?)", (doc_id, project_id, final_title, file_id, c["manifest"]))
            self.db.execute("UPDATE candidates SET saved_doc=? WHERE id=?", (doc_id, cid))
            return doc_id

    def document(self, project_id, doc_id):
        d = self.one("SELECT d.*,f.body FROM documents d JOIN files f ON f.id=d.file_id WHERE d.project_id=? AND d.id=?", (project_id, doc_id))
        if not d:
            raise StoreError("NOT_FOUND")
        d["body"] = d["body"].decode("utf-8")
        d["manifest"] = json.loads(d["manifest"])
        return d

    def save_document(self, project_id, doc_id, revision, title, body):
        with self.lock, self.db:
            d = self.document(project_id, doc_id)
            cur = self.db.execute("UPDATE documents SET title=?,revision=revision+1 WHERE id=? AND project_id=? AND revision=?", (title, doc_id, project_id, revision))
            if cur.rowcount != 1:
                raise StoreError("REVISION_CONFLICT")
            self.db.execute("UPDATE files SET body=?,name=?,revision=revision+1 WHERE id=?", (body.encode("utf-8"), title + ".md", d["file_id"]))
        return self.document(project_id, doc_id)

    def delete_document(self, project_id, doc_id, revision):
        with self.lock, self.db:
            d = self.document(project_id, doc_id)
            cur = self.db.execute("DELETE FROM documents WHERE id=? AND project_id=? AND revision=?", (doc_id, project_id, revision))
            if cur.rowcount != 1:
                raise StoreError("REVISION_CONFLICT")
            self.db.execute("DELETE FROM files WHERE id=?", (d["file_id"],))

    def document_changes(self, document):
        changes = []
        for ref in document["manifest"]:
            typ = ref.get("type")
            if typ == "ck":
                actual = self.one("SELECT revision FROM ck WHERE project_id=? AND key=?", (document["project_id"], ref["id"]))
            elif typ == "attachment":
                actual = self.one("SELECT f.revision FROM files f JOIN attachments a ON a.file_id=f.id WHERE f.id=? AND f.project_id=? AND a.session_id=? AND a.active=1", (ref["id"], document["project_id"], ref["session_id"]))
            elif typ == "document":
                actual = None if ref.get("source") == "editor" else self.one("SELECT revision FROM documents WHERE project_id=? AND id=?", (document["project_id"], ref["id"]))
            elif typ == "record":
                actual = self.one("SELECT revision FROM project_records WHERE project_id=?", (document["project_id"],))
            elif typ == "session":
                session = self.one("SELECT id FROM sessions WHERE id=? AND project_id=? AND deleted=0", (ref["id"], document["project_id"]))
                if session and "message_refs" in ref:
                    refs = ref["message_refs"]
                    rows = [self.one("SELECT ordinal FROM messages WHERE session_id=? AND id=?", (ref["id"], m["id"])) for m in refs]
                    state = "unavailable" if any(row is None for row in rows) else ("changed" if any(row["ordinal"] != m["ordinal"] for row, m in zip(rows, refs)) else "unchanged")
                    changes.append({"id": ref["id"], "status": state})
                    continue
                actual = None
            else:
                actual = None
            changes.append({"id": ref["id"], "status": "unavailable" if actual is None or ref.get("revision") is None else ("changed" if actual["revision"] != ref["revision"] else "unchanged")})
        return changes
