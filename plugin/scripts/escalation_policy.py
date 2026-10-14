#!/usr/bin/env python3
"""Read the main-agent consultation policy. Never spawn agents or invoke models."""
import argparse
import json
from pathlib import Path

DEFAULT_SETTINGS = Path(__file__).resolve().parents[1] / 'settings/orchestrator.json'


def load_settings(path=DEFAULT_SETTINGS):
    data = json.loads(Path(path).read_text())
    if not isinstance(data, dict) or set(data) != {'astraConsultation'}:
        raise ValueError('settings must contain only astraConsultation')
    policy = data['astraConsultation']
    keys = {'enabled', 'afterFailures', 'maxPerIncident', 'model', 'reasoningEffort'}
    if not isinstance(policy, dict) or set(policy) != keys:
        raise ValueError('astraConsultation has missing or unsupported fields')
    if not isinstance(policy['enabled'], bool):
        raise ValueError('enabled must be boolean')
    for key in ['afterFailures', 'maxPerIncident']:
        if type(policy[key]) is not int or not 1 <= policy[key] <= 100:
            raise ValueError(key + ' must be an integer between 1 and 100')
    if policy['model'] != 'gpt-6-astra':
        raise ValueError('model must be gpt-6-astra')
    if policy['reasoningEffort'] not in ('low', 'medium', 'high', 'xhigh', 'max', 'ultra'):
        raise ValueError('unsupported reasoningEffort')
    return policy


def consultation_decision(policy, failures, consultations_used):
    for value in (failures, consultations_used):
        if type(value) is not int or value < 0:
            raise ValueError('incident counters must be non-negative integers')
    if not policy['enabled']:
        reason = 'disabled'
    elif failures < policy['afterFailures']:
        reason = 'below-threshold'
    elif consultations_used >= policy['maxPerIncident']:
        reason = 'quota-reached'
    else:
        reason = 'main-agent-may-consult'
    decision = {'eligible': reason == 'main-agent-may-consult', 'reason': reason}
    if decision['eligible']:
        decision['spawnArgs'] = {'agent_type': 'WorkflowExpertAdvisor',
                                 'model': policy['model'], 'reasoning_effort': policy['reasoningEffort'],
                                 'fork_turns': 'none'}
    return decision


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--settings', type=Path, default=DEFAULT_SETTINGS)
    parser.add_argument('--failures', type=int, required=True)
    parser.add_argument('--consultations-used', type=int, required=True)
    args = parser.parse_args()
    try:
        decision = consultation_decision(load_settings(args.settings), args.failures, args.consultations_used)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    print(json.dumps(decision, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
