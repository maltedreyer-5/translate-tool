# SPDX-FileCopyrightText: 2026 Malte Dreyer
# SPDX-License-Identifier: MIT
"""
Repository checks that are not unit tests (run by CI, see CONTRIBUTING.md):

- every relative link in Markdown files resolves;
- no runtime state is tracked by git (log files, temp/, local config, .env,
  bytecode);
- the version is the same in src/__init__.py, CITATION.cff and CHANGELOG.md;
- every Python and YAML file carries an SPDX license header;
- no wording that refers to unpublished history.

Exit code 0 if all checks pass.
"""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

RUNTIME_STATE = [
    re.compile(r'(^|/)translation_app\.log(\.\d+)?$'),
    re.compile(r'^temp/(?!\.gitkeep$).+'),
    re.compile(r'^config/config\.local\.yaml$'),
    re.compile(r'(^|/)\.env$'),
    re.compile(r'(^|/)__pycache__/'),
    re.compile(r'\.py[cod]$'),
    re.compile(r'^RELEASE-CHECKLIST\.md$'),
]

HISTORY = re.compile(
    r'\b(previously|no longer|since version|fixed in (version|v?\d)|in earlier versions|'
    r'the old (implementation|version)|has been (removed|replaced))\b',
    re.IGNORECASE)


def tracked_files():
    try:
        out = subprocess.run(['git', 'ls-files'], cwd=ROOT, capture_output=True,
                             text=True, check=True).stdout
        return [line for line in out.splitlines() if line]
    except (OSError, subprocess.CalledProcessError):
        return None


def check_links(files):
    errors = []
    for name in files:
        if not name.endswith('.md'):
            continue
        path = ROOT / name
        for target in re.findall(r'\]\(([^)\s]+)\)', path.read_text(encoding='utf-8')):
            if target.startswith(('http://', 'https://', 'mailto:', '#')):
                continue
            if not (path.parent / target.split('#')[0]).exists():
                errors.append(f"{name}: unresolved link {target}")
    return errors


def check_runtime_state(files):
    return [f"runtime state is tracked: {name}" for name in files
            if any(p.search(name) for p in RUNTIME_STATE)]


def check_version():
    init = (ROOT / 'src' / '__init__.py').read_text(encoding='utf-8')
    version = re.search(r'__version__\s*=\s*"([^"]+)"', init).group(1)
    errors = []
    cff = (ROOT / 'CITATION.cff').read_text(encoding='utf-8')
    if f'version: "{version}"' not in cff:
        errors.append(f"CITATION.cff does not state version {version}")
    changelog = (ROOT / 'CHANGELOG.md').read_text(encoding='utf-8')
    first = re.search(r'^## \[([^\]]+)\]', changelog, re.MULTILINE)
    if not first or first.group(1) != version:
        errors.append(f"latest CHANGELOG.md entry is not {version}")
    return errors


def check_spdx(files):
    errors = []
    for name in files:
        if name.endswith(('.py', '.yaml', '.yml')) and not name.startswith('.github/'):
            head = (ROOT / name).read_text(encoding='utf-8')[:400]
            if 'SPDX-License-Identifier: MIT' not in head:
                errors.append(f"{name}: missing SPDX header")
    return errors


def check_history_wording(files):
    errors = []
    for name in files:
        if name == 'tools/check_repo.py' or not name.endswith(('.py', '.md', '.yaml', '.txt')):
            continue
        for number, line in enumerate((ROOT / name).read_text(encoding='utf-8').splitlines(), 1):
            if HISTORY.search(line):
                errors.append(f"{name}:{number}: refers to history: {line.strip()[:80]}")
    return errors


def main():
    files = tracked_files()
    if files is None:
        print("not a git checkout: checking all files, runtime-state check skipped")
        files = [str(p.relative_to(ROOT)) for p in ROOT.rglob('*')
                 if p.is_file() and '.git' not in p.parts
                 and not any(r.search(str(p.relative_to(ROOT))) for r in RUNTIME_STATE)]
        runtime = []
    else:
        runtime = check_runtime_state(files)
    errors = (check_links(files) + runtime + check_version()
              + check_spdx(files) + check_history_wording(files))
    for error in errors:
        print(f"FAIL {error}")
    print("all repository checks passed" if not errors else f"{len(errors)} problem(s)")
    return 1 if errors else 0


if __name__ == '__main__':
    sys.exit(main())
