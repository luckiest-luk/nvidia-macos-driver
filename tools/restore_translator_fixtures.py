#!/usr/bin/env python3
"""Copy the fork's authored regression inputs into both temporary test trees."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = (
    'kernel_nullable_branch_cursor.ll',
    'kernel_nested_loop_constant_weight_walk.ll',
    'vertex_narrow_attributes.ll',
)
SOURCE = ROOT / 'translator/translator/validation/fixtures/public'
TEST_NAMES = (
    'native_raw_pointer_phi_admits_a_null_arm',
    'native_nested_loop_raw_induction_cycle_stays_raw',
    'native_vertex_narrow_integer_attributes_use_32_bit_fetch_interface',
)


def restore(output, source=SOURCE):
    contents = {}
    for name in FIXTURES:
        data = (source / name).read_bytes()
        if not data.strip() or b'define ' not in data:
            raise RuntimeError(f'Fixture is empty or is not LLVM IR: {name}')
        contents[name] = data
    output.mkdir(parents=True, exist_ok=True)
    files = []
    for name, data in contents.items():
        (output / name).write_bytes(data)
        files.append({'name': name, 'sha256': hashlib.sha256(data).hexdigest()})
    report = {'selection': 'fork-authored-regression-inputs',
              'original_upstream_inputs': False, 'files': files}
    (output / 'source.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2), flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    restore(args.output.resolve())


if __name__ == '__main__':
    main()
