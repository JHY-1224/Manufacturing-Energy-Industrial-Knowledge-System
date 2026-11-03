from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class KnowledgeConflictError(ValueError):
    """A stale writer must reload the current document before trying again."""


class KnowledgeStore:
    """SQLite commits document metadata, current chunks and revision in one transaction.

    Raw assets and previous revisions are never removed by index/document deletion.
    Each connection is short-lived; SQLite serializes writes across processes.
    """

    def __init__(self, directory: str | Path):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.asset_dir = self.directory / "originals"
        self.asset_dir.mkdir(exist_ok=True)
        self.path = self.directory / "knowledge.sqlite3"
        with self.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                INSERT OR IGNORE INTO meta VALUES ('generation', '0');
                CREATE TABLE IF NOT EXISTS documents (
                    document_id TEXT PRIMARY KEY, source_key TEXT UNIQUE NOT NULL,
                    payload TEXT NOT NULL, deleted INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS chunks (
                    chunk_id TEXT PRIMARY KEY, document_id TEXT NOT NULL,
                    payload TEXT NOT NULL, FOREIGN KEY(document_id) REFERENCES documents(document_id));
                CREATE INDEX IF NOT EXISTS chunks_document ON chunks(document_id);
                CREATE TABLE IF NOT EXISTS revisions (
                    revision_id TEXT PRIMARY KEY, document_id TEXT NOT NULL,
                    payload TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY, document_id TEXT NOT NULL,
                    status TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS traces (trace_id TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS vectors (cache_key TEXT PRIMARY KEY, payload TEXT NOT NULL);
            """)
            from app.storage.workbench_store import initialize_workbench
            initialize_workbench(db)
            db.execute("BEGIN IMMEDIATE")
            for row in db.execute("SELECT document_id,payload FROM documents").fetchall():
                payload = json.loads(row["payload"])
                if not payload.get("write_version"):
                    payload["write_version"] = uuid4().hex
                    db.execute("UPDATE documents SET payload=? WHERE document_id=?", (self.encode(payload), row["document_id"]))
            # Additive migration of current records. Historical revision payloads
            # and original assets remain immutable; CAS writers must reload.
            if not db.execute("SELECT 1 FROM meta WHERE key='workbench_schema_v1'").fetchone():
                changed=False
                for row in db.execute('SELECT document_id,payload FROM documents WHERE deleted=0').fetchall():
                    document=json.loads(row['payload'])
                    chunks=[json.loads(c[0]) for c in db.execute('SELECT payload FROM chunks WHERE document_id=?',(row['document_id'],))]
                    kb=document.get('knowledge_base_id') or ('electrical' if document.get('domain') in {'calc_logic','电气工程基础'} else 'energy')
                    document['knowledge_base_id']=kb
                    document['write_version']=uuid4().hex
                    for chunk in chunks:
                        chunk['metadata'].update(document_id=row['document_id'],knowledge_base_id=kb)
                    if chunks:
                        self._replace_sections(db,document,chunks)
                        for chunk in chunks:
                            db.execute('UPDATE chunks SET payload=? WHERE chunk_id=?',(self.encode(chunk),chunk['chunk_id']))
                    db.execute('UPDATE documents SET payload=? WHERE document_id=?',(self.encode(document),row['document_id']))
                    changed=True
                if changed:self.bump(db)
                db.execute("INSERT INTO meta VALUES ('workbench_schema_v1','1')")
            self._recover_interrupted_jobs(db)
            from app.storage.workbench_store import WorkbenchStore
            WorkbenchStore(self).recover_interrupted(db)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def encode(payload) -> str:
        return json.dumps(payload, ensure_ascii=False, allow_nan=False)

    def get_meta(self, key: str) -> str | None:
        with self.connection() as db:
            row = db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
            return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self.connection() as db:
            db.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (key, value))

    @staticmethod
    def bump(db):
        db.execute("UPDATE meta SET value=CAST(value AS INTEGER)+1 WHERE key='generation'")

    def save_asset(self, content: bytes, filename: str) -> tuple[str, str]:
        sha = digest(content)
        if self.get_meta('retired_source_sha256:'+sha):
            raise KnowledgeConflictError('This original was permanently withdrawn; use a repaired source file')
        suffix = Path(filename).suffix.lower()
        if not suffix.isascii() or not suffix[1:].isalnum():
            suffix = ".bin"
        path = self.asset_dir / f"{sha}{suffix}"
        if path.exists():
            if digest(path.read_bytes()) != sha:
                raise ValueError("Stored original failed SHA256 verification")
            return sha, str(path)
        # Never expose a partial final asset. Staging files are retained under
        # the project's no-cleanup policy; a successful publish shares its inode.
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="wb", dir=self.asset_dir, prefix=".staging-", delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            if digest(temporary.read_bytes()) != sha:
                raise ValueError("Staged original failed SHA256 verification")
            try:
                os.link(temporary, path)
            except FileExistsError:
                if digest(path.read_bytes()) != sha:
                    raise ValueError("Stored original failed SHA256 verification")
            if os.name == "posix":
                descriptor = os.open(self.asset_dir, os.O_RDONLY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
        except Exception:
            # A retained staging file is diagnostic evidence, never a source asset.
            raise
        return sha, str(path)

    def find_source(self, source_key: str, include_deleted=False) -> dict | None:
        with self.connection() as db:
            row = db.execute("SELECT payload,deleted FROM documents WHERE source_key=?", (source_key,)).fetchone()
            if not row or (row["deleted"] and not include_deleted):
                return None
            return json.loads(row["payload"])

    def get_document(self, document_id: str) -> dict | None:
        with self.connection() as db:
            row = db.execute("SELECT payload FROM documents WHERE document_id=? AND deleted=0", (document_id,)).fetchone()
            return json.loads(row[0]) if row else None

    def documents(self) -> list[dict]:
        with self.connection() as db:
            return [json.loads(row[0]) for row in db.execute("SELECT payload FROM documents WHERE deleted=0 ORDER BY rowid DESC")]

    def register(self, source_key: str, payload: dict) -> dict:
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if payload.get('source_sha256') and db.execute("SELECT 1 FROM meta WHERE key=?",('retired_source_sha256:'+payload['source_sha256'],)).fetchone():
                raise KnowledgeConflictError('This original was permanently withdrawn; use a repaired source file')
            existing = db.execute("SELECT payload,deleted FROM documents WHERE source_key=?", (source_key,)).fetchone()
            if existing and not existing["deleted"]:
                return json.loads(existing["payload"])
            payload = {**payload, "source_key": source_key, "write_version": uuid4().hex}
            if existing:
                payload["document_id"] = json.loads(existing["payload"])["document_id"]
                db.execute("UPDATE documents SET payload=?,deleted=0 WHERE source_key=?", (self.encode(payload), source_key))
            else:
                db.execute("INSERT INTO documents VALUES (?,?,?,0)", (payload["document_id"], source_key, self.encode(payload)))
            self.bump(db)
        return payload

    def snapshot(self) -> tuple[int, list[dict]]:
        with self.connection() as db:
            db.execute("BEGIN")
            generation = int(db.execute("SELECT value FROM meta WHERE key='generation'").fetchone()[0])
            chunks = [json.loads(row[0]) for row in db.execute("SELECT payload FROM chunks ORDER BY rowid")]
        return generation, chunks

    def replace_document(self, document: dict, chunks: list[dict], config: dict, job_id: str | None = None, *, expected_version: str | None = None) -> None:
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            current = self._check_version(db, document["document_id"], expected_version or document.get("write_version"))
            if job_id:
                job = db.execute("SELECT document_id,status,payload FROM jobs WHERE job_id=?", (job_id,)).fetchone()
                if (not job or job["document_id"] != document["document_id"] or job["status"] != "running"
                        or json.loads(job["payload"]).get("expected_version") != current["write_version"]):
                    raise KnowledgeConflictError("Processing job is no longer current")
            document = {**document, "source_key": current["source_key"], "write_version": uuid4().hex}
            sections = self._replace_sections(db, document, chunks)
            db.execute("DELETE FROM vector_records WHERE document_id=?", (document["document_id"],))
            db.execute("DELETE FROM chunks WHERE document_id=?", (document["document_id"],))
            db.executemany("INSERT INTO chunks VALUES (?,?,?)", [(chunk["chunk_id"], document["document_id"], self.encode(chunk)) for chunk in chunks])
            revision = {"document": document, "sections": sections, "chunks": chunks, "config": config}
            db.execute("INSERT INTO revisions VALUES (?,?,?,?)", (document["revision_id"], document["document_id"], self.encode(revision), now()))
            db.execute("UPDATE documents SET payload=? WHERE document_id=?", (self.encode(document), document["document_id"]))
            if job_id:
                self._finish_job(db, job_id, "completed", None)
            self.bump(db)

    def _replace_sections(self, db, document, chunks):
        from app.preprocessors.section_builder import section_records
        sections = section_records(document, chunks)
        db.execute('DELETE FROM sections WHERE document_id=?', (document['document_id'],))
        db.executemany('INSERT INTO sections VALUES (?,?,?,?)', [(s['section_id'], document['document_id'], document['revision_id'], self.encode(s)) for s in sections])
        return sections

    def _check_version(self, db, document_id, expected_version):
        row = db.execute("SELECT payload,deleted FROM documents WHERE document_id=?", (document_id,)).fetchone()
        if not row or row["deleted"]:
            raise KnowledgeConflictError("Document was removed during processing")
        current = json.loads(row["payload"])
        if not expected_version or current.get("write_version") != expected_version:
            raise KnowledgeConflictError("Document changed during processing; reload before retrying")
        return current

    @staticmethod
    def _owner_is_alive(pid):
        if type(pid) is not int or not 0 < pid <= 0xffffffff:
            return True  # Legacy/unknown owners cannot safely be declared dead.
        if os.name == "nt":
            # os.kill(pid, 0) is not a safe liveness probe on Windows.
            import ctypes
            from ctypes import wintypes
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel.OpenProcess.restype = wintypes.HANDLE
            kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
            kernel.GetExitCodeProcess.restype = wintypes.BOOL
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel.CloseHandle.restype = wintypes.BOOL
            handle = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
            if not handle:
                return ctypes.get_last_error() != 87  # Only invalid PID establishes absence.
            try:
                exit_code = wintypes.DWORD()
                if not kernel.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                    return True
                return exit_code.value == 259  # STILL_ACTIVE
            finally:
                kernel.CloseHandle(handle)
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except (PermissionError, OSError, OverflowError):
            return True

    def _recover_interrupted_jobs(self, db):
        for row in db.execute("SELECT job_id,payload FROM jobs WHERE status='running'").fetchall():
            if not self._owner_is_alive(json.loads(row["payload"]).get("owner_pid")):
                self._finish_job(db, row["job_id"], "interrupted", "Owner process exited; previous document revision retained")

    def start_job(self, document_id: str, config: dict, *, expected_version: str | None = None) -> str:
        job_id = f"job_{uuid4().hex}"
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self._recover_interrupted_jobs(db)
            if expected_version is None:
                row = db.execute("SELECT payload FROM documents WHERE document_id=?", (document_id,)).fetchone()
                expected_version = json.loads(row[0]).get("write_version") if row else None
            self._check_version(db, document_id, expected_version)
            if db.execute("SELECT 1 FROM jobs WHERE document_id=? AND status='running'", (document_id,)).fetchone():
                raise KnowledgeConflictError("Document already has a running processing job")
            payload = {"job_id": job_id, "document_id": document_id, "status": "running", "step": "parse -> chunk -> index", "progress": 0, "started_at": now(), "finished_at": None, "config": config,
                       "owner_pid": os.getpid(), "expected_version": expected_version}
            db.execute("INSERT INTO jobs VALUES (?,?,?,?)", (job_id, document_id, "running", self.encode(payload)))
        return job_id

    def _finish_job(self, db, job_id, status, error):
        row = db.execute("SELECT payload,status FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        if row and row["status"] == "running":
            payload = {**json.loads(row[0]), "status": status, "finished_at": now(), "progress": 100 if status == "completed" else 0, "logs": error or "Current document revision indexed"}
            db.execute("UPDATE jobs SET status=?,payload=? WHERE job_id=? AND status='running'", (status, self.encode(payload), job_id))

    def finish_job(self, job_id: str, status: str, error: str | None = None):
        with self.connection() as db:
            self._finish_job(db, job_id, status, error)

    def jobs(self) -> list[dict]:
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self._recover_interrupted_jobs(db)
            return [json.loads(row[0]) for row in db.execute("SELECT payload FROM jobs ORDER BY rowid DESC LIMIT 100")]

    def remove_document(self, document_id: str) -> int:
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            count = db.execute("SELECT COUNT(*) FROM chunks WHERE document_id=?", (document_id,)).fetchone()[0]
            db.execute('DELETE FROM sections WHERE document_id=?',(document_id,))
            db.execute('DELETE FROM vector_records WHERE document_id=?',(document_id,))
            db.execute("DELETE FROM chunks WHERE document_id=?", (document_id,))
            row = db.execute("SELECT payload FROM documents WHERE document_id=?", (document_id,)).fetchone()
            if row:
                document = {**json.loads(row[0]), "write_version": uuid4().hex}
                db.execute("UPDATE documents SET deleted=1,payload=? WHERE document_id=?", (self.encode(document), document_id))
            for job in db.execute("SELECT job_id FROM jobs WHERE document_id=? AND status='running'", (document_id,)).fetchall():
                self._finish_job(db, job["job_id"], "cancelled", "Document removed; original and historical revisions retained")
            self.bump(db)
        return count

    def save_trace(self, trace: dict):
        with self.connection() as db:
            db.execute("INSERT INTO traces VALUES (?,?)", (trace["trace_id"], self.encode(trace)))

    def get_trace(self, trace_id: str) -> dict | None:
        with self.connection() as db:
            row = db.execute("SELECT payload FROM traces WHERE trace_id=?", (trace_id,)).fetchone()
            return json.loads(row[0]) if row else None

    def trace_count(self) -> int:
        with self.connection() as db:
            return db.execute("SELECT COUNT(*) FROM traces").fetchone()[0]

    def bootstrap_documents(self, records: list[tuple[dict, list[dict]]]) -> bool:
        """Commit all demo seeds and the marker together; never revive prior data."""
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM meta WHERE key='initialized'").fetchone():
                return False
            inserted = False
            if not db.execute("SELECT 1 FROM documents LIMIT 1").fetchone():
                for original, source_chunks in records:
                    document = {**original, "write_version": uuid4().hex, "data_source": "demo",
                                "revision_id": original.get("revision_id") or "rev_" + uuid4().hex,
                                "source_key": original.get("source_key") or "seed:" + original["document_id"],
                                "status": "indexed", "chunk_count": len(source_chunks), "updated_at": now()}
                    chunks = [{**chunk, "doc_id": document["document_id"],
                               "metadata": {**chunk.get("metadata", {}), "revision_id": document["revision_id"], "data_source": "demo"}}
                              for chunk in source_chunks]
                    db.execute("INSERT INTO documents VALUES (?,?,?,0)", (document["document_id"], document["source_key"], self.encode(document)))
                    self._replace_sections(db, document, chunks)
                    db.executemany("INSERT INTO chunks VALUES (?,?,?)", [(chunk["chunk_id"], document["document_id"], self.encode(chunk)) for chunk in chunks])
                    revision = {"document": document, "chunks": chunks, "config": {"source": "bootstrap"}}
                    db.execute("INSERT INTO revisions VALUES (?,?,?,?)", (document["revision_id"], document["document_id"], self.encode(revision), now()))
                    inserted = True
                if inserted:
                    self.bump(db)
            db.execute("INSERT INTO meta VALUES ('initialized','1')")
            return inserted

    def get_vector(self, key: str):
        with self.connection() as db:
            row = db.execute("SELECT payload FROM vectors WHERE cache_key=?", (key,)).fetchone()
            return json.loads(row[0]) if row else None

    def save_vector(self, key: str, vector):
        with self.connection() as db:
            db.execute("INSERT OR IGNORE INTO vectors VALUES (?,?)", (key, self.encode(vector)))

    def get_vectors(self,keys):
        keys=list(dict.fromkeys(keys));result={}
        with self.connection() as db:
            for start in range(0,len(keys),400):
                batch=keys[start:start+400]
                for row in db.execute('SELECT cache_key,payload FROM vectors WHERE cache_key IN ('+','.join('?' for _ in batch)+')',batch):
                    result[row[0]]=json.loads(row[1])
        return result
