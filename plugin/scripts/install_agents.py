#!/usr/bin/env python3
"""Install the bundled workflow profiles without overwriting existing definitions."""
import argparse
import json
import os
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / 'agent-profiles'
NAMES = ('WorkflowExplorer', 'WorkflowExecutor', 'WorkflowReviewer', 'WorkflowExpertAdvisor')


def install(destination, dry_run=False):
    destination = Path(destination).expanduser()
    operations = []
    for name in NAMES:
        source = SOURCE / (name + '.toml')
        if source.is_symlink() or not source.is_file():
            raise ValueError('missing bundled profile: ' + name)
        payload = source.read_bytes()
        target = destination / source.name
        if target.is_symlink():
            raise ValueError('refusing profile symlink: ' + str(target))
        if target.exists():
            if not target.is_file() or target.read_bytes() != payload:
                raise ValueError('existing profile differs; resolve it before installing: ' + str(target))
            status = 'unchanged'
        else:
            status = 'would-install' if dry_run else 'installed'
        operations.append((target, payload, status))
    created = []
    if not dry_run:
        destination.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            for target, payload, status in operations:
                if status == 'unchanged':
                    continue
                descriptor = os.open(str(target), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                created.append(target)
                with os.fdopen(descriptor, 'wb') as stream:
                    stream.write(payload)
        except Exception:
            for target in created:
                target.unlink()
            raise
    return {'destination': str(destination), 'dryRun': dry_run,
            'profiles': [{'name': target.stem, 'status': status} for target, _, status in operations]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destination', type=Path, default=Path.home() / '.codex/agents')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    try:
        result = install(args.destination, args.dry_run)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
