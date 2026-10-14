import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'plugin/scripts' / filename)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


installer = module('workflow_agent_installer', 'install_agents.py')
policy = module('workflow_escalation_policy', 'escalation_policy.py')


class AgentProfileTests(unittest.TestCase):
    def test_install_is_idempotent_and_preserves_other_profiles(self):
        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp) / 'agents'
            preview = installer.install(destination, dry_run=True)
            self.assertFalse(destination.exists())
            self.assertEqual({p['status'] for p in preview['profiles']}, {'would-install'})
            installed = installer.install(destination)
            self.assertEqual(len(installed['profiles']), 4)
            original = destination / 'Explorer.toml'
            original.write_text('unrelated original profile')
            again = installer.install(destination)
            self.assertEqual({p['status'] for p in again['profiles']}, {'unchanged'})
            self.assertEqual(original.read_text(), 'unrelated original profile')
            for name in installer.NAMES:
                self.assertEqual((destination / (name + '.toml')).read_bytes(),
                                 (installer.SOURCE / (name + '.toml')).read_bytes())

    def test_conflict_preflight_does_not_partially_install_or_overwrite(self):
        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp)
            existing = destination / 'WorkflowExpertAdvisor.toml'
            existing.write_text('personal customization')
            with self.assertRaisesRegex(ValueError, 'existing profile differs'):
                installer.install(destination)
            self.assertEqual(existing.read_text(), 'personal customization')
            self.assertEqual(sorted(p.name for p in destination.iterdir()), [existing.name])

    def test_expert_profile_remains_model_free_for_per_consultation_choice(self):
        content = (installer.SOURCE / 'WorkflowExpertAdvisor.toml').read_text()
        self.assertNotRegex(content, r'(?m)^model\s*=')
        self.assertNotRegex(content, r'(?m)^model_reasoning_effort\s*=')
        for name, model_name, effort in [('WorkflowExplorer', 'gpt-6-luna', 'medium'),
                                         ('WorkflowExecutor', 'gpt-6-luna', 'xhigh'),
                                         ('WorkflowReviewer', 'gpt-6.1-sol', 'high')]:
            text = (installer.SOURCE / (name + '.toml')).read_text()
            self.assertIn('name = "' + name + '"', text)
            self.assertIn('model = "' + model_name + '"', text)
            self.assertIn('model_reasoning_effort = "' + effort + '"', text)

    def test_astra_policy_requires_threshold_and_remaining_incident_quota(self):
        settings = policy.load_settings()
        self.assertFalse(policy.consultation_decision(settings, 1, 0)['eligible'])
        decision = policy.consultation_decision(settings, 2, 0)
        self.assertTrue(decision['eligible'])
        self.assertEqual(decision['spawnArgs'], {'agent_type': 'WorkflowExpertAdvisor',
                         'model': 'gpt-6-astra', 'reasoning_effort': 'high', 'fork_turns': 'none'})
        self.assertEqual(policy.consultation_decision(settings, 3, 1),
                         {'eligible': False, 'reason': 'quota-reached'})
        disabled = dict(settings, enabled=False)
        self.assertEqual(policy.consultation_decision(disabled, 20, 0),
                         {'eligible': False, 'reason': 'disabled'})
        for counters in [(-1, 0), (2, True), (True, 0)]:
            with self.assertRaises(ValueError):
                policy.consultation_decision(settings, *counters)

    def test_settings_validation_and_cli_are_local_only(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp) / 'settings.json'
            valid = {'astraConsultation': policy.load_settings()}
            for key, bad in [('enabled', 1), ('afterFailures', True), ('maxPerIncident', 0),
                             ('model', 'gpt-6-luna'), ('reasoningEffort', 'unknown')]:
                settings = {'astraConsultation': dict(valid['astraConsultation'], **{key: bad})}
                p.write_text(json.dumps(settings))
                with self.assertRaises(ValueError):
                    policy.load_settings(p)
            p.write_text(json.dumps(valid))
            result = subprocess.run([sys.executable, str(ROOT / 'plugin/scripts/escalation_policy.py'),
                                     '--settings', str(p), '--failures', '2', '--consultations-used', '1'],
                                    capture_output=True, text=True, check=True)
            self.assertEqual(json.loads(result.stdout), {'eligible': False, 'reason': 'quota-reached'})


if __name__ == '__main__':
    unittest.main()
