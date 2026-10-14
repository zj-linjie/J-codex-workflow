import json
import multiprocessing
import pathlib
import sqlite3
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "plugin" / "mcp"))
from store import Store, WorkflowError, validate_plan


def sample_plan():
    return {
        "title": "调研并评审",
        "goal": "输出可验证建议",
        "roles": [
            {"id": "analyst", "name": "调研员", "kind": "native", "instructions": "核查依据"},
            {"id": "owner", "name": "主代理", "kind": "primary", "instructions": "验收"},
        ],
        "phases": [{"id": "research", "name": "并行调研"}, {"id": "review", "name": "验收交付"}],
        "tasks": [
            {"id": "t1", "name": "核对能力", "roleId": "analyst", "phaseId": "research",
             "dependsOn": [], "acceptanceCriteria": "可追溯依据"},
            {"id": "t1b", "name": "交叉核对", "roleId": "analyst", "phaseId": "research",
             "dependsOn": [], "acceptanceCriteria": "有独立证据"},
            {"id": "t2", "name": "汇总结论", "roleId": "owner", "phaseId": "review",
             "dependsOn": ["t1", "t1b"], "acceptanceCriteria": "结论覆盖目标"},
        ],
    }


def _concurrent_create(path, event_id, queue):
    try:
        result = Store(path).create(sample_plan(), event_id)
        queue.put(("ok", result["id"], result["revision"]))
    except Exception as error:  # surfaced to parent test as a useful assertion
        queue.put(("error", type(error).__name__, str(error)))


def _concurrent_start(path, run_id, task_id, revision, event_id, native_id, queue):
    try:
        result = Store(path).task_event(run_id, task_id, "running", revision, event_id, nativeId=native_id)
        queue.put(("ok", result["revision"], task_id))
    except WorkflowError as error:
        kind = "conflict" if "revision conflict" in str(error) else "error"
        queue.put((kind, str(error), task_id))
    except Exception as error:
        queue.put(("error", type(error).__name__, str(error)))


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = pathlib.Path(self.temp.name) / "workflow.sqlite3"
        self.store = Store(self.db_path)

    def tearDown(self):
        self.temp.cleanup()

    def create(self, event_id="create-1", plan=None):
        return self.store.create(plan or sample_plan(), event_id)

    def start_run(self, run):
        return self.store.confirm(run["id"], run["revision"], "confirm-1", "用户已确认计划")

    def complete_run(self, run):
        run = self.start_run(run)
        role_kinds = {role["id"]: role["kind"] for role in run["roles"]}
        for task in run["tasks"]:
            event_base = "complete-" + task["id"]
            fields = {"nativeId": "native-" + task["id"]} if role_kinds[task["roleId"]] == "native" else {}
            run = self.store.task_event(run["id"], task["id"], "running", run["revision"],
                                        event_base + "-start", **fields)
            run = self.store.task_event(run["id"], task["id"], "submitted", run["revision"],
                                        event_base + "-submit", summary="已完成任务")
            run = self.store.task_event(run["id"], task["id"], "done", run["revision"],
                                        event_base + "-done", acceptance="符合验收标准")
        return run

    def test_plan_validation_rejects_invalid_references_cycles_and_empty_phases(self):
        plan = sample_plan()
        plan["tasks"][2]["dependsOn"] = ["missing"]
        with self.assertRaisesRegex(WorkflowError, "unknown task"):
            validate_plan(plan)

        plan = sample_plan()
        plan["tasks"][0]["dependsOn"] = ["t1b"]
        plan["tasks"][1]["dependsOn"] = ["t1"]
        with self.assertRaisesRegex(WorkflowError, "DAG"):
            validate_plan(plan)

        plan = sample_plan()
        plan["tasks"][2]["phaseId"] = "research"
        plan["tasks"][2]["dependsOn"] = []
        plan["phases"][1]["id"] = "unused"
        with self.assertRaisesRegex(WorkflowError, "every phase"):
            validate_plan(plan)

        plan = sample_plan()
        plan["tasks"][1]["dependsOn"] = ["t1"]
        plan["tasks"][0]["phaseId"] = "review"
        with self.assertRaisesRegex(WorkflowError, "later phase"):
            validate_plan(plan)

    def test_role_agent_type_binding_is_native_only_and_survives_plan_updates_and_restart(self):
        plan = sample_plan()
        plan["roles"][0]["agentType"] = "gpt-6.1-sol"
        run = self.create("bound-create", plan)
        self.assertEqual(run["roles"][0]["agentType"], "gpt-6.1-sol")

        revised_plan = sample_plan()
        revised_plan["roles"][0]["agentType"] = "research_agent.v2"
        updated = self.store.update_draft(run["id"], revised_plan, run["revision"], "bound-update")
        restarted = Store(self.db_path).get(run["id"])
        self.assertEqual(updated["roles"][0]["agentType"], "research_agent.v2")
        self.assertEqual(restarted["roles"][0]["agentType"], "research_agent.v2")

        invalid = sample_plan()
        invalid["roles"][0]["agentType"] = "invalid type"
        with self.assertRaisesRegex(WorkflowError, "agentType must use"):
            validate_plan(invalid)
        invalid = sample_plan()
        invalid["roles"][1]["agentType"] = "agent-x"
        with self.assertRaisesRegex(WorkflowError, "only valid for native"):
            validate_plan(invalid)

    def test_bound_native_start_requires_exact_agent_type_and_rejections_are_atomic(self):
        plan = sample_plan()
        plan["roles"][0]["agentType"] = "research-agent"
        run = self.start_run(self.create("bound-start-create", plan))
        for event_id, supplied in (("bound-missing", {}), ("bound-wrong", {"agentType": "other-agent"})):
            with self.subTest(event_id=event_id):
                before = self.store.get(run["id"])
                with self.assertRaisesRegex(WorkflowError, "agentType"):
                    self.store.task_event(run["id"], "t1", "running", before["revision"], event_id,
                                          nativeId="native-1", **supplied)
                after = self.store.get(run["id"])
                self.assertEqual(after["revision"], before["revision"])
                self.assertEqual(after["tasks"][0]["status"], "pending")
                self.assertEqual(after["events"], before["events"])

        run = self.store.task_event(run["id"], "t1", "running", run["revision"], "bound-correct",
                                    nativeId="native-1", agentType="research-agent")
        self.assertEqual(run["tasks"][0]["agentType"], "research-agent")
        with self.assertRaisesRegex(WorkflowError, "not valid for submitted"):
            self.store.task_event(run["id"], "t1", "submitted", run["revision"], "agent-type-on-submit",
                                  summary="结果", agentType="research-agent")
        self.assertEqual(self.store.get(run["id"])["revision"], run["revision"])

    def test_primary_running_rejects_agent_type_and_native_id(self):
        plan = sample_plan()
        plan["tasks"][0]["roleId"] = "owner"
        run = self.start_run(self.create("primary-start-create", plan))
        for event_id, fields, message in (
            ("primary-agent-type", {"agentType": "research-agent"}, "agentType"),
            ("primary-native-id", {"nativeId": "native-1"}, "nativeId"),
        ):
            with self.subTest(event_id=event_id):
                before = self.store.get(run["id"])
                with self.assertRaisesRegex(WorkflowError, message):
                    self.store.task_event(run["id"], "t1", "running", before["revision"], event_id, **fields)
                after = self.store.get(run["id"])
                self.assertEqual(after["revision"], before["revision"])
                self.assertEqual(after["tasks"][0]["status"], "pending")

    def test_legacy_plan_and_task_state_without_agent_type_remain_compatible(self):
        run = self.create("legacy-create")
        # Recreate the persisted shape written by versions before agentType existed.
        with self.store._connection() as db:
            row = db.execute("SELECT task_state_json FROM runs WHERE id=?", (run["id"],)).fetchone()
            states = json.loads(row["task_state_json"])
            for state in states.values():
                state.pop("agentType", None)
            db.execute("UPDATE runs SET task_state_json=? WHERE id=?",
                       (json.dumps(states), run["id"]))
            db.commit()

        restarted = Store(self.db_path)
        legacy = restarted.get(run["id"])
        self.assertNotIn("agentType", legacy["roles"][0])
        self.assertIsNone(legacy["tasks"][0]["agentType"])
        legacy = restarted.confirm(run["id"], legacy["revision"], "legacy-confirm", "确认旧计划")
        legacy = restarted.task_event(legacy["id"], "t1", "running", legacy["revision"], "legacy-start",
                                      nativeId="legacy-native")
        self.assertEqual(legacy["tasks"][0]["status"], "running")
        self.assertIsNone(legacy["tasks"][0]["agentType"])
        legacy = restarted.task_event(legacy["id"], "t1b", "running", legacy["revision"], "legacy-bound-at-start",
                                      nativeId="legacy-native-2", agentType="custom-agent")
        self.assertEqual(legacy["tasks"][1]["agentType"], "custom-agent")

    def test_draft_update_revision_confirmation_and_phase_gate(self):
        draft = self.create()
        self.assertEqual((draft["status"], draft["planVersion"], draft["revision"]), ("draft", 1, 0))
        with self.assertRaisesRegex(WorkflowError, "draft run"):
            self.store.task_event(draft["id"], "t1", "running", 0, "too-early", nativeId="agent-1")
        self.assertEqual(self.store.get(draft["id"])["revision"], 0)

        revised_plan = sample_plan()
        revised_plan["title"] = "更新后的目标"
        draft = self.store.update_draft(draft["id"], revised_plan, 0, "edit-1")
        self.assertEqual((draft["title"], draft["planVersion"], draft["revision"]), ("更新后的目标", 2, 1))
        with self.assertRaisesRegex(WorkflowError, "revision conflict"):
            self.store.confirm(draft["id"], 0, "stale-confirm", "用户已确认")
        running = self.store.confirm(draft["id"], 1, "confirm-1", "用户确认 plan v2")
        self.assertEqual(running["status"], "running")

        with self.assertRaisesRegex(WorkflowError, "dependencies"):
            self.store.task_event(running["id"], "t2", "running", running["revision"], "early-stage", message="start")
        with self.assertRaisesRegex(WorkflowError, "nativeId"):
            self.store.task_event(running["id"], "t1", "running", running["revision"], "no-native-id")
        a = self.store.task_event(running["id"], "t1", "running", running["revision"], "start-t1", nativeId="native-1")
        b = self.store.task_event(a["id"], "t1b", "running", a["revision"], "start-t1b", nativeId="native-2")
        self.assertEqual([task["status"] for task in b["tasks"]], ["running", "running", "pending"])

    def test_submission_needs_summary_acceptance_and_finishes_only_after_all_done(self):
        run = self.start_run(self.create())
        run = self.store.task_event(run["id"], "t1", "running", run["revision"], "s1", nativeId="native-1")
        with self.assertRaisesRegex(WorkflowError, "summary"):
            self.store.task_event(run["id"], "t1", "submitted", run["revision"], "submit-without-summary")
        run = self.store.task_event(run["id"], "t1", "submitted", run["revision"], "submit-1",
                                    summary="能力已核实", artifacts=["report.md"])
        self.assertEqual(run["tasks"][0]["status"], "submitted")
        with self.assertRaisesRegex(WorkflowError, "acceptance"):
            self.store.task_event(run["id"], "t1", "done", run["revision"], "accept-without-reason")
        run = self.store.task_event(run["id"], "t1", "done", run["revision"], "accept-1", acceptance="依据满足标准")
        self.assertEqual(run["status"], "running")
        run = self.store.task_event(run["id"], "t1b", "running", run["revision"], "s2", nativeId="native-2")
        run = self.store.task_event(run["id"], "t1b", "submitted", run["revision"], "submit-2", summary="已交叉核对")
        run = self.store.task_event(run["id"], "t1b", "done", run["revision"], "accept-2", acceptance="独立证据充足")
        run = self.store.task_event(run["id"], "t2", "running", run["revision"], "s3", message="主代理处理中")
        run = self.store.task_event(run["id"], "t2", "submitted", run["revision"], "submit-3", summary="结论已整理")
        run = self.store.task_event(run["id"], "t2", "done", run["revision"], "accept-3", acceptance="覆盖目标且可验证")
        self.assertEqual(run["status"], "completed")
        self.assertEqual(run["tasks"][2]["status"], "done")
        with self.assertRaisesRegex(WorkflowError, "not accepted"):
            self.store.task_event(run["id"], "t2", "failed", run["revision"], "after-complete", message="late")

    def test_failure_pauses_and_only_explicit_retry_or_resume_continues(self):
        plan = sample_plan()
        plan["roles"][0]["agentType"] = "research-agent"
        run = self.start_run(self.create(plan=plan))
        run = self.store.task_event(run["id"], "t1", "running", run["revision"], "s1", nativeId="native-1",
                                    agentType="research-agent")
        run = self.store.task_event(run["id"], "t1b", "running", run["revision"], "s2", nativeId="native-2",
                                    agentType="research-agent")
        run = self.store.task_event(run["id"], "t1", "failed", run["revision"], "fail-1", message="来源无法验证")
        self.assertEqual((run["status"], run["pauseReason"]), ("paused", "来源无法验证"))
        with self.assertRaisesRegex(WorkflowError, "cannot start"):
            self.store.task_event(run["id"], "t2", "running", run["revision"], "blocked-start", message="start")
        # A task already dispatched before the pause may still return its result.
        run = self.store.task_event(run["id"], "t1b", "submitted", run["revision"], "return-during-pause", summary="已返回")
        with self.assertRaisesRegex(WorkflowError, "cannot accept"):
            self.store.task_event(run["id"], "t1b", "done", run["revision"], "accept-during-pause", acceptance="不能验收")
        with self.assertRaisesRegex(WorkflowError, "retried explicitly"):
            self.store.control(run["id"], "resume", "用户要求继续", run["revision"], "resume-too-soon")
        run = self.store.control(run["id"], "retry", "用户明确要求重试来源核查", run["revision"], "retry-1", task_id="t1")
        self.assertEqual((run["status"], run["pauseReason"]), ("running", None))
        retried = next(task for task in run["tasks"] if task["id"] == "t1")
        self.assertEqual((retried["status"], retried["attempt"], retried["nativeId"]), ("pending", 2, None))
        self.assertIsNone(retried["agentType"])
        self.assertEqual(next(role for role in run["roles"] if role["id"] == "analyst")["agentType"], "research-agent")
        run = self.store.task_event(run["id"], "t1", "running", run["revision"], "retry-start", nativeId="native-1b",
                                    agentType="research-agent")
        retried = next(task for task in run["tasks"] if task["id"] == "t1")
        self.assertEqual((retried["status"], retried["attempt"], retried["nativeId"], retried["agentType"]),
                         ("running", 2, "native-1b", "research-agent"))

        run = self.store.control(run["id"], "pause", "用户决定暂停核对", run["revision"], "manual-pause")
        run = self.store.control(run["id"], "resume", "用户明确决定继续", run["revision"], "manual-resume")
        self.assertEqual(run["status"], "running")

    def test_cancel_marks_all_unfinished_tasks_and_is_terminal(self):
        run = self.start_run(self.create())
        run = self.store.control(run["id"], "cancel", "用户决定取消", run["revision"], "cancel-1")
        self.assertEqual(run["status"], "cancelled")
        self.assertEqual({task["status"] for task in run["tasks"]}, {"cancelled"})
        with self.assertRaisesRegex(WorkflowError, "not accepted"):
            self.store.task_event(run["id"], "t1", "running", run["revision"], "after-cancel", nativeId="native")

    def test_idempotency_precedes_stale_revision_and_rejects_parameter_reuse(self):
        run = self.create("create-idempotent")
        replay = self.store.create(sample_plan(), "create-idempotent")
        self.assertEqual(replay["id"], run["id"])
        run = self.start_run(run)
        updated = self.store.task_event(run["id"], "t1", "running", run["revision"], "same-event", nativeId="native-1")
        later = self.store.task_event(updated["id"], "t1b", "running", updated["revision"], "later-event", nativeId="native-2")
        replay = self.store.task_event(later["id"], "t1", "running", run["revision"], "same-event", nativeId="native-1")
        self.assertEqual(replay["revision"], updated["revision"])
        self.assertEqual(self.store.get(run["id"])["revision"], later["revision"])
        with self.assertRaisesRegex(WorkflowError, "different parameters"):
            self.store.task_event(later["id"], "t1", "running", updated["revision"], "same-event", nativeId="other")
        self.assertEqual(self.store.get(run["id"])["revision"], later["revision"])

    def test_two_runs_and_store_restart_remain_isolated(self):
        first = self.create("create-a")
        second = self.store.create(sample_plan() | {"title": "另一个运行"}, "create-b")
        second = self.start_run(second)
        second = self.store.control(second["id"], "cancel", "停止", second["revision"], "cancel-b")
        restarted = Store(self.db_path)
        self.assertEqual(restarted.get(first["id"])["status"], "draft")
        self.assertEqual(restarted.get(second["id"])["status"], "cancelled")
        self.assertEqual({item["id"] for item in restarted.list_runs()}, {first["id"], second["id"]})
        with self.assertRaisesRegex(WorkflowError, "does not exist"):
            restarted.get("not-a-real-run")

    def test_session_id_is_validated_bound_at_creation_and_legacy_create_is_idempotent(self):
        legacy = self.create("usage-legacy-create")
        self.assertIsNone(legacy["sessionId"])
        self.assertIsNone(legacy["usage"])
        replay = self.store.create(sample_plan(), "usage-legacy-create")
        self.assertEqual((replay["id"], replay["sessionId"], replay["usage"]),
                         (legacy["id"], None, None))

        bound = self.store.create(sample_plan(), "usage-bound-create", session_id="session.123:abc")
        self.assertEqual(bound["sessionId"], "session.123:abc")
        updated = self.store.update_draft(bound["id"], sample_plan() | {"title": "修改草案"},
                                          bound["revision"], "usage-bound-update")
        self.assertEqual(updated["sessionId"], "session.123:abc")
        self.assertIsNone(updated["usage"])
        with self.assertRaisesRegex(WorkflowError, "different parameters"):
            self.store.create(sample_plan(), "usage-legacy-create", session_id="session-123")

        for invalid in ("../outside", r"folder\\session", "", "x" * 129, "space id"):
            with self.subTest(session_id=invalid):
                with self.assertRaises(WorkflowError):
                    self.store.create(sample_plan(), "invalid-session-" + str(len(invalid)), session_id=invalid)

    def test_usage_capture_is_gated_first_wins_persisted_and_does_not_change_workflow(self):
        run = self.complete_run(self.store.create(sample_plan(), "usage-complete-create", "session-usage"))
        before = self.store.get(run["id"])
        calls = []
        summary = {"version": 1, "status": "available", "sessionId": "session-usage",
                   "totals": {"totalTokens": 17}}

        def collector(session_id):
            calls.append(session_id)
            return summary

        captured = self.store.capture_usage(run["id"], collector)
        self.assertEqual(calls, ["session-usage"])
        self.assertEqual(captured["usage"], summary)
        self.assertEqual(captured["sessionId"], "session-usage")
        self.assertEqual((captured["revision"], captured["events"]), (before["revision"], before["events"]))
        captured["usage"]["totals"]["totalTokens"] = -1

        cached = self.store.capture_usage(run["id"], lambda _session_id: self.fail("cached usage rescanned"))
        self.assertEqual(cached["usage"]["totals"]["totalTokens"], 17)
        restarted = Store(self.db_path)
        self.assertEqual(restarted.get(run["id"])["usage"]["totals"]["totalTokens"], 17)
        self.assertEqual(restarted.get(run["id"])["revision"], before["revision"])

    def test_concurrent_usage_scans_keep_first_saved_snapshot_and_legacy_collector_gets_none(self):
        run = self.complete_run(self.store.create(sample_plan(), "usage-race-first"))
        ready = threading.Barrier(2)
        results = []
        errors = []
        calls = []

        def scan(label):
            def collector(session_id):
                calls.append(session_id)
                self.assertIsNone(session_id)
                ready.wait(10)
                return {"version": 1, "status": "unavailable", "sessionId": None,
                        "notes": [label]}
            try:
                results.append(self.store.capture_usage(run["id"], collector))
            except Exception as error:
                errors.append(error)

        workers = [threading.Thread(target=scan, args=(label,)) for label in ("first", "second")]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(10)
        self.assertFalse(any(worker.is_alive() for worker in workers), "usage scans did not finish")
        self.assertEqual(errors, [])
        self.assertEqual(calls, [None, None])
        self.assertEqual(len(results), 2)
        saved = self.store.get(run["id"])["usage"]
        self.assertIn(saved["notes"], (["first"], ["second"]))
        self.assertEqual([result["usage"] for result in results], [saved, saved])

    def test_usage_capture_validates_run_and_completion_before_collector_and_rechecks_before_save(self):
        draft = self.create("usage-draft")
        calls = []
        collector = lambda session_id: calls.append(session_id) or {"status": "unavailable"}
        for run_id in ("bad/path", "missing-run"):
            with self.subTest(run_id=run_id):
                with self.assertRaises(WorkflowError):
                    self.store.capture_usage(run_id, collector)
        with self.assertRaisesRegex(WorkflowError, "completed"):
            self.store.capture_usage(draft["id"], collector)
        self.assertEqual(calls, [])

        completed = self.complete_run(self.store.create(sample_plan(), "usage-race-create"))

        def change_status_while_collecting(session_id):
            calls.append(session_id)
            with sqlite3.connect(self.db_path) as db:
                db.execute("UPDATE runs SET status='running' WHERE id=?", (completed["id"],))
            return {"status": "available"}

        with self.assertRaisesRegex(WorkflowError, "completed"):
            self.store.capture_usage(completed["id"], change_status_while_collecting)
        self.assertEqual(calls, [None])
        with sqlite3.connect(self.db_path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM run_usage WHERE run_id=?",
                                        (completed["id"],)).fetchone()[0], 0)

    def test_old_database_without_session_usage_tables_and_receipt_fields_remains_readable(self):
        run = self.create("old-db-create")
        with sqlite3.connect(self.db_path) as db:
            old_result = json.loads(db.execute("SELECT result_json FROM idempotency WHERE event_id=?",
                                               ("old-db-create",)).fetchone()[0])
            old_result.pop("sessionId", None)
            old_result.pop("usage", None)
            db.execute("UPDATE idempotency SET result_json=? WHERE event_id=?",
                       (json.dumps(old_result), "old-db-create"))
            db.execute("DROP TABLE run_sessions")
            db.execute("DROP TABLE run_usage")

        upgraded = Store(self.db_path)
        self.assertEqual(upgraded.get(run["id"])["sessionId"], None)
        self.assertIsNone(upgraded.get(run["id"])["usage"])
        replay = upgraded.create(sample_plan(), "old-db-create")
        self.assertEqual((replay["id"], replay["sessionId"], replay["usage"]), (run["id"], None, None))

    def test_get_uses_one_snapshot_when_confirmation_commits_mid_read(self):
        run = self.create("snapshot-create")
        reader = Store(self.db_path)
        selected_run = threading.Event()
        continue_read = threading.Event()
        result_box = []
        error_box = []
        original_snapshot = reader._snapshot

        def pause_between_run_and_events(db, run_id):
            row = db.execute("SELECT revision,status FROM runs WHERE id=?", (run_id,)).fetchone()
            selected_run.set()
            if not continue_read.wait(10):
                raise AssertionError("test did not release paused snapshot reader")
            result = original_snapshot(db, run_id)
            # Preserve the first SELECT as the run portion of this snapshot;
            # original_snapshot supplies the event list queried after commit.
            result["revision"] = row["revision"]
            result["status"] = row["status"]
            return result

        reader._snapshot = pause_between_run_and_events

        def read_run():
            try:
                result_box.append(reader.get(run["id"]))
            except Exception as error:
                error_box.append(error)

        thread = threading.Thread(target=read_run)
        thread.start()
        self.assertTrue(selected_run.wait(10), "reader did not reach the run SELECT")
        committed = Store(self.db_path).confirm(run["id"], 0, "snapshot-confirm", "用户确认")
        self.assertEqual((committed["status"], committed["revision"]), ("running", 1))
        continue_read.set()
        thread.join(10)
        self.assertFalse(thread.is_alive(), "snapshot reader did not finish")
        self.assertEqual(error_box, [])
        snapshot = result_box[0]
        self.assertEqual((snapshot["status"], snapshot["revision"]), ("draft", 0))
        self.assertEqual([event["type"] for event in snapshot["events"]], ["created"])

    def test_observed_receipts_are_isolated_and_do_not_change_revision(self):
        run = self.create()
        receipt = self.store.observed(run["id"], "client-1", 0,
                                      [{"id": "t1", "status": "pending"}], "visible")
        self.assertTrue(receipt["observedAt"].endswith("Z"))
        self.assertEqual(self.store.get(run["id"])["revision"], 0)
        with self.assertRaisesRegex(WorkflowError, "ahead"):
            self.store.observed(run["id"], "client-1", 1, [], "visible")

    def test_cross_process_create_is_idempotent(self):
        # The process race exercises SQLite's on-disk locking and idempotency
        # receipt, not just a Python lock around one Store instance.
        context = multiprocessing.get_context("spawn")
        queue = context.Queue()
        processes = [context.Process(target=_concurrent_create, args=(str(self.db_path), "cross-process-create", queue))
                     for _ in range(3)]
        for process in processes:
            process.start()
        results = [queue.get(timeout=30) for _ in processes]
        for process in processes:
            process.join(30)
            self.assertEqual(process.exitcode, 0)
        self.assertTrue(all(item[0] == "ok" for item in results), results)
        self.assertEqual(len({item[1] for item in results}), 1)
        self.assertEqual(self.store.list_runs(), [{"id": results[0][1], "title": "调研并评审", "status": "draft",
                                                   "planVersion": 1, "revision": 0,
                                                   "updatedAt": self.store.get(results[0][1])["updatedAt"]}])

    def test_concurrent_compare_and_swap_never_loses_a_task_event(self):
        run = self.start_run(self.create())
        context = multiprocessing.get_context("spawn")
        queue = context.Queue()
        # Compete on two independent ready tasks with the same expected revision.
        processes = [context.Process(target=_concurrent_start,
                                     args=(str(self.db_path), run["id"], task_id, run["revision"], event_id, native_id, queue))
                     for task_id, event_id, native_id in (("t1", "cas-t1", "native-1"),
                                                          ("t1b", "cas-t1b", "native-2"))]
        for process in processes:
            process.start()
        results = [queue.get(timeout=30) for _ in processes]
        for process in processes:
            process.join(30)
            self.assertEqual(process.exitcode, 0)
        self.assertEqual(sorted(item[0] for item in results), ["conflict", "ok"], results)
        current = self.store.get(run["id"])
        self.assertEqual(current["revision"], run["revision"] + 1)
        self.assertEqual(sum(task["status"] == "running" for task in current["tasks"]), 1)
        self.assertEqual(len(current["events"]), 3)  # created, confirmed, and one winning start


if __name__ == "__main__":
    unittest.main()
