"""Small versioned SQLite store; commits include a recoverable receipt."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import uuid
from .catalog import ToolError, canonical, require

SCHEMA = [
    'CREATE TABLE runtime_meta (version INTEGER NOT NULL)',
    'CREATE TABLE tasks (task_id TEXT PRIMARY KEY, creation_json TEXT NOT NULL, input_json TEXT NOT NULL, input_hash TEXT NOT NULL, catalog_json TEXT NOT NULL, catalog_hash TEXT NOT NULL, state_json TEXT NOT NULL, state_hash TEXT NOT NULL, status TEXT NOT NULL, stop_reason TEXT, last_run TEXT)',
    "CREATE TABLE runs (run_id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(task_id), status TEXT NOT NULL, max_actions INTEGER NOT NULL, action_count INTEGER NOT NULL, mode TEXT NOT NULL DEFAULT 'offline_mock')",
    'CREATE TABLE replays (operation TEXT NOT NULL, request_id TEXT NOT NULL, normalized TEXT NOT NULL, response TEXT NOT NULL, PRIMARY KEY(operation, request_id))',
    'CREATE TABLE events (run_id TEXT NOT NULL REFERENCES runs(run_id), sequence INTEGER NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(run_id, sequence))',
    'CREATE TABLE commits (tx_id TEXT PRIMARY KEY, response TEXT NOT NULL)',
    'CREATE TABLE artifacts (artifact_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, plan_version INTEGER NOT NULL, payload TEXT NOT NULL, UNIQUE(task_id, plan_version))',
    'CREATE TABLE remote_actions (remote_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(run_id), sequence INTEGER NOT NULL, action_json TEXT NOT NULL, before_state_json TEXT NOT NULL, reserved_hash TEXT NOT NULL, payload_json TEXT NOT NULL, status TEXT NOT NULL, outcome_json TEXT, UNIQUE(run_id,sequence))',
]


class Database:
    def __init__(self, path):
        self.path = Path(path).resolve()

    def _schema(self, conn, initialize=False):
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not tables and initialize:
            for sql in SCHEMA:
                conn.execute(sql)
            conn.execute('INSERT INTO runtime_meta VALUES (2)')
        else:
            require(tables == {'runtime_meta', 'tasks', 'runs', 'replays', 'events', 'commits', 'artifacts', 'remote_actions'}, 'Incompatible database layout; v1 needs explicit upgrade', 'STORAGE_CORRUPT')
            require(conn.execute('SELECT version FROM runtime_meta').fetchall() == [(2,)], 'Unsupported database version', 'STORAGE_CORRUPT')

    def upgrade(self):
        require(self.path.is_file(),'Database does not exist','TASK_NOT_FOUND')
        try:
            with closing(sqlite3.connect(self.path,timeout=5)) as c:
                c.execute('BEGIN IMMEDIATE')
                version=c.execute('SELECT version FROM runtime_meta').fetchall()
                if version==[(2,)]:
                    self._schema(c);return {'schema_version':2,'changed':False}
                require(version==[(1,)],'Unsupported migration source','STORAGE_CORRUPT')
                tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                require(tables=={'runtime_meta','tasks','runs','replays','events','commits','artifacts'},'Partial database cannot be upgraded','STORAGE_CORRUPT')
                c.execute("ALTER TABLE runs ADD COLUMN mode TEXT NOT NULL DEFAULT 'offline_mock'")
                c.execute(SCHEMA[-1]);c.execute('UPDATE runtime_meta SET version=2')
                self._schema(c);c.commit()
                return {'schema_version':2,'changed':True}
        except sqlite3.Error as exc:
            raise ToolError('STORAGE_FAILED','Atomic upgrade failed; old data retained') from exc

    def read(self, callback):
        if not self.path.is_file():
            raise ToolError('TASK_NOT_FOUND', 'Task database does not exist')
        try:
            with closing(sqlite3.connect(self.path.as_uri()+'?mode=ro', uri=True, timeout=5)) as conn:
                conn.execute('BEGIN')
                self._schema(conn)
                conn.row_factory = sqlite3.Row
                return callback(conn)
        except sqlite3.Error as exc:
            raise ToolError('STORAGE_FAILED', str(exc)) from exc

    def empty(self):
        """Only an actual zero-schema SQLite file is safe to initialize again."""
        try:
            with closing(sqlite3.connect(self.path.as_uri()+'?mode=ro', uri=True, timeout=5)) as conn:
                return conn.execute('SELECT count(*) FROM sqlite_master').fetchone()[0] == 0
        except sqlite3.Error as exc:
            raise ToolError('STORAGE_CORRUPT', 'Cannot inspect existing database') from exc

    def write(self, callback, initialize=False):
        if not self.path.exists() and not initialize:
            raise ToolError('TASK_NOT_FOUND', 'Task database does not exist')
        if initialize:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        token = str(uuid.uuid4())
        committing = False
        try:
            with closing(sqlite3.connect(self.path, timeout=5)) as conn:
                conn.execute('PRAGMA foreign_keys=ON')
                conn.execute('BEGIN IMMEDIATE')
                self._schema(conn, initialize)
                conn.row_factory = sqlite3.Row
                result = callback(conn)
                conn.execute('INSERT INTO commits VALUES (?,?)', (token, canonical(result)))
                committing = True
                conn.commit()
                return result
        except (sqlite3.Error, OSError) as exc:
            if committing:
                try:
                    row = self.read(lambda c: c.execute('SELECT response FROM commits WHERE tx_id=?', (token,)).fetchone())
                    if row:
                        return json.loads(row[0])
                except (ToolError, ValueError) as recovery:
                    raise ToolError('PENDING_RECONCILIATION', 'Commit outcome cannot be verified; query before retry') from recovery
            raise ToolError('STORAGE_FAILED', str(exc)) from exc
