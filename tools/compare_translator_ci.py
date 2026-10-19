#!/usr/bin/env python3
"""Detect new library-test failures compared with the unchanged v2.3.7 translator."""
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

BASE = 'e2b0e547a18841b7d944850b03c9e605df76720b'
ROOT = Path(__file__).resolve().parents[1]
OUT = Path(os.environ.get('UI_CI_OUT', str(ROOT / '.local/cloud-ci/output'))) / 'tests'


def parse_results(text, code):
    match = re.search(r'test result: (?:ok|FAILED)\. (\d+) passed; (\d+) failed; (\d+) ignored;', text)
    if not match or code not in (0, 101):
        raise ValueError('Test compilation or execution did not complete; inspect the log.')
    states = dict(re.findall(r'^test (.+) \.\.\. (ok|FAILED|ignored)$', text, re.M))
    if not states:
        raise ValueError('No library tests ran; refusing an empty comparison.')
    counts = {state: sum(value == state for value in states.values()) for state in ('ok', 'FAILED', 'ignored')}
    if [counts['ok'], counts['FAILED'], counts['ignored']] != list(map(int, match.groups())):
        raise ValueError('Could not identify every test result in the summary.')
    return states


def compare(before, after):
    return {'new_failures': sorted(name for name, value in after.items()
                                   if value == 'FAILED' and before.get(name) != 'FAILED'),
            'missing_tests': sorted(set(before) - set(after)),
            'newly_ignored': sorted(name for name, value in after.items()
                                    if value == 'ignored' and before.get(name) in ('ok', 'FAILED'))}


def run_tests(source, label):
    log = OUT / (label + '.log')
    args = ['cargo', 'test', '--locked', '--lib', '--target', 'x86_64-apple-darwin',
            '--features', 'serde', '--manifest-path', str(source / 'translator/translator/Cargo.toml'),
            '--', '--test-threads=1', '--format=pretty', '--color=never']
    print('Running library tests:', label, flush=True)
    with log.open('w') as out:
        code = subprocess.run(args, stdout=out, stderr=subprocess.STDOUT).returncode
    try:
        return parse_results(log.read_text(), code)
    except ValueError as exc:
        raise RuntimeError(f'{label}: {exc} Log: {log}') from exc


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    env = os.environ
    env.setdefault('CARGO_TARGET_DIR', str(ROOT / '.local/cloud-ci/target'))
    env.setdefault('CARGO_BUILD_JOBS', '3')
    env.setdefault('CARGO_PROFILE_TEST_DEBUG', '0')
    env.setdefault('CARGO_PROFILE_DEV_DEBUG', '0')
    report = {'base_commit': BASE, 'state': 'incomplete'}
    try:
        subprocess.run(['git', '-C', str(ROOT), 'cat-file', '-e', BASE + '^{commit}'], check=True)
        with tempfile.TemporaryDirectory(prefix='translator-baseline-', dir=OUT) as tmp:
            baseline = Path(tmp)
            archive = baseline / 'source.tar'
            with archive.open('wb') as out:
                subprocess.run(['git', '-C', str(ROOT), 'archive', BASE, 'translator/translator'],
                               stdout=out, check=True)
            subprocess.run(['tar', '-xf', str(archive), '-C', str(baseline)], check=True)
            before = run_tests(baseline, 'baseline')
            after = run_tests(ROOT, 'candidate')
        report.update({'baseline': before, 'candidate': after, **compare(before, after)})
        report['state'] = 'passed' if not any(report[key] for key in
            ('new_failures', 'missing_tests', 'newly_ignored')) else 'failed'
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        report['error'] = str(exc)
    (OUT / 'comparison.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({key: value for key, value in report.items()
                      if key not in ('baseline', 'candidate')}, indent=2))
    if report['state'] != 'passed':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
