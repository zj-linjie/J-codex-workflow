"""Exercise the installable package, including its generated stdio launch command."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('workflow_package', ROOT / 'scripts/package.py')
package_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package_module)


class PackageTests(unittest.TestCase):
    def test_built_package_launches_and_contains_skill_references(self):
        with tempfile.TemporaryDirectory(prefix='workflow-package-') as directory:
            report = package_module.build(Path(directory) / 'dist')
            plugin = Path(report['plugin'])
            manifest = json.loads((plugin / '.codex-plugin/plugin.json').read_text())
            self.assertEqual(manifest['name'], 'codex-workflow')
            self.assertTrue((plugin / 'docs/CONTRACT.md').is_file())
            self.assertIn('MIT License', (plugin / 'LICENSE').read_text())
            self.assertTrue((plugin / 'skills/codex-workflow/references/plan-schema.md').is_file())
            self.assertTrue((plugin / 'examples/research-review.json').is_file())
            self.assertTrue((plugin / 'settings/orchestrator.json').is_file())
            self.assertTrue((plugin / 'scripts/escalation_policy.py').is_file())
            self.assertTrue((plugin / 'agent-profiles/WorkflowExpertAdvisor.toml').is_file())
            agent_destination = Path(directory) / 'installed-agents'
            subprocess.run([sys.executable, str(plugin / 'scripts/install_agents.py'),
                            '--destination', str(agent_destination)], check=True, capture_output=True)
            self.assertEqual(len(list(agent_destination.glob('*.toml'))), 4)
            config = json.loads((plugin / '.mcp.json').read_text())['mcpServers']['codex-workflow']
            env = os.environ.copy()
            env['CODEX_WORKFLOW_DATA_DIR'] = str(Path(directory) / 'data')
            plan = json.loads((ROOT / 'examples/research-review.json').read_text())
            requests = [
                {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {'protocolVersion': '2025-06-18'}},
                {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'},
                {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call', 'params': {'name': 'workflow_create', 'arguments': {'plan': plan, 'eventId': 'package-smoke-create'}}},
                {'jsonrpc': '2.0', 'id': 4, 'method': 'resources/read', 'params': {'uri': 'ui://codex-workflow/board.html'}}]
            result = subprocess.run([config['command']] + config['args'], env=env,
                                    input=''.join(json.dumps(item) + '\n' for item in requests),
                                    capture_output=True, text=True, check=True, timeout=15)
            self.assertEqual(result.stderr, '')
            replies = [json.loads(line)['result'] for line in result.stdout.splitlines()]
            self.assertEqual(len(replies[1]['tools']), 9)
            self.assertEqual(replies[2]['structuredContent']['run']['status'], 'draft')
            self.assertIn('J-codex-workflow', replies[3]['contents'][0]['text'])
            with zipfile.ZipFile(report['archive']) as archive:
                self.assertIn('codex-workflow/.mcp.json', archive.namelist())
                self.assertIn('codex-workflow/docs/CONTRACT.md', archive.namelist())


if __name__ == '__main__':
    unittest.main()
