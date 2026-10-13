"""Transactional SQLite state store for J-codex-workflow.

The module deliberately uses only the Python standard library so it can be
bundled with the local plugin.  Each public operation opens its own SQLite
connection; write operations serialize with ``BEGIN IMMEDIATE`` and commit the
state transition, event, and idempotency receipt together.
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import re
import sqlite3
import contextlib
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple


class WorkflowError(Exception):
    """A rejected workflow operation that made no state changes."""


_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_MAX_RECENT_EVENTS = 100
_MAX_RECEIPTS = 200


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _string(value: Any, label: str, maximum: int, *, required: bool = True) -> str:
    if not isinstance(value, str):
        raise WorkflowError("%s must be a string" % label)
    if required and not value.strip():
        raise WorkflowError("%s must not be empty" % label)
    if len(value) > maximum:
        raise WorkflowError("%s exceeds %d characters" % (label, maximum))
    if any(ord(char) < 32 and char not in "\n\r\t" for char in value):
        raise WorkflowError("%s contains a control character" % label)
    return value


def _identifier(value: Any, label: str) -> str:
    result = _string(value, label, 64)
    if not _ID.fullmatch(result):
        raise WorkflowError("%s must use letters, digits, dot, underscore, or hyphen" % label)
    return result


def _plain_object(value: Any, label: str, required: Iterable[str], optional: Iterable[str] = ()) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise WorkflowError("%s must be an object" % label)
    required_set, optional_set = set(required), set(optional)
    missing = required_set - set(value)
    extra = set(value) - required_set - optional_set
    if missing:
        raise WorkflowError("%s is missing: %s" % (label, ", ".join(sorted(missing))))
    if extra:
        raise WorkflowError("%s has unsupported fields: %s" % (label, ", ".join(sorted(extra))))
    return value


def validate_plan(plan: Any) -> Dict[str, Any]:
    """Validate and return a JSON-safe normalized plan without changing text."""
    _plain_object(plan, "plan", ("title", "goal", "roles", "phases", "tasks"))
    title = _string(plan["title"], "title", 120)
    goal = _string(plan["goal"], "goal", 4000)

    roles = plan["roles"]
    phases = plan["phases"]
    tasks = plan["tasks"]
    if not isinstance(roles, list) or not roles or len(roles) > 32:
        raise WorkflowError("roles must contain 1 to 32 entries")
    if not isinstance(phases, list) or not phases or len(phases) > 12:
        raise WorkflowError("phases must contain 1 to 12 entries")
    if not isinstance(tasks, list) or not tasks or len(tasks) > 64:
        raise WorkflowError("tasks must contain 1 to 64 entries")

    normalized_roles: List[Dict[str, str]] = []
    role_ids = set()
    for index, role in enumerate(roles):
        label = "roles[%d]" % index
        _plain_object(role, label, ("id", "name", "kind", "instructions"))
        role_id = _identifier(role["id"], label + ".id")
        if role_id in role_ids:
            raise WorkflowError("role ids must be unique")
        role_ids.add(role_id)
        name = _string(role["name"], label + ".name", 80)
        kind = _string(role["kind"], label + ".kind", 16)
        if kind not in ("native", "primary"):
            raise WorkflowError(label + ".kind must be native or primary")
        instructions = _string(role["instructions"], label + ".instructions", 8000)
        normalized_roles.append({"id": role_id, "name": name, "kind": kind, "instructions": instructions})

    normalized_phases: List[Dict[str, str]] = []
    phase_ids = set()
    phase_index: Dict[str, int] = {}
    for index, phase in enumerate(phases):
        label = "phases[%d]" % index
        _plain_object(phase, label, ("id", "name"))
        phase_id = _identifier(phase["id"], label + ".id")
        if phase_id in phase_ids:
            raise WorkflowError("phase ids must be unique")
        phase_ids.add(phase_id)
        phase_index[phase_id] = index
        normalized_phases.append({"id": phase_id, "name": _string(phase["name"], label + ".name", 100)})

    normalized_tasks: List[Dict[str, Any]] = []
    task_ids = set()
    task_phase: Dict[str, int] = {}
    for index, task in enumerate(tasks):
        label = "tasks[%d]" % index
        _plain_object(task, label, ("id", "name", "roleId", "phaseId", "dependsOn", "acceptanceCriteria"))
        task_id = _identifier(task["id"], label + ".id")
        if task_id in task_ids:
            raise WorkflowError("task ids must be unique")
        task_ids.add(task_id)
        name = _string(task["name"], label + ".name", 160)
        role_id = _identifier(task["roleId"], label + ".roleId")
        if role_id not in role_ids:
            raise WorkflowError(label + ".roleId does not reference a role")
        phase_id = _identifier(task["phaseId"], label + ".phaseId")
        if phase_id not in phase_index:
            raise WorkflowError(label + ".phaseId does not reference a phase")
        depends = task["dependsOn"]
        if not isinstance(depends, list) or len(depends) > 64:
            raise WorkflowError(label + ".dependsOn must be a list")
        normalized_depends = []
        for dep_index, dep in enumerate(depends):
            dep_id = _identifier(dep, "%s.dependsOn[%d]" % (label, dep_index))
            if dep_id in normalized_depends:
                raise WorkflowError(label + ".dependsOn contains duplicates")
            normalized_depends.append(dep_id)
        criteria = _string(task["acceptanceCriteria"], label + ".acceptanceCriteria", 2000)
        normalized = {"id": task_id, "name": name, "roleId": role_id, "phaseId": phase_id,
                      "dependsOn": normalized_depends, "acceptanceCriteria": criteria}
        normalized_tasks.append(normalized)
        task_phase[task_id] = phase_index[phase_id]

    counts = {phase_id: 0 for phase_id in phase_ids}
    indegree = {task["id"]: 0 for task in normalized_tasks}
    outgoing = {task["id"]: [] for task in normalized_tasks}
    for task in normalized_tasks:
        counts[task["phaseId"]] += 1
        for dep_id in task["dependsOn"]:
            if dep_id not in task_ids:
                raise WorkflowError("task %s depends on unknown task %s" % (task["id"], dep_id))
            if dep_id == task["id"]:
                raise WorkflowError("task %s cannot depend on itself" % task["id"])
            if task_phase[dep_id] > task_phase[task["id"]]:
                raise WorkflowError("task %s cannot depend on a later phase" % task["id"])
            indegree[task["id"]] += 1
            outgoing[dep_id].append(task["id"])
    if any(count == 0 for count in counts.values()):
        raise WorkflowError("every phase must contain at least one task")

    # Kahn's algorithm catches cycles regardless of task array order.
    ready = [task_id for task_id, degree in indegree.items() if degree == 0]
    visited = 0
    while ready:
        current = ready.pop()
        visited += 1
        for child in outgoing[current]:
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.append(child)
    if visited != len(normalized_tasks):
        raise WorkflowError("task dependencies must form a DAG")

    return {"title": title, "goal": goal, "roles": normalized_roles,
            "phases": normalized_phases, "tasks": normalized_tasks}


class Store:
    """Persistent multi-run workflow store."""

    def __init__(self, path: Optional[os.PathLike] = None, *, timeout: float = 15.0):
        if path is None:
            data_dir = os.environ.get("CODEX_WORKFLOW_DATA_DIR")
            if data_dir:
                path = pathlib.Path(data_dir) / "workflows.sqlite3"
            else:
                path = pathlib.Path.home() / ".local" / "share" / "codex-workflow" / "workflows.sqlite3"
        self._memory = str(path) == ":memory:"
        self.path = ("file:codex-workflow-%s?mode=memory&cache=shared" % uuid.uuid4().hex
                     if self._memory else str(pathlib.Path(path).expanduser()))
        self.timeout = timeout
        self._keeper = None
        if self._memory:
            self._keeper = self._connect()
        else:
            pathlib.Path(self.path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=self.timeout, isolation_level=None, uri=self._memory)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=%d" % int(self.timeout * 1000))
        if not self._memory:
            connection.execute("PRAGMA synchronous=FULL")
        return connection

    @contextlib.contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            yield connection
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connection() as db:
            if not self._memory:
                db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    goal TEXT NOT NULL,
                    plan_json TEXT NOT NULL,
                    task_state_json TEXT NOT NULL,
                    plan_version INTEGER NOT NULL,
                    revision INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    pause_reason TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                    event_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    at TEXT NOT NULL,
                    data_json TEXT NOT NULL,
                    UNIQUE(run_id, event_id)
                );
                CREATE INDEX IF NOT EXISTS events_run_seq ON events(run_id, seq DESC);
                CREATE TABLE IF NOT EXISTS idempotency (
                    event_id TEXT PRIMARY KEY,
                    operation TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    run_id TEXT,
                    result_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS observed_receipts (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                    client_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    rendered_json TEXT NOT NULL,
                    visibility TEXT NOT NULL,
                    at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS receipts_run_seq ON observed_receipts(run_id, seq DESC);
            """)

    @staticmethod
    def _event_id(event_id: Any) -> str:
        return _string(event_id, "eventId", 128)

    @staticmethod
    def _run_id(run_id: Any) -> str:
        value = _string(run_id, "runId", 128)
        if not _RUN_ID.fullmatch(value):
            raise WorkflowError("runId has an invalid format")
        return value

    @staticmethod
    def _revision(value: Any) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise WorkflowError("expectedRevision must be a non-negative integer")
        return value

    def _write_start(self, db: sqlite3.Connection) -> None:
        db.execute("BEGIN IMMEDIATE")

    def _prior_result(self, db: sqlite3.Connection, event_id: str, operation: str, payload: Any) -> Optional[Dict[str, Any]]:
        fingerprint = hashlib.sha256(_json({"operation": operation, "payload": payload}).encode("utf-8")).hexdigest()
        row = db.execute("SELECT operation,fingerprint,result_json FROM idempotency WHERE event_id=?", (event_id,)).fetchone()
        if row is None:
            return None
        if row["operation"] != operation or row["fingerprint"] != fingerprint:
            raise WorkflowError("eventId was already used with different parameters")
        return json.loads(row["result_json"])

    def _save_receipt(self, db: sqlite3.Connection, event_id: str, operation: str, payload: Any,
                      run_id: str, result: Dict[str, Any]) -> None:
        fingerprint = hashlib.sha256(_json({"operation": operation, "payload": payload}).encode("utf-8")).hexdigest()
        db.execute("INSERT INTO idempotency(event_id,operation,fingerprint,run_id,result_json,created_at) VALUES(?,?,?,?,?,?)",
                   (event_id, operation, fingerprint, run_id, _json(result), _now()))

    def _snapshot(self, db: sqlite3.Connection, run_id: str) -> Dict[str, Any]:
        row = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise WorkflowError("runId does not exist")
        plan = json.loads(row["plan_json"])
        states = json.loads(row["task_state_json"])
        tasks = []
        for task in plan["tasks"]:
            state = states[task["id"]]
            tasks.append(dict(task, status=state["status"], nativeId=state["nativeId"],
                              message=state["message"], summary=state["summary"],
                              artifacts=state["artifacts"], acceptance=state["acceptance"],
                              attempt=state["attempt"]))
        event_rows = db.execute("SELECT event_id,kind,revision,at,data_json FROM events WHERE run_id=? ORDER BY seq DESC LIMIT ?",
                                (run_id, _MAX_RECENT_EVENTS)).fetchall()
        events = []
        for event in reversed(event_rows):
            value = {"eventId": event["event_id"], "type": event["kind"], "revision": event["revision"], "at": event["at"]}
            value.update(json.loads(event["data_json"]))
            events.append(value)
        return {"id": row["id"], "title": row["title"], "goal": row["goal"],
                "planVersion": row["plan_version"], "revision": row["revision"], "status": row["status"],
                "roles": plan["roles"], "phases": plan["phases"], "tasks": tasks, "events": events,
                "createdAt": row["created_at"], "updatedAt": row["updated_at"], "pauseReason": row["pause_reason"]}

    def _append_event(self, db: sqlite3.Connection, run_id: str, event_id: str, kind: str,
                      revision: int, data: Dict[str, Any]) -> None:
        db.execute("INSERT INTO events(run_id,event_id,kind,revision,at,data_json) VALUES(?,?,?,?,?,?)",
                   (run_id, event_id, kind, revision, _now(), _json(data)))

    def _load_mutable(self, db: sqlite3.Connection, run_id: str) -> Tuple[sqlite3.Row, Dict[str, Any], Dict[str, Any]]:
        row = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise WorkflowError("runId does not exist")
        return row, json.loads(row["plan_json"]), json.loads(row["task_state_json"])

    def _check_revision(self, row: sqlite3.Row, expected_revision: int) -> None:
        if row["revision"] != expected_revision:
            raise WorkflowError("revision conflict: expected %d, current %d" % (expected_revision, row["revision"]))

    def _commit_run(self, db: sqlite3.Connection, run_id: str, *, plan: Dict[str, Any], states: Dict[str, Any],
                    plan_version: int, revision: int, status: str, pause_reason: Optional[str],
                    event_id: str, kind: str, data: Dict[str, Any]) -> Dict[str, Any]:
        db.execute("UPDATE runs SET title=?,goal=?,plan_json=?,task_state_json=?,plan_version=?,revision=?,status=?,pause_reason=?,updated_at=? WHERE id=?",
                   (plan["title"], plan["goal"], _json(plan), _json(states), plan_version, revision,
                    status, pause_reason, _now(), run_id))
        self._append_event(db, run_id, event_id, kind, revision, data)
        return self._snapshot(db, run_id)

    def create(self, plan: Any, event_id: Any) -> Dict[str, Any]:
        normalized = validate_plan(plan)
        event_id = self._event_id(event_id)
        payload = {"plan": normalized}
        with self._connection() as db:
            self._write_start(db)
            prior = self._prior_result(db, event_id, "create", payload)
            if prior is not None:
                db.commit()
                return prior
            run_id = str(uuid.uuid4())
            now = _now()
            states = {task["id"]: {"status": "pending", "nativeId": None, "message": "", "summary": "",
                                  "artifacts": [], "acceptance": "", "attempt": 1}
                      for task in normalized["tasks"]}
            db.execute("INSERT INTO runs(id,title,goal,plan_json,task_state_json,plan_version,revision,status,pause_reason,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                       (run_id, normalized["title"], normalized["goal"], _json(normalized), _json(states), 1, 0,
                        "draft", None, now, now))
            self._append_event(db, run_id, event_id, "created", 0, {"planVersion": 1})
            result = self._snapshot(db, run_id)
            self._save_receipt(db, event_id, "create", payload, run_id, result)
            db.commit()
            return result

    def update_draft(self, run_id: Any, plan: Any, expected_revision: Any, event_id: Any) -> Dict[str, Any]:
        run_id, expected_revision, event_id = self._run_id(run_id), self._revision(expected_revision), self._event_id(event_id)
        normalized = validate_plan(plan)
        payload = {"runId": run_id, "plan": normalized, "expectedRevision": expected_revision}
        with self._connection() as db:
            self._write_start(db)
            prior = self._prior_result(db, event_id, "update_draft", payload)
            if prior is not None:
                db.commit()
                return prior
            row, _old_plan, old_states = self._load_mutable(db, run_id)
            self._check_revision(row, expected_revision)
            if row["status"] != "draft":
                raise WorkflowError("only a draft run can be edited")
            states = {task["id"]: old_states.get(task["id"], {"status": "pending", "nativeId": None, "message": "",
                      "summary": "", "artifacts": [], "acceptance": "", "attempt": 1}) for task in normalized["tasks"]}
            result = self._commit_run(db, run_id, plan=normalized, states=states, plan_version=row["plan_version"] + 1,
                                      revision=row["revision"] + 1, status="draft", pause_reason=None,
                                      event_id=event_id, kind="draft_updated", data={"planVersion": row["plan_version"] + 1})
            self._save_receipt(db, event_id, "update_draft", payload, run_id, result)
            db.commit()
            return result

    def confirm(self, run_id: Any, expected_revision: Any, event_id: Any, confirmation: Any) -> Dict[str, Any]:
        run_id, expected_revision, event_id = self._run_id(run_id), self._revision(expected_revision), self._event_id(event_id)
        confirmation = _string(confirmation, "confirmation", 2000)
        payload = {"runId": run_id, "expectedRevision": expected_revision, "confirmation": confirmation}
        with self._connection() as db:
            self._write_start(db)
            prior = self._prior_result(db, event_id, "confirm", payload)
            if prior is not None:
                db.commit()
                return prior
            row, plan, states = self._load_mutable(db, run_id)
            self._check_revision(row, expected_revision)
            if row["status"] != "draft":
                raise WorkflowError("only a draft run can be confirmed")
            result = self._commit_run(db, run_id, plan=plan, states=states, plan_version=row["plan_version"],
                                      revision=row["revision"] + 1, status="running", pause_reason=None,
                                      event_id=event_id, kind="confirmed", data={"confirmation": confirmation})
            self._save_receipt(db, event_id, "confirm", payload, run_id, result)
            db.commit()
            return result

    def task_event(self, run_id: Any, task_id: Any, status: Any, expected_revision: Any, event_id: Any,
                   **fields: Any) -> Dict[str, Any]:
        run_id, task_id = self._run_id(run_id), _identifier(task_id, "taskId")
        status = _string(status, "status", 16)
        if status not in ("running", "submitted", "done", "failed"):
            raise WorkflowError("status must be running, submitted, done, or failed")
        expected_revision, event_id = self._revision(expected_revision), self._event_id(event_id)
        allowed = {"nativeId", "message", "summary", "artifacts", "acceptance"}
        extra = set(fields) - allowed
        if extra:
            raise WorkflowError("unsupported task event fields: %s" % ", ".join(sorted(extra)))
        allowed_by_status = {
            "running": {"nativeId", "message"},
            "submitted": {"message", "summary", "artifacts"},
            "done": {"message", "artifacts", "acceptance"},
            "failed": {"message", "artifacts"},
        }
        irrelevant = set(fields) - allowed_by_status[status]
        if irrelevant:
            raise WorkflowError("fields not valid for %s: %s" % (status, ", ".join(sorted(irrelevant))))
        cleaned: Dict[str, Any] = {}
        if "nativeId" in fields:
            cleaned["nativeId"] = _string(fields["nativeId"], "nativeId", 256)
        for key, maximum in (("message", 4000), ("summary", 4000), ("acceptance", 4000)):
            if key in fields:
                cleaned[key] = _string(fields[key], key, maximum, required=(key in ("summary", "acceptance")))
        if "artifacts" in fields:
            artifacts = fields["artifacts"]
            if not isinstance(artifacts, list) or len(artifacts) > 32:
                raise WorkflowError("artifacts must be a list of at most 32 references")
            cleaned["artifacts"] = [_string(item, "artifact reference", 2048) for item in artifacts]
        payload = {"runId": run_id, "taskId": task_id, "status": status,
                   "expectedRevision": expected_revision, "fields": cleaned}
        with self._connection() as db:
            self._write_start(db)
            prior = self._prior_result(db, event_id, "task_event", payload)
            if prior is not None:
                db.commit()
                return prior
            row, plan, states = self._load_mutable(db, run_id)
            self._check_revision(row, expected_revision)
            if row["status"] == "draft":
                raise WorkflowError("draft run must be confirmed before execution events")
            if row["status"] not in ("running", "paused"):
                raise WorkflowError("execution events are not accepted for this run")
            tasks = {task["id"]: task for task in plan["tasks"]}
            if task_id not in tasks:
                raise WorkflowError("taskId does not exist in this run")
            task = tasks[task_id]
            state = states[task_id]
            old_status = state["status"]
            run_status = row["status"]
            next_pause_reason = row["pause_reason"]

            if status == "running":
                if run_status != "running":
                    raise WorkflowError("paused runs cannot start tasks")
                if old_status != "pending":
                    raise WorkflowError("only a pending task can start")
                done = {key for key, value in states.items() if value["status"] == "done"}
                if any(dep not in done for dep in task["dependsOn"]):
                    raise WorkflowError("task dependencies are not yet accepted")
                phase_order = {phase["id"]: index for index, phase in enumerate(plan["phases"])}
                current_phase = phase_order[task["phaseId"]]
                earlier_task_ids = {other["id"] for other in plan["tasks"]
                                    if phase_order[other["phaseId"]] < current_phase}
                if any(states[key]["status"] != "done" for key in earlier_task_ids):
                    raise WorkflowError("all earlier phase tasks must be accepted first")
                role = next(item for item in plan["roles"] if item["id"] == task["roleId"])
                native_id = cleaned.get("nativeId")
                if role["kind"] == "native" and not native_id:
                    raise WorkflowError("native tasks require a nativeId before starting")
                if role["kind"] == "primary" and native_id is not None:
                    raise WorkflowError("primary tasks must not claim a nativeId")
                state["status"] = "running"
                state["nativeId"] = native_id
                state["message"] = cleaned.get("message", "")
            elif status == "submitted":
                if old_status != "running":
                    raise WorkflowError("only a running task can submit a result")
                if "summary" not in cleaned:
                    raise WorkflowError("submitted tasks require a result summary")
                state["status"] = "submitted"
                state["summary"] = cleaned["summary"]
                state["message"] = cleaned.get("message", state["message"])
                state["artifacts"] = cleaned.get("artifacts", state["artifacts"])
            elif status == "done":
                if run_status != "running":
                    raise WorkflowError("paused runs cannot accept new task evaluations")
                if old_status != "submitted":
                    raise WorkflowError("only a submitted task can be accepted")
                if "acceptance" not in cleaned:
                    raise WorkflowError("done tasks require an acceptance explanation")
                state["status"] = "done"
                state["acceptance"] = cleaned["acceptance"]
                state["message"] = cleaned.get("message", state["message"])
                state["artifacts"] = cleaned.get("artifacts", state["artifacts"])
            else:  # failed
                if old_status not in ("running", "submitted"):
                    raise WorkflowError("only a running or submitted task can fail")
                failure = cleaned.get("message")
                if not failure:
                    raise WorkflowError("failed tasks require a failure message")
                state["status"] = "failed"
                state["message"] = failure
                if "artifacts" in cleaned:
                    state["artifacts"] = cleaned["artifacts"]
                run_status = "paused"
                next_pause_reason = failure

            if run_status == "running" and all(value["status"] == "done" for value in states.values()):
                run_status = "completed"
                next_pause_reason = None
            revision = row["revision"] + 1
            event_data = {"taskId": task_id, "status": status, **cleaned}
            result = self._commit_run(db, run_id, plan=plan, states=states, plan_version=row["plan_version"],
                                      revision=revision, status=run_status, pause_reason=next_pause_reason,
                                      event_id=event_id, kind="task_%s" % status, data=event_data)
            self._save_receipt(db, event_id, "task_event", payload, run_id, result)
            db.commit()
            return result

    def control(self, run_id: Any, action: Any, reason: Any, expected_revision: Any, event_id: Any,
                task_id: Any = None) -> Dict[str, Any]:
        run_id, action = self._run_id(run_id), _string(action, "action", 16)
        if action not in ("pause", "resume", "retry", "cancel"):
            raise WorkflowError("action must be pause, resume, retry, or cancel")
        reason = _string(reason, "reason", 2000)
        expected_revision, event_id = self._revision(expected_revision), self._event_id(event_id)
        if action == "retry":
            task_id = _identifier(task_id, "taskId")
        elif task_id is not None:
            raise WorkflowError("taskId is only valid for retry")
        payload = {"runId": run_id, "action": action, "reason": reason,
                   "taskId": task_id, "expectedRevision": expected_revision}
        with self._connection() as db:
            self._write_start(db)
            prior = self._prior_result(db, event_id, "control", payload)
            if prior is not None:
                db.commit()
                return prior
            row, plan, states = self._load_mutable(db, run_id)
            self._check_revision(row, expected_revision)
            run_status, pause_reason = row["status"], row["pause_reason"]
            if action == "pause":
                if run_status != "running":
                    raise WorkflowError("only a running run can be paused")
                run_status, pause_reason = "paused", reason
            elif action == "resume":
                if run_status != "paused":
                    raise WorkflowError("only a paused run can be resumed")
                if any(value["status"] == "failed" for value in states.values()):
                    raise WorkflowError("failed tasks must be retried explicitly before resume")
                run_status, pause_reason = "running", None
            elif action == "retry":
                if run_status != "paused":
                    raise WorkflowError("only a paused run can retry a failed task")
                state = states.get(task_id)
                if state is None or state["status"] != "failed":
                    raise WorkflowError("retry requires a failed task in this run")
                state.update({"status": "pending", "nativeId": None, "message": reason,
                              "summary": "", "artifacts": [], "acceptance": "", "attempt": state["attempt"] + 1})
                if any(value["status"] == "failed" for value in states.values()):
                    run_status = "paused"
                    pause_reason = "其他失败任务仍需你决定是否重试"
                else:
                    run_status, pause_reason = "running", None
            else:  # cancel
                if run_status in ("completed", "cancelled"):
                    raise WorkflowError("terminal runs cannot be cancelled again")
                for state in states.values():
                    if state["status"] not in ("done", "cancelled"):
                        state["status"] = "cancelled"
                        state["message"] = reason
                run_status, pause_reason = "cancelled", None

            revision = row["revision"] + 1
            result = self._commit_run(db, run_id, plan=plan, states=states, plan_version=row["plan_version"],
                                      revision=revision, status=run_status, pause_reason=pause_reason,
                                      event_id=event_id, kind=action, data={"reason": reason, "taskId": task_id})
            self._save_receipt(db, event_id, "control", payload, run_id, result)
            db.commit()
            return result

    def get(self, run_id: Any) -> Dict[str, Any]:
        run_id = self._run_id(run_id)
        with self._connection() as db:
            db.execute("BEGIN")
            result = self._snapshot(db, run_id)
            db.commit()
            return result

    def list_runs(self, limit: Any = 20) -> List[Dict[str, Any]]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise WorkflowError("limit must be an integer from 1 to 100")
        with self._connection() as db:
            db.execute("BEGIN")
            rows = db.execute("SELECT id,title,status,plan_version,revision,updated_at FROM runs ORDER BY updated_at DESC,id DESC LIMIT ?", (limit,)).fetchall()
            db.commit()
            return [{"id": row["id"], "title": row["title"], "status": row["status"],
                     "planVersion": row["plan_version"], "revision": row["revision"], "updatedAt": row["updated_at"]}
                    for row in rows]

    def observed(self, run_id: Any, client_id: Any, revision: Any, rendered: Any, visibility: Any) -> Dict[str, Any]:
        run_id, client_id = self._run_id(run_id), _string(client_id, "clientId", 128)
        revision = self._revision(revision)
        if not isinstance(rendered, list) or len(rendered) > 64:
            raise WorkflowError("rendered must be a list of at most 64 task summaries")
        clean_rendered = []
        for index, item in enumerate(rendered):
            _plain_object(item, "rendered[%d]" % index, ("id", "status"))
            clean_rendered.append({"id": _identifier(item["id"], "rendered task id"),
                                   "status": _string(item["status"], "rendered task status", 16)})
        visibility = _string(visibility, "visibility", 16)
        if visibility not in ("visible", "hidden", "prerender"):
            raise WorkflowError("visibility must be visible, hidden, or prerender")
        with self._connection() as db:
            self._write_start(db)
            row = db.execute("SELECT revision FROM runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                raise WorkflowError("runId does not exist")
            if revision > row["revision"]:
                raise WorkflowError("observed revision cannot be ahead of the run")
            at = _now()
            db.execute("INSERT INTO observed_receipts(run_id,client_id,revision,rendered_json,visibility,at) VALUES(?,?,?,?,?,?)",
                       (run_id, client_id, revision, _json(clean_rendered), visibility, at))
            db.execute("DELETE FROM observed_receipts WHERE run_id=? AND seq NOT IN (SELECT seq FROM observed_receipts WHERE run_id=? ORDER BY seq DESC LIMIT ?)",
                       (run_id, run_id, _MAX_RECEIPTS))
            db.commit()
        return {"runId": run_id, "clientId": client_id, "revision": revision,
                "rendered": clean_rendered, "visibility": visibility, "observedAt": at}
