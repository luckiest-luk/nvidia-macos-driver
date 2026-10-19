#!/usr/bin/env python3
"""Recover omitted test inputs from the published translator's upstream Git revision."""
import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile
import tempfile
from urllib.request import Request, urlopen

VERSION = '0.1.0'
REPOSITORY = 'https://github.com/steelbrain/metal2vulkan.git'
FIXTURES = (
    'kernel_nullable_branch_cursor.ll',
    'kernel_nested_loop_constant_weight_walk.ll',
    'vertex_narrow_attributes.ll',
)
TESTS = (
    ('buffers.rs', 'native_raw_pointer_phi_admits_a_null_arm'),
    ('buffers.rs', 'native_nested_loop_raw_induction_cycle_stays_raw'),
    ('interface.rs', 'native_vertex_narrow_integer_attributes_use_32_bit_fetch_interface'),
)
ROOT = Path(__file__).resolve().parents[1]


def download(url):
    print('Downloading:', url, flush=True)
    request = Request(url, headers={'User-Agent': 'nvidia-macos-driver-ci'})
    with urlopen(request, timeout=60) as response:
        return response.read()


def published_revision():
    # The release is immutable. Its registry checksum authenticates the archive;
    # Cargo's VCS metadata pins the Git source even after upstream reorganizes it.
    index = download('https://index.crates.io/me/ta/metal2vulkan')
    release = next(json.loads(line) for line in index.splitlines()
                   if json.loads(line)['vers'] == VERSION)
    archive = download(f'https://static.crates.io/crates/metal2vulkan/metal2vulkan-{VERSION}.crate')
    checksum = hashlib.sha256(archive).hexdigest()
    if checksum != release['cksum']:
        raise RuntimeError('Published translator archive checksum mismatch.')
    with tarfile.open(fileobj=io.BytesIO(archive), mode='r:gz') as package:
        info = package.extractfile(f'metal2vulkan-{VERSION}/.cargo_vcs_info.json')
        if info is None:
            raise RuntimeError('Published translator has no Cargo VCS metadata.')
        vcs = json.load(info)
    revision = vcs['git']['sha1']
    prefix = PurePosixPath(vcs.get('path_in_vcs', ''))
    if not re.fullmatch(r'[0-9a-f]{40}', revision) or prefix.is_absolute() or '..' in prefix.parts:
        raise RuntimeError('Invalid Git revision or package path in Cargo VCS metadata.')
    return revision, prefix, checksum


def historical_revision(repository, source):
    # The vendored translator may be newer than its published 0.1.0 release.
    # Search only pre-import history, and require the three test bodies to match
    # the actual vendored tests before accepting that snapshot's fixtures.
    imported = subprocess.check_output(
        ['git', '-C', str(source), 'log', '--reverse', '--format=%cI', '--',
         'translator/translator/src/native/tests/interface.rs'], text=True).splitlines()[0]
    subprocess.run(['git', '-C', repository, 'fetch', '--unshallow', REPOSITORY,
                    '+refs/heads/master:refs/heads/upstream'], check=True)
    paths = ['src/native/tests/buffers.rs', 'src/native/tests/interface.rs',
             'validation/fixtures/public']
    head = subprocess.check_output(['git', '-C', repository, 'rev-list', '-1',
                                   '--before=' + imported, 'upstream'], text=True).strip()
    revisions = subprocess.check_output(['git', '-C', repository, 'log', '--format=%H',
                                        '--before=' + imported, 'upstream', '--', *paths],
                                       text=True).splitlines()
    expected = []
    for filename, name in TESTS:
        text = subprocess.check_output(['git', '-C', str(source), 'show',
                                      'HEAD:translator/translator/src/native/tests/' + filename], text=True)
        expected.append(test_body(text, name))
    for revision in dict.fromkeys([head, *revisions]):
        if not revision:
            continue
        for (filename, name), body in zip(TESTS, expected):
            result = subprocess.run(['git', '-C', repository, 'show',
                                     revision + ':src/native/tests/' + filename],
                                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
            if result.returncode or test_body(result.stdout, name) != body:
                break
        else:
            present = all(subprocess.run(['git', '-C', repository, 'cat-file', '-e',
                                         revision + ':validation/fixtures/public/' + name],
                                        stderr=subprocess.DEVNULL).returncode == 0 for name in FIXTURES)
            if present:
                return revision
    raise RuntimeError('No upstream snapshot has all three fixtures and matching vendored test bodies.')


def test_body(text, name):
    match = re.search(r'fn ' + re.escape(name) + r'\(\)\s*\{.*?^\}', text, re.S | re.M)
    return re.sub(r'\s+', '', match.group(0)) if match else None


def restore(output, source=ROOT):
    output.mkdir(parents=True, exist_ok=True)
    provenance = output / 'source.json'
    provenance.unlink(missing_ok=True)
    revision, prefix, checksum = published_revision()
    published = revision
    files = []
    with tempfile.TemporaryDirectory(prefix='translator-upstream-', dir=output) as tmp:
        subprocess.run(['git', 'init', '--bare', tmp], check=True)
        subprocess.run(['git', '-C', tmp, 'fetch', '--depth=1', REPOSITORY, revision], check=True)
        actual = subprocess.check_output(['git', '-C', tmp, 'rev-parse', 'FETCH_HEAD'], text=True).strip()
        if actual != revision:
            raise RuntimeError('Fetched translator revision does not match its published release.')
        present = all(subprocess.run(['git', '-C', tmp, 'cat-file', '-e',
                                     f'{revision}:{prefix / "validation/fixtures/public" / name}'],
                                    stderr=subprocess.DEVNULL).returncode == 0 for name in FIXTURES)
        method = 'published-revision'
        if not present:
            print('Published revision lacks inputs; locating matching pre-import upstream tests.', flush=True)
            revision = historical_revision(tmp, source)
            prefix = PurePosixPath('.')
            method = 'matching-pre-import-tests'
        # Load all inputs before writing any, so an incomplete recovery never
        # leaves a provenance record claiming that all three files are present.
        contents = {}
        for name in FIXTURES:
            path = str(prefix / 'validation/fixtures/public' / name)
            data = subprocess.check_output(['git', '-C', tmp, 'show', f'{revision}:{path}'])
            if not data.strip() or b'define ' not in data:
                raise RuntimeError(f'Upstream fixture is empty or is not LLVM IR: {path}')
            contents[name] = data
            files.append({'name': name, 'upstream_path': path,
                          'sha256': hashlib.sha256(data).hexdigest()})
        for name, data in contents.items():
            (output / name).write_bytes(data)
    report = {'repository': REPOSITORY, 'revision': revision, 'selection': method, 'crate_version': VERSION,
              'published_revision': published, 'crate_sha256': checksum, 'files': files}
    provenance.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2), flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    restore(args.output.resolve())


if __name__ == '__main__':
    main()
