"""Exercise CI recovery with a local upstream repository; no Rust or network needed."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

import compare_translator_ci as comparison
import restore_translator_fixtures as recovery


class FixtureRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.upstream = self.root / 'upstream'
        self.upstream.mkdir()
        self.prefix = Path('translator')
        self.inputs = {}
        for name in recovery.FIXTURES:
            # Synthetic inputs belong only to this recovery test, never to the
            # translator's actual regression tests or comparison artifacts.
            data = f'; Recovery transport test: {name}\ndefine void @example() {{ ret void }}\n'.encode()
            self.inputs[name] = data
            path = self.upstream / self.prefix / 'validation/fixtures/public' / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        self.git('init', '--quiet', '--initial-branch=master')
        self.commit()
        self.addCleanup(patch.stopall)
        patch.object(recovery, 'REPOSITORY', self.upstream.as_uri()).start()
        patch.object(recovery, 'download', side_effect=self.download).start()

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.upstream), *args], stderr=subprocess.PIPE)

    def commit(self):
        self.git('add', '.')
        self.git('-c', 'user.name=CI test', '-c', 'user.email=ci@example.invalid',
                 'commit', '--quiet', '-m', 'Recovery test inputs')
        self.revision = self.git('rev-parse', 'HEAD').decode().strip()
        metadata = json.dumps({'git': {'sha1': self.revision}, 'path_in_vcs': str(self.prefix)}).encode()
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode='w:gz') as package:
            member = tarfile.TarInfo('metal2vulkan-0.1.0/.cargo_vcs_info.json')
            member.size = len(metadata)
            package.addfile(member, io.BytesIO(metadata))
        self.archive = archive.getvalue()
        self.checksum = hashlib.sha256(self.archive).hexdigest()

    def download(self, url):
        if url.startswith('https://index.crates.io/'):
            return json.dumps({'vers': '0.1.0', 'cksum': self.checksum}).encode()
        return self.archive

    def test_recovery_uses_published_revision_and_preserves_bytes(self):
        output = self.root / 'recovered'
        with contextlib.redirect_stdout(io.StringIO()):
            report = recovery.restore(output)
        self.assertEqual(report['revision'], self.revision)
        self.assertEqual(report['crate_sha256'], self.checksum)
        self.assertEqual(len(report['files']), 3)
        for item in report['files']:
            self.assertEqual((output / item['name']).read_bytes(), self.inputs[item['name']])
            self.assertEqual(item['sha256'], hashlib.sha256(self.inputs[item['name']]).hexdigest())

    def test_checksum_mismatch_stops_recovery(self):
        self.archive += b'tampered'
        with self.assertRaisesRegex(RuntimeError, 'checksum mismatch'):
            recovery.published_revision()

    def test_missing_upstream_file_leaves_no_success_record(self):
        path = self.upstream / self.prefix / 'validation/fixtures/public' / recovery.FIXTURES[-1]
        path.unlink()
        self.commit()
        output = self.root / 'incomplete'
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaisesRegex(RuntimeError, 'No upstream snapshot'):
            recovery.restore(output)
        self.assertFalse((output / 'source.json').exists())
        self.assertFalse(any(output.glob('*.ll')))

    def test_newer_vendored_tests_can_recover_from_matching_pre_import_history(self):
        for filename, _ in recovery.TESTS:
            path = self.upstream / 'src/native/tests' / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(subprocess.check_output(
                ['git', '-C', str(recovery.ROOT), 'show',
                 'HEAD:translator/translator/src/native/tests/' + filename]))
        for name, data in self.inputs.items():
            path = self.upstream / 'validation/fixtures/public' / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        (self.upstream / self.prefix / 'validation/fixtures/public' / recovery.FIXTURES[-1]).unlink()
        with patch.dict('os.environ', {'GIT_AUTHOR_DATE': '2026-10-01T00:00:00Z',
                                      'GIT_COMMITTER_DATE': '2026-10-01T00:00:00Z'}):
            self.commit()
        output = self.root / 'historical'
        with contextlib.redirect_stdout(io.StringIO()):
            report = recovery.restore(output)
        self.assertEqual(report['selection'], 'matching-pre-import-tests')
        self.assertEqual(report['revision'], self.revision)
        for name, data in self.inputs.items():
            self.assertEqual((output / name).read_bytes(), data)

    def test_both_snapshots_receive_same_inputs_and_conflicts_fail(self):
        output = self.root / 'reports'
        output.mkdir()
        baseline, candidate = self.root / 'baseline', self.root / 'candidate'

        def recover_command(args, label):
            self.assertEqual(label, 'fixtures')
            recovery.restore(Path(args[args.index('--output') + 1]))
            return '', 0

        with patch.object(comparison, 'OUT', output), \
             patch.object(comparison, 'run_logged', side_effect=recover_command), \
             contextlib.redirect_stdout(io.StringIO()):
            comparison.restore_fixtures(baseline, candidate)
            for name, data in self.inputs.items():
                relative = Path('translator/translator/validation/fixtures/public') / name
                self.assertEqual((baseline / relative).read_bytes(), data)
                self.assertEqual((candidate / relative).read_bytes(), data)
            (candidate / relative).write_bytes(b'different input')
            with self.assertRaisesRegex(RuntimeError, 'differs from the pinned upstream input'):
                comparison.restore_fixtures(baseline, candidate)

    def test_regressions_are_not_treated_as_known_failures(self):
        result = comparison.compare({'known': 'FAILED', 'regression': 'ok', 'lost': 'ok'},
                                    {'known': 'FAILED', 'regression': 'FAILED'})
        self.assertEqual(result['new_failures'], ['regression'])
        self.assertEqual(result['missing_tests'], ['lost'])
        with self.assertRaises(ValueError):
            comparison.parse_results('error: compilation did not finish', 101)

    def test_full_comparison_recovers_real_tree_missing_inputs_without_mutating_worktree(self):
        output = self.root / 'comparison'
        original_lock = comparison.ROOT / 'translator/translator/Cargo.lock'
        original_bytes = original_lock.read_bytes()
        runs = []

        def command(args, label):
            if label == 'fixtures':
                recovery.restore(Path(args[args.index('--output') + 1]))
                return '', 0
            manifest = Path(args[args.index('--manifest-path') + 1])
            if label == 'dependencies':
                manifest.with_name('Cargo.lock').write_text('normalized test lock\n')
                return '', 0
            self.assertIn('--locked', args)
            self.assertFalse(comparison.missing_fixtures(manifest.parents[2]))
            self.assertEqual(manifest.with_name('Cargo.lock').read_text(), 'normalized test lock\n')
            runs.append(label)
            return ('test fixture_transport_only ... ok\n'
                    'test result: ok. 1 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out;\n'), 0

        with patch.object(comparison, 'OUT', output), \
             patch.object(comparison, 'run_logged', side_effect=command), \
             contextlib.redirect_stdout(io.StringIO()):
            comparison.main()
        report = json.loads((output / 'comparison.json').read_text())
        self.assertEqual(report['state'], 'passed')
        self.assertEqual(runs, ['baseline', 'candidate'])
        self.assertFalse(any(report['missing_fixtures'].values()))
        self.assertEqual(report['restored_fixtures']['revision'], self.revision)
        self.assertEqual(original_lock.read_bytes(), original_bytes)


if __name__ == '__main__':
    unittest.main()
