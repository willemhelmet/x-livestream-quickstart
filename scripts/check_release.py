"""Check the publication allowlist without printing secret values.

Run before publishing; --tracked additionally checks the exact Git index.
This targeted check is not a substitute for a dedicated secret scanner.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path, PurePosixPath
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
ROOT_FILES = {'.env.example', '.gitignore', 'README.md', 'NOTICE', 'LICENSE',
              'run.py', 'server.py', 'publisher.py', 'requirements.txt', 'requirements-local.txt'}
WEB_FILES = {'web/index.html', 'web/app.js', 'web/style.css'}
PATTERNS = (
    re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})\b'),
    re.compile(r'\b(?:xai-|sk-)[A-Za-z0-9_-]{24,}\b'),
    re.compile(r'rtmps?://[^\s\x22\x27<>]+/x/[A-Za-z0-9_-]{16,}'),
)


def allowed(name):
    path = PurePosixPath(name)
    if '..' in path.parts or path.is_absolute():
        return False
    if name in ROOT_FILES or name in WEB_FILES:
        return True
    return (len(path.parts) > 1 and path.parts[0] in ('runtime', 'tests', 'scripts')
            and all(not part.startswith('.') and part != '__pycache__' for part in path.parts)
            and path.suffix == '.py')


def local_secrets(root):
    """Compare against saved values in memory only, never print them."""
    values = []
    for relative in ('.local/credentials.json', '.local/x-destination.json'):
        path = root / relative
        if path.is_file():
            data = json.loads(path.read_text())
            values.extend(value for key, value in data.items()
                          if key != 'serverUrl' and isinstance(value, str) and len(value) >= 8)
    env = root / '.env'
    if env.is_file():
        for line in env.read_text().splitlines():
            key, separator, value = line.partition('=')
            if separator and any(word in key.upper() for word in ('KEY', 'TOKEN', 'SECRET', 'PASSWORD')):
                value = value.strip().strip('\"\x27')
                if len(value) >= 8:
                    values.append(value)
    return values


def inspect(root, tracked=False):
    index = {}
    if tracked:
        result = subprocess.run(['git', 'ls-files', '-s', '-z'], cwd=root, check=True, capture_output=True)
        for entry in result.stdout.decode().split('\0'):
            if entry:
                metadata, name = entry.split('\t', 1)
                index[name] = metadata.split()
        names = sorted(index)
    else:
        names = sorted(name for path in root.rglob('*')
                       if path.is_file() and allowed(name := path.relative_to(root).as_posix()))
    findings = []
    secrets = local_secrets(root)
    if not names:
        findings.append('No publication files found.')
    for name in names:
        path = root / name
        if not allowed(name):
            findings.append(f'{name}: outside publication allowlist')
            continue
        if any(parent.is_symlink() for parent in (path, *path.parents) if parent != root.parent):
            findings.append(f'{name}: symlinks are not allowed in publication')
            continue
        if not path.is_file() or path.stat().st_size > 1_000_000:
            findings.append(f'{name}: missing or unexpectedly large source file')
            continue
        if tracked:
            mode, blob, stage = index[name]
            if mode not in ('100644', '100755') or stage != '0':
                findings.append(f'{name}: unsupported index entry')
                continue
            content = subprocess.run(['git', 'cat-file', 'blob', blob], cwd=root,
                                     check=True, capture_output=True).stdout.decode()
        else:
            content = path.read_text()
        if any(secret in content for secret in secrets):
            findings.append(f'{name}: contains a saved private credential')
        if any(pattern.search(content) for pattern in PATTERNS):
            findings.append(f'{name}: potential embedded credential')
        if name == '.env.example' and any(line.partition('=')[2].strip()
                                          for line in content.splitlines() if line and not line.startswith('#')):
            findings.append(f'{name}: example credential values must be blank')
        if re.search(r'/(?:Users|home)/[A-Za-z0-9_.-]+/|/private/(?:tmp|var)/[A-Za-z0-9_.-]+', content):
            findings.append(f'{name}: machine-specific absolute path')
    return names, findings


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tracked', action='store_true')
    parser.add_argument('--list', action='store_true', help='Print candidate relative paths, never contents.')
    args = parser.parse_args()
    files, findings = inspect(ROOT, args.tracked)
    if args.list:
        print('\n'.join(files))
    print(f'Checked {len(files)} publication files; {len(findings)} finding(s).')
    for finding in findings:
        print(finding)
    raise SystemExit(bool(findings))
