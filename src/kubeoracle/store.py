"""Local SQLite store: investigation history, team notes ("what fixed it last time"), audit log, alert dedupe."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from kubeoracle.models import Finding, Report
from kubeoracle.util import now

_SCHEMA = """
CREATE TABLE IF NOT EXISTS reports (
  id TEXT PRIMARY KEY, created TEXT NOT NULL, cluster TEXT, namespace TEXT, question TEXT,
  source TEXT, counts TEXT, summary TEXT, body TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS reports_created ON reports(created DESC);
CREATE TABLE IF NOT EXISTS notes (
  id INTEGER PRIMARY KEY AUTOINCREMENT, created TEXT NOT NULL, check_id TEXT NOT NULL,
  target TEXT, note TEXT NOT NULL, author TEXT);
CREATE INDEX IF NOT EXISTS notes_check ON notes(check_id);
CREATE TABLE IF NOT EXISTS audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT, created TEXT NOT NULL, actor TEXT, action TEXT NOT NULL,
  target TEXT NOT NULL, dry_run INTEGER NOT NULL, result TEXT);
CREATE TABLE IF NOT EXISTS seen (
  fingerprint TEXT PRIMARY KEY, cluster TEXT, first_seen TEXT, last_seen TEXT, notified INTEGER DEFAULT 0);
"""


class Store:
    def __init__(self, data_dir: str):
        path = Path(data_dir).expanduser()
        path.mkdir(parents=True, exist_ok=True)
        self.path = path / "kubeoracle.db"
        self._lock = threading.Lock()
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")  # readers don't block the background scanner
        self._db.execute("PRAGMA busy_timeout=5000")
        self._db.executescript(_SCHEMA)
        self._saves = 0

    def _exec(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._db.execute(sql, params)
            self._db.commit()
            return cur

    # reports
    def prune(self, days: int) -> int:
        """Delete stored investigations older than `days`. Notes and the audit log are kept."""
        cutoff = (now() - timedelta(days=days)).isoformat()
        return self._exec("DELETE FROM reports WHERE created < ?", (cutoff,)).rowcount

    def save_report(self, report: Report, source: str = "cli", retention_days: int = 30) -> str:
        self._saves += 1
        if self._saves % 50 == 1:
            self.prune(retention_days)
        rid = uuid.uuid4().hex[:10]
        summary = report.diagnosis.summary if report.diagnosis else ""
        self._exec("INSERT INTO reports VALUES (?,?,?,?,?,?,?,?,?)",
                   (rid, now().isoformat(), report.cluster, report.namespace, report.question, source,
                    json.dumps(report.counts()), summary, report.model_dump_json()))
        return rid

    def list_reports(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self._exec("SELECT id, created, cluster, namespace, question, source, counts, summary FROM reports "
                          "ORDER BY created DESC LIMIT ?", (limit,)).fetchall()
        return [{**dict(r), "counts": json.loads(r["counts"] or "{}")} for r in rows]

    def get_report(self, rid: str) -> Report | None:
        row = self._exec("SELECT body FROM reports WHERE id = ?", (rid,)).fetchone()
        return Report.model_validate_json(row["body"]) if row else None

    # notes
    def add_note(self, check_id: str, note: str, target: str | None = None, author: str | None = None) -> int:
        cur = self._exec("INSERT INTO notes (created, check_id, target, note, author) VALUES (?,?,?,?,?)",
                         (now().isoformat(), check_id, target, note, author))
        return int(cur.lastrowid or 0)

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

    # alert dedupe
    def new_findings(self, cluster: str, findings: list[Finding]) -> list[Finding]:
        """Return findings not notified before, and remember them. Findings that disappear are forgotten."""
        ts = now().isoformat()
        current = {f.fingerprint: f for f in findings}
        known = {r["fingerprint"]: r for r in self._exec("SELECT * FROM seen WHERE cluster = ?", (cluster,)).fetchall()}
        fresh = []
        for fp, f in current.items():
            if fp in known:
                self._exec("UPDATE seen SET last_seen = ? WHERE fingerprint = ?", (ts, fp))
            else:
                self._exec("INSERT INTO seen VALUES (?,?,?,?,1)", (fp, cluster, ts, ts))
                fresh.append(f)
        for fp in set(known) - set(current):
            self._exec("DELETE FROM seen WHERE fingerprint = ?", (fp,))
        return fresh


def parse_created(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None
