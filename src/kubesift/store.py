"""Storage: investigation history, team notes ("what fixed it last time"), audit log, alert dedupe.

SQLite (a file in data_dir) by default. Set database_url=postgresql://... to share state between
several server replicas (pip install "kubesift[postgres]").
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from kubesift.models import Finding, Report
from kubesift.util import now

_TABLES = """
CREATE TABLE IF NOT EXISTS reports (
  id TEXT PRIMARY KEY, created TEXT NOT NULL, cluster TEXT, namespace TEXT, question TEXT,
  source TEXT, counts TEXT, summary TEXT, body TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS reports_created ON reports(created DESC);
CREATE TABLE IF NOT EXISTS notes (
  id {serial}, created TEXT NOT NULL, check_id TEXT NOT NULL, target TEXT, note TEXT NOT NULL, author TEXT);
CREATE INDEX IF NOT EXISTS notes_check ON notes(check_id);
CREATE TABLE IF NOT EXISTS audit (
  id {serial}, created TEXT NOT NULL, actor TEXT, action TEXT NOT NULL, target TEXT NOT NULL,
  dry_run INTEGER NOT NULL, result TEXT);
CREATE TABLE IF NOT EXISTS seen (
  cluster TEXT NOT NULL, fingerprint TEXT NOT NULL, first_seen TEXT, last_seen TEXT,
  PRIMARY KEY (cluster, fingerprint));
CREATE TABLE IF NOT EXISTS claims (key TEXT PRIMARY KEY, until DOUBLE PRECISION NOT NULL);
CREATE TABLE IF NOT EXISTS digests (
  id {serial}, created TEXT NOT NULL, cluster TEXT NOT NULL, namespace TEXT, fingerprint TEXT, body TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS digests_cluster_created ON digests(cluster, created)
"""


class Store:
    def __init__(self, data_dir: str, database_url: str | None = None):
        self._lock = threading.Lock()
        self._saves = 0
        self._url = database_url
        self.postgres = bool(database_url and database_url.startswith(("postgres://", "postgresql://")))
        if self.postgres:
            self._connect_pg()
            serial = "BIGSERIAL PRIMARY KEY"
        else:
            path = Path(data_dir).expanduser()
            path.mkdir(parents=True, exist_ok=True)
            self.path = path / "kubesift.db"
            self._db: Any = sqlite3.connect(self.path, check_same_thread=False)
            self._db.row_factory = sqlite3.Row
            self._db.execute("PRAGMA journal_mode=WAL")  # readers don't block the background scanner
            self._db.execute("PRAGMA busy_timeout=5000")
            serial = "INTEGER PRIMARY KEY AUTOINCREMENT"
        for stmt in _TABLES.format(serial=serial).split(";"):
            if stmt.strip():
                self._exec(stmt)

    def _connect_pg(self) -> None:
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise RuntimeError('PostgreSQL support needs: pip install "kubesift[postgres]"') from exc
        self._db = psycopg.connect(self._url, autocommit=True, row_factory=dict_row, connect_timeout=10)

    def _exec(self, sql: str, params: tuple = ()) -> Any:
        with self._lock:
            if not self.postgres:
                cur = self._db.execute(sql, params)
                self._db.commit()
                return cur
            sql = sql.replace("?", "%s")
            try:
                return self._db.execute(sql, params)
            except Exception as exc:  # reconnect once after a dropped connection (failover, idle timeout)
                import psycopg
                if not isinstance(exc, psycopg.OperationalError) and not self._db.closed:
                    raise
                self._connect_pg()
                return self._db.execute(sql, params)

    # reports
    def prune(self, days: int) -> int:
        """Delete stored investigations older than `days`. Notes and the audit log are kept."""
        cutoff = (now() - timedelta(days=days)).isoformat()
        self._exec("DELETE FROM digests WHERE created < ?", (cutoff,))
        return self._exec("DELETE FROM reports WHERE created < ?", (cutoff,)).rowcount

    def save_report(self, report: Report, source: str = "cli", retention_days: int = 30) -> str:
        self._saves += 1
        if self._saves % 50 == 1:
            self.prune(retention_days)
        rid = uuid.uuid4().hex[:10]
        summary = report.diagnosis.summary if report.diagnosis else ""
        self._exec("INSERT INTO reports (id, created, cluster, namespace, question, source, counts, summary, body) "
                   "VALUES (?,?,?,?,?,?,?,?,?)",
                   (rid, now().isoformat(), report.cluster, report.namespace, report.question, source,
                    json.dumps(report.counts()), summary, report.model_dump_json()))
        return rid

    def update_report(self, rid: str, report: Report) -> None:
        summary = report.diagnosis.summary if report.diagnosis else ""
        self._exec("UPDATE reports SET summary = ?, body = ? WHERE id = ?", (summary, report.model_dump_json(), rid))

    def list_reports(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self._exec("SELECT id, created, cluster, namespace, question, source, counts, summary FROM reports "
                          "ORDER BY created DESC LIMIT ?", (limit,)).fetchall()
        return [{**dict(r), "counts": json.loads(r["counts"] or "{}")} for r in rows]

    def get_report(self, rid: str) -> Report | None:
        row = self._exec("SELECT body FROM reports WHERE id = ?", (rid,)).fetchone()
        return Report.model_validate_json(row["body"]) if row else None

    # notes
    def add_note(self, check_id: str, note: str, target: str | None = None, author: str | None = None) -> int:
        sql = "INSERT INTO notes (created, check_id, target, note, author) VALUES (?,?,?,?,?)"
        params = (now().isoformat(), check_id, target, note, author)
        if self.postgres:
            row = self._exec(sql + " RETURNING id", params).fetchone()
            return int(row["id"]) if row else 0
        return int(self._exec(sql, params).lastrowid or 0)

    def list_notes(self, check_id: str | None = None) -> list[dict[str, Any]]:
        if check_id:
            rows = self._exec("SELECT * FROM notes WHERE check_id = ? ORDER BY created DESC", (check_id,)).fetchall()
        else:
            rows = self._exec("SELECT * FROM notes ORDER BY created DESC").fetchall()
        return [dict(r) for r in rows]

    def attach_notes(self, findings: list[Finding]) -> None:
        notes = self.list_notes()
        for f in findings:
            owner = str(f.owner or f.resource)
            for n in notes:
                if n["check_id"] != f.check:
                    continue
                if n["target"] and n["target"] not in owner:
                    continue
                f.notes.append(f"{n['created'][:10]}{' ' + n['author'] if n['author'] else ''}: {n['note']}")

    # audit
    def audit(self, action: str, target: str, dry_run: bool, result: str, actor: str | None = None) -> None:
        self._exec("INSERT INTO audit (created, actor, action, target, dry_run, result) VALUES (?,?,?,?,?,?)",
                   (now().isoformat(), actor, action, target, int(dry_run), result[:2000]))

    def list_audit(self, limit: int = 50) -> list[dict[str, Any]]:
        return [dict(r) for r in self._exec("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]

    # time travel
    def save_digest(self, d: dict[str, Any]) -> bool:
        """Store a cluster digest unless nothing changed since the last one. Returns True if stored."""
        import base64
        import gzip
        import hashlib

        items = json.dumps(d.get("items"), sort_keys=True, default=str)
        fp = hashlib.sha1(items.encode()).hexdigest()
        last = self._exec("SELECT fingerprint FROM digests WHERE cluster = ? ORDER BY created DESC LIMIT 1",
                          (d.get("cluster"),)).fetchone()
        if last and last["fingerprint"] == fp:
            return False
        body = base64.b64encode(gzip.compress(json.dumps(d, default=str).encode())).decode()
        self._exec("INSERT INTO digests (created, cluster, namespace, fingerprint, body) VALUES (?,?,?,?,?)",
                   (d.get("time") or now().isoformat(), d.get("cluster"), d.get("namespace"), fp, body))
        return True

    def digest_before(self, cluster: str, when: datetime) -> dict[str, Any] | None:
        """The latest stored digest taken at or before `when`."""
        import base64
        import gzip

        row = self._exec("SELECT body FROM digests WHERE cluster = ? AND created <= ? ORDER BY created DESC LIMIT 1",
                         (cluster, when.isoformat())).fetchone()
        return json.loads(gzip.decompress(base64.b64decode(row["body"]))) if row else None

    def list_digests(self, cluster: str, limit: int = 50) -> list[dict[str, Any]]:
        rows = self._exec("SELECT id, created, namespace FROM digests WHERE cluster = ? ORDER BY created DESC LIMIT ?",
                          (cluster, limit)).fetchall()
        return [dict(r) for r in rows]

    # coordination between replicas
    def claim(self, key: str, seconds: float) -> bool:
        """Atomically claim `key` for `seconds`. False if another caller (or replica) holds a live claim."""
        t = time.time()
        cur = self._exec(
            "INSERT INTO claims (key, until) VALUES (?, ?) "
            "ON CONFLICT (key) DO UPDATE SET until = excluded.until WHERE claims.until < ?",
            (key, t + seconds, t))
        return cur.rowcount == 1

    def new_findings(self, cluster: str, findings: list[Finding]) -> list[Finding]:
        """Return findings not notified before, and remember them. Findings that disappear are forgotten."""
        ts = now().isoformat()
        current = {f.fingerprint: f for f in findings}
        known = {r["fingerprint"] for r in self._exec("SELECT fingerprint FROM seen WHERE cluster = ?",
                                                      (cluster,)).fetchall()}
        fresh = []
        for fp, f in current.items():
            if fp in known:
                self._exec("UPDATE seen SET last_seen = ? WHERE cluster = ? AND fingerprint = ?", (ts, cluster, fp))
            elif self._exec("INSERT INTO seen (cluster, fingerprint, first_seen, last_seen) VALUES (?,?,?,?) "
                            "ON CONFLICT DO NOTHING", (cluster, fp, ts, ts)).rowcount == 1:
                fresh.append(f)
        for fp in known - set(current):
            self._exec("DELETE FROM seen WHERE cluster = ? AND fingerprint = ?", (cluster, fp))
        return fresh


def open_store(settings: Any) -> Store:
    return Store(settings.data_dir, getattr(settings, "database_url", None))


def parse_created(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None
