import json
from pathlib import Path
import tempfile
import unittest

from plugin.mcp.usage import collect_usage


def event(kind, payload, at=0):
    return {"type": kind, "payload": payload, "timestamp": '2026-10-14T00:00:%02dZ' % at}


def tokens(at, count=100, cumulative=1000, output=40):
    usage = {"input_tokens": count, "cached_input_tokens": count // 2,
             "output_tokens": output, "reasoning_output_tokens": output // 2}
    info = {"last_token_usage": usage}
    if cumulative is not None:
        info["total_token_usage"] = {"input_tokens": cumulative, "output_tokens": cumulative // 2}
    return event('event_msg', {"type": 'token_count', 'info': info}, at)


class UsageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / 'sessions'
        self.archive = Path(self.temp.name) / 'archived_sessions'
        self.root.mkdir()
        self.archive.mkdir()
        self.addCleanup(self.temp.cleanup)

    def trace(self, sid='root', parent=None, data=None, archive=False, name=None, nested=False):
        meta = {'id': sid, 'agent_role': 'WorkflowExecutor' if parent else 'main'}
        if parent:
            if nested:
                meta['source'] = {'subagent': {'thread_spawn': {'parent_thread_id': parent}}}
            else:
                meta['parent_thread_id'] = parent
        values = [event('session_meta', meta), event('turn_context', {'model': 'model-A', 'effort': 'high'})]
        values.extend(data if data is not None else [tokens(1), tokens(11, cumulative=2000)])
        path = (self.archive if archive else self.root) / ((name or sid) + '.jsonl')
        path.write_text('\n'.join(json.dumps(value) for value in values) + '\n')
        return path

    def collect(self, sid='root'):
        return collect_usage(sid, self.root, self.archive)

    def test_session_tree_aggregates_main_children_and_models_without_content(self):
        self.trace()
        self.trace('child', 'root', nested=True)
        self.trace('grandchild', 'child', data=[event('turn_context', {'model': 'model-B'}), tokens(1)])
        self.trace('unrelated', data=[tokens(1, count=999999)])
        report = self.collect()
        self.assertEqual(report['status'], 'available')
        self.assertEqual(report['coverage']['sessionsFound'], 3)
        self.assertEqual(report['totals']['totalTokens'], 700)
        self.assertEqual(report['totals']['inputTokens'], 500)
        self.assertEqual(report['totals']['cachedInputTokens'], 250)
        self.assertEqual(report['totals']['reasoningOutputTokens'], 100)
        self.assertEqual(report['speed']['samples'], 2)
        self.assertEqual(report['speed']['medianTokensPerSecond'], 4)
        self.assertEqual({row['model'] for row in report['models']}, {'model-A', 'model-B'})
        self.assertNotIn(str(self.root), json.dumps(report))
        self.assertNotIn('unrelated', json.dumps(report))

    def test_identical_last_usage_without_cumulative_evidence_is_not_dropped(self):
        self.trace(data=[tokens(1, cumulative=None), tokens(11, cumulative=None)])
        self.assertEqual(self.collect()['totals']['tokenEvents'], 2)
        self.trace(data=[tokens(1), tokens(2), tokens(11, cumulative=2000)])
        report = self.collect()
        self.assertEqual(report['totals']['tokenEvents'], 2)
        self.assertEqual(report['coverage']['skippedDuplicateEvents'], 1)
        self.assertEqual(report['speed']['medianTokensPerSecond'], 4)

    def test_latest_archive_copy_wins_even_when_active_file_is_larger(self):
        old = self.trace(data=[tokens(1)])
        with old.open('a') as stream:
            stream.write(json.dumps(event('response_item', {'type': 'message', 'content': 'SECRET TEXT' * 1000}, 2)) + '\n')
        self.trace(archive=True, data=[tokens(1), tokens(11, cumulative=2000)])
        report = self.collect()
        self.assertEqual(report['totals']['tokenEvents'], 2)
        self.assertEqual(report['coverage']['duplicateFiles'], 1)
        self.assertNotIn('SECRET', json.dumps(report))

    def test_parallel_tool_union_is_subtracted_once(self):
        data = [tokens(1)]
        for kind, call, at in [('function_call', 'a', 2), ('custom_tool_call', 'b', 3),
                               ('function_call_output', 'a', 7), ('custom_tool_call_output', 'b', 8)]:
            data.append(event('response_item', {'type': kind, 'call_id': call}, at))
        data.append(tokens(11, cumulative=2000))
        self.trace(data=data)
        self.assertEqual(self.collect()['speed']['medianTokensPerSecond'], 10)

    def test_speed_resets_between_turns_and_model_effort_changes(self):
        self.trace(data=[tokens(1), event('event_msg', {'type': 'task_started'}, 2), tokens(11, cumulative=2000),
                         event('turn_context', {'model': 'model-B'}, 12), tokens(20, cumulative=3000),
                         event('turn_context', {'model': 'model-B', 'effort': 'low'}, 21), tokens(30, cumulative=4000)])
        report = self.collect()
        self.assertEqual(report['totals']['tokenEvents'], 4)
        self.assertIsNone(report['speed'])
        self.assertEqual(len(report['models']), 2)

    def test_missing_logs_are_unavailable_instead_of_zero_usage(self):
        report = self.collect()
        self.assertEqual(report['status'], 'unavailable')
        self.assertIsNone(report['totals'])
        self.assertIn('root_missing', report['notes'])
        self.assertIsNone(collect_usage(None, self.root)['totals'])
        self.trace(data=[])
        self.assertIsNone(self.collect()['totals'])

    def test_missing_root_or_child_usage_marks_coverage_partial(self):
        self.trace('child', 'root')
        self.assertEqual(self.collect()['status'], 'partial')
        self.trace()
        self.trace('empty-child', 'root', data=[])
        report = self.collect()
        self.assertEqual(report['status'], 'partial')
        self.assertEqual(report['coverage']['sessionsFound'], 3)
        self.assertEqual(report['coverage']['sessionsWithUsage'], 2)

    def test_malformed_lines_shapes_and_invalid_tokens_do_not_become_usage(self):
        path = self.trace(data=[tokens(1), event('event_msg', {'type': 'token_count', 'info': []}, 2),
                                event('event_msg', {'type': 'token_count', 'info': {'last_token_usage': {'input_tokens': True, 'output_tokens': -1}}}, 3)])
        with path.open('a') as stream:
            stream.write('null\n{"payload": []}\n{broken\n')
        report = self.collect()
        self.assertEqual(report['status'], 'partial')
        self.assertEqual(report['totals']['tokenEvents'], 1)
        self.assertEqual(report['coverage']['malformedLines'], 3)
        self.assertIn('incomplete_events', report['notes'])

    def test_no_speed_sample_for_zero_window_or_first_response(self):
        self.trace(data=[tokens(1), tokens(1, cumulative=2000)])
        self.assertIsNone(self.collect()['speed'])

    def test_rate_limit_only_events_do_not_mark_valid_usage_partial(self):
        self.trace(data=[tokens(1), event('event_msg', {'type': 'token_count', 'info': None, 'rate_limits': {}}, 2), tokens(11, cumulative=2000)])
        report = self.collect()
        self.assertEqual(report['status'], 'available')
        self.assertEqual(report['totals']['tokenEvents'], 2)

    def test_cycle_and_invalid_session_id_cannot_expand_scope(self):
        self.trace()
        self.trace('cycle1', 'cycle2')
        self.trace('cycle2', 'cycle1')
        self.assertEqual(self.collect()['coverage']['sessionsFound'], 1)
        for sid in ('../root', '', 'a' * 129, 123):
            with self.assertRaises(ValueError):
                self.collect(sid)

    def test_default_roots_use_codex_home_including_archives(self):
        from unittest.mock import patch
        self.trace()
        self.trace('child', 'root', archive=True)
        with patch.dict('os.environ', {'CODEX_HOME': self.temp.name}):
            self.assertEqual(collect_usage('root')['coverage']['sessionsFound'], 2)

    def test_mcp_completion_uses_bound_id_and_real_collector_with_temporary_logs(self):
        from unittest.mock import patch
        from plugin.mcp import server
        from plugin.mcp.store import Store
        self.trace()
        self.trace('child', 'root')
        store = Store(Path(self.temp.name) / 'workflow.sqlite3')
        previous_store = server._STORE
        server.use_store(store)
        self.addCleanup(server.use_store, previous_store)
        plan = {'title': 'synthetic test', 'goal': 'temporary fixtures only',
                'roles': [{'id': 'main', 'name': 'main', 'kind': 'primary', 'instructions': 'check'}],
                'phases': [{'id': 'p', 'name': 'p'}],
                'tasks': [{'id': 't', 'name': 't', 'roleId': 'main', 'phaseId': 'p', 'dependsOn': [], 'acceptanceCriteria': 'fixture'}]}
        def call(name, arguments):
            response = server.respond('tools/call', {'name': name, 'arguments': arguments})
            self.assertFalse(response.get('isError'), response)
            return response['structuredContent']['run']
        run = call('workflow_create', {'plan': plan, 'sessionId': 'root', 'eventId': 'create'})
        run = call('workflow_confirm', {'runId': run['id'], 'expectedRevision': run['revision'], 'eventId': 'confirm', 'confirmation': 'fixture only'})
        for status, fields in [('running', {}), ('submitted', {'summary': 'fixture'}), ('done', {'acceptance': 'fixture checked'})]:
            run = call('workflow_event', {'runId': run['id'], 'taskId': 't', 'status': status,
                                        'expectedRevision': run['revision'], 'eventId': status, **fields})
        self.assertEqual(run['status'], 'completed')
        self.assertIsNone(run['usage'])
        with patch.dict('os.environ', {'CODEX_HOME': self.temp.name}):
            captured = call('workflow_usage', {'runId': run['id']})
        self.assertEqual(captured['usage']['totals']['totalTokens'], 560)
        self.assertEqual(captured['usage']['coverage']['sessionsFound'], 2)
        self.assertEqual(captured['revision'], run['revision'])


if __name__ == '__main__':
    unittest.main()
