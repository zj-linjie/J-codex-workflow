#!/usr/bin/env python3
"""Build a local marketplace and deterministic zip; never install implicitly."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def build(output):
    output = output.resolve()
    if output == ROOT or ROOT in output.parents and output.parts[len(ROOT.parts):] != ('dist',):
        raise ValueError('Use the project dist directory or an external output directory')
    package = output / 'marketplace' / 'plugins' / 'codex-workflow'
    source = ROOT / 'plugin'
    required = ['.codex-plugin/plugin.json', 'mcp/server.py', 'mcp/store.py', 'mcp/usage.py',
                'ui/board.html', 'skills/codex-workflow/SKILL.md',
                'scripts/install_agents.py', 'scripts/escalation_policy.py',
                'settings/orchestrator.json']
    required += ['agent-profiles/Workflow' + name + '.toml'
                 for name in ('Explorer', 'Executor', 'Reviewer', 'ExpertAdvisor')]
    for name in required:
        if not (source / name).is_file():
            raise ValueError('Missing package file: ' + name)
    # Only replace the known generated package, never an arbitrary destination tree.
    if package.exists():
        shutil.rmtree(package)
    shutil.copytree(source, package, ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '.mcp.json'))
    (package / 'docs').mkdir(exist_ok=True)
    shutil.copyfile(ROOT / 'docs/CONTRACT.md', package / 'docs/CONTRACT.md')
    shutil.copyfile(ROOT / 'LICENSE', package / 'LICENSE')
    shutil.copytree(ROOT / 'examples', package / 'examples')
    manifest = json.loads((package / '.codex-plugin/plugin.json').read_text())
    config = {'mcpServers': {'codex-workflow': {
        'command': sys.executable,
        'args': [str(package / 'mcp/server.py')],
        'startup_timeout_sec': 15}}}
    (package / '.mcp.json').write_text(json.dumps(config, ensure_ascii=False, indent=2) + '\n')
    catalog = output / 'marketplace' / '.agents/plugins/marketplace.json'
    catalog.parent.mkdir(parents=True, exist_ok=True)
    catalog.write_text(json.dumps({
        'name': 'codex-workflow-local',
        'interface': {'displayName': 'J-codex-workflow 本地开发'},
        'plugins': [{'name': 'codex-workflow',
                     'source': {'source': 'local', 'path': './plugins/codex-workflow'},
                     'policy': {'installation': 'AVAILABLE', 'authentication': 'ON_INSTALL'},
                     'category': 'Productivity'}]}, ensure_ascii=False, indent=2) + '\n')
    archive = output / ('J-codex-workflow-' + manifest['version'] + '.zip')
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(package.rglob('*')):
            if path.is_file():
                info = zipfile.ZipInfo(str(path.relative_to(package.parent)), (2026, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                bundle.writestr(info, path.read_bytes())
    report = {'version': manifest['version'], 'marketplace': str(catalog.parents[2]),
              'plugin': str(package), 'archive': str(archive),
              'sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
              'note': 'Local MCP paths are generated for this directory. Regenerate after moving the project.'}
    (output / 'package.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'dist')
    args = parser.parse_args()
    print(json.dumps(build(args.output), ensure_ascii=False, indent=2))
