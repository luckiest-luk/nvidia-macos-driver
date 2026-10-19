#!/bin/bash
# Build only NVRMFB; NVIDIA headers are required, vendor binary archives are not.
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
: "${OGKM:?Set OGKM to the NVIDIA open-gpu-kernel-modules source directory}"
OUT=${1:?Output directory required}
SDK=$(xcrun --show-sdk-path)
KHDR="$SDK/System/Library/Frameworks/Kernel.framework/Headers"
KMS="$OGKM/src/nvidia-modeset"
mkdir -p "$OUT/NVRMFB.kext/Contents/MacOS"
xcrun clang++ -arch x86_64 -fapple-kext -mkernel -nostdinc \
  -I"$KHDR" -I"$ROOT/kexts/NVRMFB" \
  -I"$KMS/os-interface/include" -I"$KMS/kapi/interface" -I"$KMS/kapi/include" \
  -I"$KMS/include" -I"$KMS/interface" -I"$OGKM/src/common/sdk/nvidia/inc" \
  -I"$OGKM/src/common/inc" -I"$OGKM/src/common/shared/inc" \
  -I"$OGKM/src/nvidia/arch/nvalloc/unix/include" \
  -I"$OGKM/src/common/unix/common/inc" -I"$OGKM/src/common/unix/common/utils/interface" \
  -DKERNEL -DKERNEL_PRIVATE -DDRIVER_PRIVATE -DAPPLE -DNeXT -DNV_BSD \
  -std=c++17 -fno-rtti -fno-exceptions -fno-builtin -fno-common \
  -Wall -Wno-unused-parameter -Wno-inconsistent-missing-override -Wno-unused-function -O2 \
  -c "$ROOT/kexts/NVRMFB/fb/nvrm-fb.cpp" -o "$OUT/nvrm-fb.o"
xcrun clang++ -arch x86_64 -fapple-kext -nostdlib -Xlinker -kext \
  -lkmodc++ -lkmod -lcc_kext "$OUT/nvrm-fb.o" \
  -o "$OUT/NVRMFB.kext/Contents/MacOS/NVRMFB"
cp "$ROOT/kexts/NVRMFB/fb/Info.plist" "$OUT/NVRMFB.kext/Contents/Info.plist"
