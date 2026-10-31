"""Atomic artifact-only persistence. Task/attempt persistence is phase 3."""
from contextlib import closing
import hashlib
import json
import sqlite3

from .catalog import canonical, ToolError


def save_in_transaction(conn, task_id, version, snapshot):
    """Trusted local runtime only: caller owns transaction, no commit here."""
    payload = canonical(snapshot)
    artifact_id = 'artifact-' + hashlib.sha256(f'{task_id}:{version}'.encode()).hexdigest()[:24]
    row = conn.execute('SELECT payload FROM artifacts WHERE task_id=? AND plan_version=?', (task_id, version)).fetchone()
    if row:
        if row[0] != payload:
            raise ToolError('REQUEST_CONFLICT', 'Different artifact for this task and plan version')
    else:
        conn.execute('INSERT INTO artifacts VALUES (?,?,?,?)', (artifact_id, task_id, version, payload))
    return {'artifact_id': artifact_id, 'snapshot': json.loads(payload)}


def save_artifact(path, task_id, version, snapshot):
    payload = canonical(snapshot)
    artifact_id = 'artifact-' + hashlib.sha256(f'{task_id}:{version}'.encode()).hexdigest()[:24]
    try:
        with closing(sqlite3.connect(path, timeout=5)) as conn:
            conn.execute('BEGIN IMMEDIATE')
            conn.execute('CREATE TABLE IF NOT EXISTS artifacts (artifact_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, plan_version INTEGER NOT NULL, payload TEXT NOT NULL, UNIQUE(task_id, plan_version))')
            row = conn.execute('SELECT payload FROM artifacts WHERE task_id=? AND plan_version=?', (task_id, version)).fetchone()
            if row:
                if row[0] != payload:
                    raise ToolError('REQUEST_CONFLICT', 'Different artifact for this task and plan version')
            else:
                conn.execute('INSERT INTO artifacts VALUES (?,?,?,?)', (artifact_id, task_id, version, payload))
            try:
                conn.commit()
            except sqlite3.Error as exc:
                # Connection close below rolls back if commit did not occur.
                commit_error = exc
            else:
                return {'artifact_id': artifact_id, 'snapshot': json.loads(payload)}
        # Resolve a commit exception without inventing a successful result.
        try:
            with closing(sqlite3.connect(path)) as check:
                row = check.execute('SELECT payload FROM artifacts WHERE artifact_id=?', (artifact_id,)).fetchone()
                if row and row[0] == payload:
                    return {'artifact_id': artifact_id, 'snapshot': json.loads(payload)}
                raise ToolError('STORAGE_FAILED', str(commit_error))
        except sqlite3.Error as exc:
            raise ToolError('PENDING_RECONCILIATION', 'Cannot verify commit') from exc
    except (sqlite3.Error, OSError) as exc:
        raise ToolError('STORAGE_FAILED', str(exc)) from exc
