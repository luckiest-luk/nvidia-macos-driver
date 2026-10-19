#!/usr/bin/env python3
"""Check the shipped translator's output against the three fixture test contracts."""
import argparse
import ctypes
import hashlib
import json
from pathlib import Path
import struct

from restore_translator_fixtures import FIXTURES, SOURCE


def instructions(data):
    words = struct.unpack('<' + 'I' * (len(data) // 4), data)
    if len(words) < 5 or words[0] != 0x07230203:
        raise RuntimeError('Translator did not return SPIR-V.')
    offset = 5
    result = []
    while offset < len(words):
        length, opcode = words[offset] >> 16, words[offset] & 0xffff
        if not length or offset + length > len(words):
            raise RuntimeError('Translator returned a truncated SPIR-V instruction.')
        result.append((opcode, words[offset + 1:offset + length]))
        offset += length
    return result


def check_structure(name, data):
    # Opcode/decoration numbers are the SPIR-V binary format. These checks mirror
    # the existing Rust regression assertions, applied to the compiled C API.
    inst = instructions(data)
    def require(condition, message):
        if not condition:
            raise RuntimeError(name + ': ' + message)
    if name.startswith('kernel'):
        binding = next(a[0] for op, a in inst if op == 71 and a[1:] == (33, 0))
        chains = sum(op == 66 and a[2] == binding for op, a in inst)
        nullable = 'nullable' in name
        require(chains >= (5 if nullable else 4), f'only {chains} source InBoundsAccessChains')
        if nullable:
            integers = {a[0] for op, a in inst if op == 21}
            booleans = {a[0] for op, a in inst if op == 20}
            require(any(op == 245 and a[0] in integers for op, a in inst), 'missing integer index Phi')
            require(any(op == 245 and a[0] in booleans for op, a in inst), 'missing nullness Phi')
        return {'source_access_chains': chains}
    capabilities = {a[0] for op, a in inst if op == 17}
    require({22, 39} <= capabilities, 'missing Int16 or Int8 capability')
    require(any(op == 113 for op, a in inst), 'missing UConvert')
    variables = {a[1]: a for op, a in inst if op == 59}
    types = {a[0]: (op, a) for op, a in inst if op in (21, 23, 32)}
    for location in (0, 1):
        variable = next(a[0] for op, a in inst if op == 71 and a[1:] == (30, location))
        pointer = types[variables[variable][0]][1]
        require(pointer[1] == 1, f'location {location} is not an Input pointer')
        opcode, scalar = types[pointer[2]]
        if opcode == 23:
            opcode, scalar = types[scalar[1]]
        require(opcode == 21 and scalar[1] == 32, f'location {location} is not a 32-bit integer input')
    return {'capabilities': sorted(capabilities), 'input_integer_width': 32}


def check(library, output):
    output.mkdir(parents=True, exist_ok=True)
    report_path = output / 'checks.json'
    report_path.unlink(missing_ok=True)
    lib = ctypes.CDLL(str(library.resolve()))
    lib.nvmtl_translate.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.POINTER(ctypes.c_void_p),
                                  ctypes.POINTER(ctypes.c_size_t), ctypes.c_char_p, ctypes.c_size_t]
    lib.nvmtl_translate.restype = ctypes.c_int
    lib.nvmtl_translate_free.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
    results = []
    for name in FIXTURES:
        text = (SOURCE / name).read_bytes()
        pointer, size = ctypes.c_void_p(), ctypes.c_size_t()
        error = ctypes.create_string_buffer(8192)
        stage = b'vertex' if name.startswith('vertex') else b'kernel'
        code = lib.nvmtl_translate(text, stage, ctypes.byref(pointer), ctypes.byref(size), error, len(error))
        if code:
            raise RuntimeError(f'{name}: {error.value.decode(errors="replace")}')
        try:
            data = ctypes.string_at(pointer, size.value)
        finally:
            lib.nvmtl_translate_free(pointer, size)
        (output / (name + '.spv')).write_bytes(data)
        result = {'fixture': name, 'sha256': hashlib.sha256(text).hexdigest(), **check_structure(name, data)}
        print(json.dumps(result), flush=True)
        results.append(result)
    report = {'library_sha256': hashlib.sha256(library.read_bytes()).hexdigest(), 'state': 'passed',
              'original_upstream_inputs': False, 'checks': results, 'gpu_runtime_checked': False}
    report_path.write_text(json.dumps(report, indent=2) + '\n')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    check(args.library, args.output)


if __name__ == '__main__':
    main()
