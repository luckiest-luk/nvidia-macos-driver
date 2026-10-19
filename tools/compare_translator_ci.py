#!/usr/bin/env python3
"""Detect new library-test failures compared with the unchanged v2.3.7 translator."""
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile

from restore_translator_fixtures import TEST_NAMES

BASE = 'e2b0e547a18841b7d944850b03c9e605df76720b'
ROOT = Path(__file__).resolve().parents[1]
OUT = Path(os.environ.get('UI_CI_OUT', str(ROOT / '.local/cloud-ci/output'))) / 'tests'


def parse_results(text, code):
    match = re.search(r'test result: (?:ok|FAILED)\. (\d+) passed; (\d+) failed; (\d+) ignored;', text)
    if not match or code not in (0, 101):
        raise ValueError('Test compilation or execution did not complete; inspect the log.')
    states = dict(re.findall(r'^test (.+) \.\.\. (ok|FAILED|ignored)(?:, [^\n]*)?$', text, re.M))
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


def fixture_test_failures(results):
    states = {name.rsplit('::', 1)[-1]: value for name, value in results.items()}
    return [name for name in TEST_NAMES if states.get(name) != 'ok']


def run_logged(args, label):
    log = OUT / (label + '.log')
    print('Running:', shlex.join(args), flush=True)
    with log.open('w') as out:
        code = subprocess.run(args, stdout=out, stderr=subprocess.STDOUT).returncode
    text = log.read_text(errors='replace')
    if code:
        print(f'{label}: exit code {code}; last 100 log lines:', flush=True)
        print('\n'.join(text.splitlines()[-100:]), flush=True)
    return text, code


def prepare_dependencies(baseline, candidate):
    relative = Path('translator/translator')
    before, after = baseline / relative, candidate / relative
    if (before / 'Cargo.toml').read_bytes() != (after / 'Cargo.toml').read_bytes():
        raise RuntimeError('Dependency manifests differ; cannot use one lock file for both test runs.')
    shutil.copyfile(before / 'Cargo.lock', OUT / 'baseline-original.Cargo.lock')
    # Upstream's lock file needs normalization. Preserve existing versions where
    # possible, then use the exact same resolved lock for both source snapshots.
    _, code = run_logged(['cargo', 'update', '--workspace', '--manifest-path',
                          str(before / 'Cargo.toml')], 'dependencies')
    if code:
        raise RuntimeError(f'Dependency preparation failed (exit code {code}); see dependencies.log.')
    shutil.copyfile(before / 'Cargo.lock', OUT / 'resolved.Cargo.lock')
    shutil.copyfile(before / 'Cargo.lock', after / 'Cargo.lock')


def missing_fixtures(source):
    source = source.resolve()
    crate = source / 'translator/translator'
    missing = []
    for path in sorted((crate / 'src').rglob('*.rs')):
        for relative in re.findall(r'include_(?:str|bytes)!\s*\(\s*"([^"]+)"', path.read_text()):
            target = (path.parent / relative).resolve()
            if not target.is_file():
                missing.append({'source': str(path.relative_to(source)),
                                'fixture': str(target.relative_to(source))})
    return missing


def restore_fixtures(baseline, candidate):
    output = OUT / 'restored-fixtures'
    _, code = run_logged([sys.executable, str(ROOT / 'tools/restore_translator_fixtures.py'),
                          '--output', str(output)], 'fixtures')
    if code:
        raise RuntimeError(f'Fixture preparation failed (exit code {code}); see fixtures.log.')
    provenance = json.loads((output / 'source.json').read_text())
    for item in provenance['files']:
        relative = Path('translator/translator/validation/fixtures/public') / item['name']
        data = (output / item['name']).read_bytes()
        for source in (baseline, candidate):
            target = source / relative
            if target.exists() and target.read_bytes() != data:
                raise RuntimeError(f'Test fixture differs from the recorded regression input: {relative}')
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
    return provenance


def run_tests(source, label):
    args = ['cargo', 'test', '--locked', '--lib', '--target', 'x86_64-apple-darwin',
            '--features', 'serde', '--manifest-path', str(source / 'translator/translator/Cargo.toml'),
            '--', '--test-threads=1', '--format=pretty', '--color=never']
    print('Running library tests:', label, flush=True)
    text, code = run_logged(args, label)
    try:
        return parse_results(text, code)
    except ValueError as exc:
        raise RuntimeError(f'{label}: {exc} Cargo exit code: {code}. Log: {OUT / (label + ".log")}') from exc


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
            candidate = baseline / 'candidate'
            shutil.copytree(ROOT / 'translator/translator', candidate / 'translator/translator',
                            ignore=shutil.ignore_patterns('target', '.local', '.git', '__pycache__'))
            report['missing_fixtures'] = {label: missing_fixtures(source) for label, source in
                                          [('baseline', baseline), ('candidate', candidate)]}
            if any(report['missing_fixtures'].values()):
                report['restored_fixtures'] = restore_fixtures(baseline, candidate)
                report['missing_fixtures'] = {label: missing_fixtures(source) for label, source in
                                              [('baseline', baseline), ('candidate', candidate)]}
            if any(report['missing_fixtures'].values()):
                raise RuntimeError('Compile-time test fixtures are missing; restore the real .ll files '
                                   'before comparing tests. See missing_fixtures in this report.')
            prepare_dependencies(baseline, candidate)
            before = run_tests(baseline, 'baseline')
            after = run_tests(candidate, 'candidate')
        report.update({'baseline': before, 'candidate': after, **compare(before, after)})
        report['fixture_test_failures'] = {label: fixture_test_failures(results) for label, results in
                                          [('baseline', before), ('candidate', after)]}
        report['state'] = 'passed' if not any(report[key] for key in
            ('new_failures', 'missing_tests', 'newly_ignored')) and not any(
                report['fixture_test_failures'].values()) else 'failed'
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        report['error'] = str(exc)
    (OUT / 'comparison.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({key: value for key, value in report.items()
                      if key not in ('baseline', 'candidate')}, indent=2))
    if report['state'] != 'passed':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
