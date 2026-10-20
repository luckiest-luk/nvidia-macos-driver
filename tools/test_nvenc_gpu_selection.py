#!/usr/bin/env python3
"""Exercise production NVENC selection at the TU116/TU117 and missing-profile boundaries."""
import pathlib
import subprocess
import tempfile

root = pathlib.Path(__file__).resolve().parents[1]
source = (root / 'plugin/NVMTLDevice.m').read_text()
start = source.index('static bool nvmtl_video_is_blackwell(void)')
selection = source[start:source.index('static void nvmtl_nvdec_register(void)', start)]
fixture = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#define CFSTR(s) s
static const char *generation, *chip, *layout, *deviceName;
static int g_video_blackwell;
static bool nvmtl_gpu_profile_string(const char *key, char *out, size_t n) {
    const char *value = !strcmp(key, "generation") ? generation :
                        !strcmp(key, "chip") ? chip : layout;
    if (!value) return false;
    snprintf(out, n, "%s", value);
    return true;
}
static const char *nvmtl_vk_device_name(void) { return deviceName; }
'''
checks = r'''
static void check(const char *gen, const char *gpu, const char *video,
                  const char *name, const char *avc, const char *hevc) {
    generation = gen; chip = gpu; layout = video; deviceName = name; g_video_blackwell = -1;
    const char *a = nvmtl_nvenc_library_for_this_gpu();
    const char *h = nvmtl_nvenc_hevc_library_for_this_gpu();
    assert(avc ? a && !strcmp(a, avc) : !a);
    assert(hevc ? h && !strcmp(h, hevc) : !h);
}
int main(void) {
    check("Turing GTX 16", "TU116", "classic", "NVK TU116",
          "libnvenc_h264_tu10x.dylib", "libnvenc_hevc_tu10x.dylib");
    check("Turing GTX 16", "TU117", "classic", "NVK TU117", NULL, NULL);
    check("Turing GTX 16", NULL, "classic", "NVK TU116", NULL, NULL);
    check("Turing", "TU106", "classic", "NVK TU106",
          "libnvenc_h264_tu10x.dylib", "libnvenc_hevc_tu10x.dylib");
    check("Ampere", "GA106", "classic", "NVK GA106",
          "libnvenc_h264_ga10x.dylib", "libnvenc_hevc_ga10x.dylib");
    check("Blackwell", "GB206", "blackwell", "NVK GB206",
          "libnvenc_h264.dylib", "libnvenc_hevc.dylib");
    check("Ada", "AD104", "classic", "NVK AD104", NULL, NULL);
    check(NULL, NULL, "classic", "NVK TU116", NULL, NULL);
    check(NULL, NULL, "blackwell", "NVK GB206",
          "libnvenc_h264.dylib", "libnvenc_hevc.dylib");
    check(NULL, NULL, NULL, "NVK GB206",
          "libnvenc_h264.dylib", "libnvenc_hevc.dylib");
    puts("PASS: TU116 selected; TU117 and incomplete GTX profile refused; Turing/Ampere/Blackwell and missing-profile fallbacks preserved");
}
'''
with tempfile.TemporaryDirectory() as directory:
    path = pathlib.Path(directory)
    (path / 'selection.c').write_text(fixture + selection + checks)
    subprocess.run(['xcrun', 'clang', '-Wall', '-Wextra', '-Werror', str(path / 'selection.c'),
                    '-o', str(path / 'selection')], check=True)
    subprocess.run([str(path / 'selection')], check=True)
