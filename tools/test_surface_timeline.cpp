#include <cassert>
#include <cstdio>
#include "../kexts/NVRM/accel/nv_surface_timeline.h"
int main() {
    NVSurfaceTimeline t = {};
    int a, b, resource, surface;
    unsigned long long x, y, snapshot;
    void *released;
    assert(sizeof(NVSurfaceSyncRequest) == 32);
    assert(nvSurfaceBegin(&t, &a, 7, &resource, &surface, &x, false) == NV_SURFACE_SYNC_READY);
    assert(nvSurfaceSnapshot(&t, 7, &snapshot) == NV_SURFACE_SYNC_READY && snapshot == 0);
    assert(nvSurfaceArm(&t, &b, 7, x) == NV_SURFACE_SYNC_INVALID);
    assert(nvSurfaceArm(&t, &a, 7, x) == NV_SURFACE_SYNC_READY);
    assert(nvSurfaceSnapshot(&t, 7, &snapshot) == NV_SURFACE_SYNC_PENDING && snapshot == x);
    assert(nvSurfaceEnd(&t, &a, 7, x, false, &released) == NV_SURFACE_SYNC_READY);
    assert(nvSurfaceBegin(&t, &a, 7, &resource, &surface, &x, false) == NV_SURFACE_SYNC_READY);
    assert(nvSurfaceEnd(&t, &a, 7, x, true, &released) == NV_SURFACE_SYNC_READY);
    assert(nvSurfaceArm(&t, &a, 7, x) == NV_SURFACE_SYNC_INVALID);
    assert(nvSurfaceSnapshot(&t, 7, &snapshot) == NV_SURFACE_SYNC_FAILED);
    nvSurfaceForget(&t, &resource);
    assert(nvSurfaceBegin(&t, &a, 7, &resource, &surface, &x) == NV_SURFACE_SYNC_READY);
    assert(nvSurfaceSnapshot(&t, 7, &snapshot) == NV_SURFACE_SYNC_PENDING && snapshot == x);
    assert(nvSurfaceBegin(&t, &b, 7, &resource, &surface, &y) == NV_SURFACE_SYNC_READY && y > x);
    assert(nvSurfaceEnd(&t, &b, 7, x, false, &released) == NV_SURFACE_SYNC_INVALID);
    assert(nvSurfaceEnd(&t, &a, 8, x, false, &released) == NV_SURFACE_SYNC_INVALID);
    assert(nvSurfacePoll(&t, 7, snapshot) == NV_SURFACE_SYNC_PENDING);
    assert(nvSurfaceEnd(&t, &a, 7, x, false, &released) == NV_SURFACE_SYNC_READY && released == &surface);
    assert(nvSurfacePoll(&t, 7, snapshot) == NV_SURFACE_SYNC_READY); // later writers do not starve this snapshot
    assert(nvSurfacePoll(&t, 7, y) == NV_SURFACE_SYNC_PENDING); // an older completion cannot clear a newer write
    assert(nvSurfaceEnd(&t, &b, 7, y, true, &released) == NV_SURFACE_SYNC_READY && released == &surface);
    assert(nvSurfacePoll(&t, 7, y) == NV_SURFACE_SYNC_FAILED); // disconnect / GPU error is not success
    assert(nvSurfaceEnd(&t, nullptr, 7, y, false, &released) == NV_SURFACE_SYNC_INVALID);
    assert(nvSurfaceBegin(&t, &a, 7, &resource, &surface, &x) == NV_SURFACE_SYNC_FAILED);
    nvSurfaceForget(&t, &surface); // unrelated destruction must not clear the failure
    assert(nvSurfacePoll(&t, 7, y) == NV_SURFACE_SYNC_FAILED);
    nvSurfaceForget(&t, &resource);
    for (unsigned i = 0; i < kNVSurfaceOwnerTickets; ++i)
        assert(nvSurfaceBegin(&t, &a, 7, &resource, &surface, &x) == NV_SURFACE_SYNC_READY);
    assert(nvSurfaceBegin(&t, &a, 7, &resource, &surface, &x) == NV_SURFACE_SYNC_BUSY);
    for (unsigned i = kNVSurfaceOwnerTickets; i < kNVSurfaceTickets; ++i)
        assert(nvSurfaceBegin(&t, reinterpret_cast<void *>(static_cast<unsigned long>(i + 1)), 9,
                              &resource, &surface, &x) == NV_SURFACE_SYNC_READY);
    assert(nvSurfaceBegin(&t, &b, 9, &resource, &surface, &x) == NV_SURFACE_SYNC_BUSY);
    t = {}; t.serial = ~0ull;
    assert(nvSurfaceBegin(&t, &a, 7, &resource, &surface, &x) == NV_SURFACE_SYNC_BUSY);
    t = {};
    assert(nvSurfacePoll(&t, 7, 1) == NV_SURFACE_SYNC_INVALID);
    for (unsigned i = 0; i < 10000; ++i) {
        assert(nvSurfaceBegin(&t, &a, 7, &resource, &surface, &x) == NV_SURFACE_SYNC_READY);
        nvSurfaceForget(&t, &resource); // destruction cleanup must not evict a live pending generation
        assert(nvSurfacePoll(&t, 7, x) == NV_SURFACE_SYNC_PENDING);
        assert(nvSurfaceEnd(&t, &a, 7, x, i & 1, &released) == NV_SURFACE_SYNC_READY);
        nvSurfaceForget(&t, &resource);
        assert(nvSurfaceSnapshot(&t, 7, &snapshot) == NV_SURFACE_SYNC_READY);
    }
    std::puts("surface timeline: PASS");
}
