"""Reject developer installation traces in public portable bundles.

This is a focused path/metadata gate, not a replacement for credential auditing.
Third-party authors and license notices must remain intact.
"""
from pathlib import Path
import re

TEXT_SUFFIXES = {'.py', '.json', '.yaml', '.yml', '.txt', '.md', '.cfg', '.ini',
                 '.pth', '.ps1', '.bat', '.html', '.js', '.csv', '.toml'}
PERSONAL_PATH = re.compile(r'(?i)[a-z]:/users/([^/\s"<>]+)/')
LOCAL_INSTALL = re.compile(r'(?i)file:/+[a-z]:/|file:/+/(?:home|Users)/')
# Public upstream wheel build accounts and documented example users, not
# exemptions for this builder's own home/build roots (checked independently).
UPSTREAM_EXAMPLE_USERS = {'runneradmin', 'runner', 'user', 'myuser', 'username'}


def assert_portable_privacy(bundle: Path, *, private_roots=()) -> None:
    """Fail without echoing matching private values into build logs."""
    roots = [str(root).replace('\\', '/').rstrip('/').lower()
             for root in private_roots if str(root)]
    findings = []
    for path in sorted(bundle.rglob('*')):
        if not path.is_file():
            continue
        relative = path.relative_to(bundle).as_posix()
        parts = relative.lower().split('/')
        if 'conda-meta' in parts or '.git' in parts or path.name.lower() == 'cookies.txt':
            findings.append(relative + ': private installation/session metadata')
            continue
        if relative.lower() == 'app/history.json':
            findings.append(relative + ': task history')
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES and path.name.lower() != 'history':
            continue
        text = path.read_bytes().decode('utf-8', errors='replace')
        normalized = text.replace('\\\\', '\\').replace('\\', '/').lower()
        personal = any(match.group(1) not in UPSTREAM_EXAMPLE_USERS
                       for match in PERSONAL_PATH.finditer(normalized))
        local_install = path.name.lower() == 'direct_url.json' and LOCAL_INSTALL.search(normalized)
        if (personal or local_install
                or any(root in normalized for root in roots)):
            findings.append(relative + ': local personal/build path')
    if findings:
        raise RuntimeError('Portable privacy checks failed:\n- ' + '\n- '.join(findings))
