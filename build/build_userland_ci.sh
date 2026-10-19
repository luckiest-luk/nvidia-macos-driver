#!/bin/bash
# Build user-space files without the author's SSH host or any installation step.
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
OUT=${UI_CI_OUT:-"$ROOT/.local/cloud-ci/output"}
ARTIFACTS="$OUT/artifacts"
REPORTS="$OUT/reports"
: "${VULKAN_INCLUDE:?VULKAN_INCLUDE must point to the Vulkan-Headers include directory}"
test -f "$VULKAN_INCLUDE/vulkan/vulkan_core.h"
mkdir -p "$ARTIFACTS" "$REPORTS"
# A failed rerun must not expose stale binaries as a successful candidate.
rm -f "$ARTIFACTS/NVMTLDriver" "$ARTIFACTS/libnvmtl_translate.dylib" "$ARTIFACTS/SHA256SUMS" "$ARTIFACTS/build.json"
export MACOSX_DEPLOYMENT_TARGET=15.0
export CARGO_TARGET_DIR=${CARGO_TARGET_DIR:-"$ROOT/.local/cloud-ci/target"}
export CARGO_BUILD_JOBS=${CARGO_BUILD_JOBS:-3}
export RUSTFLAGS="${RUSTFLAGS:-} --remap-path-prefix=$ROOT=/src --remap-path-prefix=${CARGO_HOME:-$HOME/.cargo}=/cargo"
{
    git -C "$ROOT" rev-parse HEAD
    rustc --version
    cargo --version
    xcrun clang --version
    xcrun --show-sdk-version
} > "$REPORTS/environment.txt"
echo 'Building the x86_64 translator.'
if ! cargo build --locked --release --target x86_64-apple-darwin \
    --manifest-path "$ROOT/translator/wrapper/Cargo.toml" > "$REPORTS/translator.log" 2>&1; then
    tail -100 "$REPORTS/translator.log"
    exit 1
fi
cp "$CARGO_TARGET_DIR/x86_64-apple-darwin/release/libnvmtl_translate.dylib" "$ARTIFACTS/libnvmtl_translate.dylib"
strip -S "$ARTIFACTS/libnvmtl_translate.dylib"
install_name_tool -id @rpath/libnvmtl_translate.dylib "$ARTIFACTS/libnvmtl_translate.dylib"
echo 'Building the CoreImage plugin.'
if ! xcrun clang -arch x86_64 -mmacosx-version-min=15.0 -bundle -fobjc-arc -O1 -DNVMTL_RELEASE=1 \
    -Wno-protocol -Wno-incomplete-implementation -Werror=unguarded-availability-new \
    -I"$VULKAN_INCLUDE" -o "$ARTIFACTS/NVMTLDriver" "$ROOT/plugin/NVMTLDevice.m" \
    -framework Foundation -framework Metal -framework IOSurface -framework IOKit -framework CoreFoundation \
    > "$REPORTS/plugin.log" 2>&1; then
    tail -100 "$REPORTS/plugin.log"
    exit 1
fi
for name in NVMTLDriver libnvmtl_translate.dylib; do
    binary="$ARTIFACTS/$name"
    test "$(lipo -archs "$binary")" = x86_64
    codesign --force --sign - "$binary"
    codesign --verify --strict "$binary"
    xcrun vtool -show-build "$binary" > "$REPORTS/$name-build.txt"
    minos=$(awk '/minos/{print $2; exit}' "$REPORTS/$name-build.txt")
    test "$minos" = 15.0 || { echo "Unexpected minimum OS for $name: $minos"; exit 1; }
    otool -L "$binary" > "$REPORTS/$name-libraries.txt"
done
python3 - "$ROOT" "$ARTIFACTS" <<'PY'
import hashlib, json, os, subprocess, sys
from pathlib import Path
root, out = map(Path, sys.argv[1:])
files = {name: hashlib.sha256((out / name).read_bytes()).hexdigest()
         for name in ('NVMTLDriver', 'libnvmtl_translate.dylib')}
(out / 'SHA256SUMS').write_text(''.join(f'{sha}  {name}\n' for name, sha in files.items()))
report = {'commit': subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip(),
          'architecture': 'x86_64', 'minimum_macos': '15.0',
          'vulkan_headers_commit': os.environ.get('VULKAN_HEADERS_COMMIT', 'not recorded'),
          'state': 'compiled_runtime_unverified', 'installed': False,
          'scope': 'CoreImage plugin and PR187 translator; no PR273 NVRM binary', 'sha256': files}
(out / 'build.json').write_text(json.dumps(report, indent=2) + '\n')
(out / 'README.txt').write_text('Candidate user-space files only. Not an installer or complete driver package.\n'
    'No GPU runtime validation has run. Check the separate translator-tests job before use.\n'
    'PR273 requires an NVRM rebuild and is not present in these binaries.\n')
PY
echo "Candidate files and checksums: $ARTIFACTS"
