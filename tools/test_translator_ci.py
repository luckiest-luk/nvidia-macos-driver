"""Check fixture provisioning and CI failure handling without Rust or network."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import compare_translator_ci as comparison
import restore_translator_fixtures as recovery


class FixtureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()

    def test_fixture_bytes_and_provenance_are_preserved(self):
        output = self.root / 'inputs'
        with contextlib.redirect_stdout(io.StringIO()):
            report = recovery.restore(output)
        self.assertFalse(report['original_upstream_inputs'])
        self.assertEqual(len(report['files']), 3)
        for item in report['files']:
            data = (recovery.SOURCE / item['name']).read_bytes()
            self.assertEqual((output / item['name']).read_bytes(), data)
            self.assertEqual(item['sha256'], hashlib.sha256(data).hexdigest())

    def test_missing_input_does_not_generate_success_provenance(self):
        output = self.root / 'output'
        source = self.root / 'missing'
        source.mkdir()
        with self.assertRaises(FileNotFoundError):
            recovery.restore(output, source)
        self.assertFalse((output / 'source.json').exists())

    def test_both_snapshots_use_identical_fixtures_and_conflicts_fail(self):
        baseline, candidate = self.root / 'baseline', self.root / 'candidate'
        with patch.object(comparison, 'OUT', self.root), contextlib.redirect_stdout(io.StringIO()):
            comparison.restore_fixtures(baseline, candidate)
            for name in recovery.FIXTURES:
                relative = Path('translator/translator/validation/fixtures/public') / name
                self.assertEqual((baseline / relative).read_bytes(), (candidate / relative).read_bytes())
            (candidate / relative).write_bytes(b'different input')
            with self.assertRaisesRegex(RuntimeError, 'differs from the recorded regression input'):
                comparison.restore_fixtures(baseline, candidate)

    def test_known_failures_regressions_missing_and_ignored_tests(self):
        result = comparison.compare({'known': 'FAILED', 'regression': 'ok', 'lost': 'ok', 'skip': 'ok'},
                                    {'known': 'FAILED', 'regression': 'FAILED', 'skip': 'ignored'})
        self.assertEqual(result['new_failures'], ['regression'])
        self.assertEqual(result['missing_tests'], ['lost'])
        self.assertEqual(result['newly_ignored'], ['skip'])
        with self.assertRaises(ValueError):
            comparison.parse_results('error: compilation did not finish', 101)
        states = {name: 'ok' for name in recovery.TEST_NAMES}
        self.assertEqual(comparison.fixture_test_failures(states), [])
        states[recovery.TEST_NAMES[0]] = 'FAILED'
        self.assertEqual(comparison.fixture_test_failures(states), [recovery.TEST_NAMES[0]])

    def test_real_baseline_archive_runs_with_same_lock_and_inputs(self):
        output = self.root / 'comparison'
        original_lock = comparison.ROOT / 'translator/translator/Cargo.lock'
        original_bytes = original_lock.read_bytes()
        actual_run_logged = comparison.run_logged
        runs = []

        def command(args, label):
            if label == 'fixtures':
                return actual_run_logged(args, label)
            manifest = Path(args[args.index('--manifest-path') + 1])
            if label == 'dependencies':
                manifest.with_name('Cargo.lock').write_text('normalized test lock\n')
                return '', 0
            self.assertIn('--locked', args)
            self.assertFalse(comparison.missing_fixtures(manifest.parents[2]))
            self.assertEqual(manifest.with_name('Cargo.lock').read_text(), 'normalized test lock\n')
            runs.append(label)
            lines = ''.join(f'test native::tests::{name} ... ok\n' for name in recovery.TEST_NAMES)
            return (lines + 'test result: ok. 3 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out;\n'), 0

        with patch.object(comparison, 'OUT', output), \
             patch.object(comparison, 'run_logged', side_effect=command), \
             contextlib.redirect_stdout(io.StringIO()):
            comparison.main()
        report = json.loads((output / 'comparison.json').read_text())
        self.assertEqual(report['state'], 'passed')
        self.assertEqual(runs, ['baseline', 'candidate'])
        self.assertFalse(any(report['missing_fixtures'].values()))
        self.assertFalse(report['restored_fixtures']['original_upstream_inputs'])
        self.assertEqual(original_lock.read_bytes(), original_bytes)

        # A broken replacement input must not be accepted just because both
        # snapshots fail the same test (the usual known-failure comparison).
        def broken_command(args, label):
            text, code = command(args, label)
            if label in ('baseline', 'candidate'):
                text = text.replace(recovery.TEST_NAMES[0] + ' ... ok',
                                    recovery.TEST_NAMES[0] + ' ... FAILED')
                return text.replace('3 passed; 0 failed;', '2 passed; 1 failed;'), 101
            return text, code

        with patch.object(comparison, 'OUT', output), \
             patch.object(comparison, 'run_logged', side_effect=broken_command), \
             contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as exit_status:
            comparison.main()
        self.assertEqual(exit_status.exception.code, 1)
        report = json.loads((output / 'comparison.json').read_text())
        self.assertEqual(report['state'], 'failed')
        self.assertEqual(report['new_failures'], [])
        self.assertEqual(report['fixture_test_failures']['baseline'], [recovery.TEST_NAMES[0]])


if __name__ == '__main__':
    unittest.main()
