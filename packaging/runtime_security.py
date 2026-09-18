"""Reject known-vulnerable installer tools in new public portable builds.

This dated minimum-version check does not replace an up-to-date advisory scan.
"""
from pathlib import Path
from packaging.version import InvalidVersion, Version

MINIMUM_TOOL_VERSIONS = {'pip': '26.2.0', 'wheel': '0.46.2'}


def runtime_security_blockers(inventory: list[dict], runtime: Path) -> list[str]:
    blockers = []
    tools = [(p.get('name', '').lower().replace('_', '-'), p.get('version', '')) for p in inventory]
    # Updating installed pip alone leaves ensurepip able to restore vulnerable pip.
    bundled = runtime / 'Lib' / 'ensurepip' / '_bundled'
    for file in bundled.glob('pip-*.whl'):
        tools.append(('pip', file.name.split('-')[1]))
    for name, value in tools:
        if name not in MINIMUM_TOOL_VERSIONS:
            continue
        try:
            valid = Version(value) >= Version(MINIMUM_TOOL_VERSIONS[name])
        except InvalidVersion:
            valid = False
        if not valid:
            blockers.append(f'{name} {value}: public runtime requires >= {MINIMUM_TOOL_VERSIONS[name]} (including bootstrap wheels)')
    return blockers
