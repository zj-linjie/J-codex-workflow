#!/usr/bin/env python3
"""Compare synthetic MCP reply bytes using a temporary DB, without model calls."""
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'plugin/mcp'))
import server
from store import Store


def byte_size(result):
    return len(json.dumps(result, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))


def measure():
    plan = {
        'title': '合成上下文载荷对照', 'goal': '仅验证返回载荷，不执行真实子代理',
        'roles': [{'id': 'owner', 'name': '主代理', 'kind': 'primary',
                   'instructions': '限定范围并检查证据。' * 30}],
        'phases': [{'id': 'work', 'name': '执行'}],
        'tasks': [{'id': 't' + str(i), 'name': '任务' + str(i), 'roleId': 'owner',
                   'phaseId': 'work', 'dependsOn': [], 'acceptanceCriteria': '产物与证据满足要求。' * 10}
                  for i in range(10)],
    }
    previous_store = server._STORE
    try:
        with tempfile.TemporaryDirectory(prefix='workflow-payload-') as directory:
            server.use_store(Store(Path(directory) / 'test.sqlite3'))

            def call(name, **arguments):
                result = server.respond('tools/call', {'name': name, 'arguments': arguments})
                if result.get('isError'):
                    raise RuntimeError(result)
                return result

            run = call('workflow_create', plan=plan, eventId='create', view='compact')['structuredContent']['run']
            run = call('workflow_confirm', runId=run['id'], expectedRevision=run['revision'],
                       eventId='confirm', confirmation='合成测试确认', view='compact')['structuredContent']['run']
            old_bytes = new_bytes = 0
            for task in plan['tasks']:
                for status, fields in [
                    ('running', {}),
                    ('submitted', {'summary': '已完成，证据见报告。' * 20, 'artifacts': ['report.md']}),
                    ('done', {'acceptance': '检查了产物和要求。' * 20}),
                ]:
                    compact = call('workflow_event', runId=run['id'], taskId=task['id'], status=status,
                                   expectedRevision=run['revision'], eventId=task['id'] + '-' + status,
                                   view='compact', **fields)
                    run = compact['structuredContent']['run']
                    full = call('workflow_get', runId=run['id'])
                    # Old coordination received the same snapshot on both write and get.
                    full_write = server._run_result(full['structuredContent']['run'], compact['content'][0]['text'], 'full')
                    old_bytes += byte_size(full_write) + byte_size(full)
                    new_bytes += byte_size(compact)
            return {
                'fixtureTasks': 10, 'stateWrites': 30,
                'oldCoordinationReplies': 60, 'newCoordinationReplies': 30,
                'oldReplyBytes': old_bytes, 'newReplyBytes': new_bytes,
                'replyByteReductionPercent': round((1 - new_bytes / old_bytes) * 100, 2),
                'finalFullReplyBytes': byte_size(full), 'finalCompactReplyBytes': byte_size(compact),
                'note': 'Synthetic JSON UTF-8 reply bytes only; no model calls, token or billing measurement.',
            }
    finally:
        server._STORE = previous_store


if __name__ == '__main__':
    print(json.dumps(measure(), ensure_ascii=False, indent=2))
