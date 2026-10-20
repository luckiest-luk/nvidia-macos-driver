#!/usr/bin/env python3
"""Run the production handoff helpers against the kernel's real timeline, without loading a kext."""
import pathlib
import subprocess
import tempfile

root = pathlib.Path(__file__).resolve().parents[1]
fixture = r'''
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include <assert.h>
#include <time.h>
#include <string.h>
#include "kexts/NVRM/accel/nv_surface_timeline.h"
struct Surface { unsigned id, seed; };
static unsigned IOSurfaceGetID(Surface *s) { return s->id; }
static NVSurfaceTimeline timeline;
static int connection;
static BOOL available = YES;
static BOOL broken;
static unsigned busyID;
static unsigned publications, blockingPublications;
static dispatch_semaphore_t publicationEntered, publicationRelease, callbacksFinished;
static BOOL blockPublication;
static NSMutableDictionary *surfaces;
static const char *test_progname() { return "test-app"; }
#define getprogname() test_progname()
static int nvmtl_vk_surface_sync_available() { return available; }
static int nvmtl_vk_surface_sync(unsigned op, uint32_t sid, uint32_t plane, uint64_t *ticket, int failed) {
    if (!available || broken) return -1;
    Surface *s = (Surface *)[surfaces[@(sid)] pointerValue];
    unsigned long long value = *ticket; unsigned status;
    void *released;
    switch (op) {
    case NV_SURFACE_SYNC_BEGIN:
        if (sid == busyID) return NV_SURFACE_SYNC_BUSY;
        status = nvSurfaceBegin(&timeline, &connection, sid, s, s, &value);
        if (status == NV_SURFACE_SYNC_READY) s->seed++;
        break;
    case NV_SURFACE_SYNC_END:
        status = nvSurfaceEnd(&timeline, &connection, sid, value, failed, &released);
        if (status == NV_SURFACE_SYNC_READY) {
            unsigned long long latest;
            if (nvSurfaceSnapshot(&timeline, sid, &latest) == NV_SURFACE_SYNC_FAILED) status = NV_SURFACE_SYNC_FAILED;
        }
        break;
    case NV_SURFACE_SYNC_SNAPSHOT: status = nvSurfaceSnapshot(&timeline, sid, &value); break;
    default: status = nvSurfacePoll(&timeline, sid, value); break;
    }
    *ticket = value;
    return status;
}
@interface NVMTLTexture : NSObject { @public Surface *_surf; BOOL _vramOn; NSUInteger _plane; }
- (BOOL)nvmtlSurfaceOutDirtied:(BOOL)dirtied;
@end
@implementation NVMTLTexture
- (BOOL)nvmtlSurfaceOutDirtied:(BOOL)dirtied {
    if (!dirtied && blockPublication) {
        dispatch_semaphore_signal(publicationEntered);
        assert(!dispatch_semaphore_wait(publicationRelease, dispatch_time(DISPATCH_TIME_NOW, 5*NSEC_PER_SEC)));
    }
    unsigned long long ticket;
    // A CPU copy/mark must not run while this worker still owns pending writes to the same surface.
    assert(nvSurfaceSnapshot(&timeline, _surf->id, &ticket) == NV_SURFACE_SYNC_READY);
    publications++; if (!dirtied) blockingPublications++;
    return YES;
}
@end
@interface NVMTLSubmissionPart : NSObject { @public NSSet *surfaces; } @end
@implementation NVMTLSubmissionPart @end
@interface NVMTLCommandBuffer : NSObject { @public
    NSMutableDictionary *_surfaceSyncWrites, *_surfaceSyncReads;
    NSMutableArray *_surfaceSyncLatePublications;
    BOOL _surfaceSyncAsyncFinish, finished; unsigned reservationsReleased, callbacks;
    NSMutableArray *_parts; BOOL _scheduleAfterPublish; NSError *_cbError;
    uint64_t _surfaceSyncReadStart;
}
- (void)nvmtlRecordEncodingError:(NSError *)error;
- (void)nvmtlReleaseReservation;
- (void)nvmtlFinishCallbacks;
- (void)nvmtlFinishExecution:(NSError *)error;
@end
@implementation NVMTLCommandBuffer
- (void)nvmtlRecordEncodingError:(NSError *)error { _cbError = error; }
- (void)nvmtlReleaseReservation { reservationsReleased++; }
- (void)nvmtlFinishCallbacks { callbacks++; dispatch_semaphore_signal(callbacksFinished); }
- (void)nvmtlFinishExecution:(NSError *)error { _cbError = error; finished = YES; }
@end
static void nvmtl_pump_buffers() {}
#include "plugin/NVMTLSurfaceSync.m"
static NVMTLTexture *texture(Surface *surface) {
    NVMTLTexture *t = [NVMTLTexture new]; t->_surf = surface; t->_vramOn = YES;
    surfaces[@(surface->id)] = [NSValue valueWithPointer:surface]; return t;
}
static NVMTLSubmissionPart *part(NVMTLTexture *t) {
    NVMTLSubmissionPart *p = [NVMTLSubmissionPart new]; p->surfaces = [NSSet setWithObject:t]; return p;
}
static NVMTLCommandBuffer *buffer(NVMTLTexture *t) {
    NVMTLCommandBuffer *cb = [NVMTLCommandBuffer new]; cb->_scheduleAfterPublish = YES;
    cb->_parts = [NSMutableArray arrayWithObject:part(t)]; return cb;
}
int main() { @autoreleasepool {
    surfaces = [NSMutableDictionary new];
    Surface s = {7, 1}, s2 = {8, 1};
    NVMTLTexture *t = texture(&s), *other = texture(&s2);
    NVMTLCommandBuffer *a = buffer(t), *reader = buffer(t);
    nvmtl_surface_sync_begin(a);
    assert(!a->_scheduleAfterPublish && a->_surfaceSyncWrites.count == 1);
    assert(nvmtl_surface_sync_read(reader, t));
    assert(!nvmtl_surface_sync_dependencies(reader) && !reader->_cbError); // consumer defers; encoding returns
    assert(nvmtl_surface_sync_part_landed(a, 0, @[t]));
    assert(publications == 1 && blockingPublications == 0);
    assert(nvmtl_surface_sync_dependencies(reader) && !reader->_surfaceSyncReads.count);
    assert(!nvmtl_surface_sync_read(buffer(other), other)); // unrelated surfaces never wait

    // A split command buffer retains the ticket until its last write, and publishes the latest texture only once.
    a = buffer(t); [a->_parts addObject:part(t)];
    nvmtl_surface_sync_begin(a);
    unsigned before = publications;
    assert(nvmtl_surface_sync_part_landed(a, 0, @[t]));
    assert(a->_surfaceSyncWrites.count == 1 && publications == before);
    assert(nvmtl_surface_sync_part_landed(a, 1, @[t]));
    assert(!a->_surfaceSyncWrites.count && publications == before + 1);

    // Older completion cannot clear a newer buffer's mark or publish its incomplete pixels.
    a = buffer(t); NVMTLCommandBuffer *b = buffer(t);
    nvmtl_surface_sync_begin(a); reader = buffer(t); assert(nvmtl_surface_sync_read(reader, t));
    nvmtl_surface_sync_begin(b);
    before = publications;
    assert(nvmtl_surface_sync_part_landed(a, 0, @[t]));
    assert(publications == before && b->_surfaceSyncWrites.count == 1);
    assert(nvmtl_surface_sync_dependencies(reader)); // this snapshot precedes b
    reader = buffer(t); assert(nvmtl_surface_sync_read(reader, t));
    assert(!nvmtl_surface_sync_dependencies(reader));
    assert(nvmtl_surface_sync_part_landed(b, 0, @[t]));
    assert(publications == before + 1 && nvmtl_surface_sync_dependencies(reader));

    // An unsupported kernel/private backing retains the scheduled-after-publication fallback.
    available = NO; a = buffer(t); nvmtl_surface_sync_begin(a);
    assert(a->_scheduleAfterPublish && !a->_surfaceSyncWrites.count);
    assert(!nvmtl_surface_sync_read(buffer(t), t));
    available = YES; t->_vramOn = NO; a = buffer(t); nvmtl_surface_sync_begin(a);
    assert(a->_scheduleAfterPublish && !a->_surfaceSyncWrites.count); t->_vramOn = YES;
    broken = YES; a = buffer(t); nvmtl_surface_sync_begin(a);
    assert(a->_cbError && !a->_surfaceSyncWrites.count); broken = NO;

    // Partial registration keeps fallback scheduling, but still retires every accepted ticket after GPU completion.
    busyID = s2.id; a = buffer(t); [a->_parts addObject:part(other)]; nvmtl_surface_sync_begin(a);
    assert(a->_scheduleAfterPublish && a->_surfaceSyncWrites.count == 1);
    assert(nvmtl_surface_sync_part_landed(a, 0, @[t]));
    assert(nvmtl_surface_sync_part_landed(a, 1, @[other])); busyID = 0;
    assert(nvmtl_surface_sync_finish(a, NO));
    assert(a->_surfaceSyncLatePublications.count == 1);
    for (NVMTLTexture *late in a->_surfaceSyncLatePublications) assert([late nvmtlSurfaceOutDirtied:NO]);

    // Producer errors poison that generation rather than exposing pixels or pretending completion.
    a = buffer(t); nvmtl_surface_sync_begin(a); reader = buffer(t);
    assert(nvmtl_surface_sync_read(reader, t));
    assert(nvmtl_surface_sync_finish(a, YES));
    assert(!nvmtl_surface_sync_dependencies(reader) && reader->_cbError);
    a = buffer(t); nvmtl_surface_sync_begin(a); assert(a->_cbError && a->_scheduleAfterPublish);
    nvSurfaceForget(&timeline, &s);

    a = buffer(t); b = buffer(t); nvmtl_surface_sync_begin(a); nvmtl_surface_sync_begin(b);
    before = publications;
    assert(nvmtl_surface_sync_finish(a, YES));
    assert(!nvmtl_surface_sync_part_landed(b, 0, @[t]));
    assert(publications == before && !b->_surfaceSyncWrites.count);
    nvSurfaceForget(&timeline, &s);

    // A consumer also fails closed if its producer never retires the captured generation.
    a = buffer(t); nvmtl_surface_sync_begin(a); reader = buffer(t); assert(nvmtl_surface_sync_read(reader, t));
    reader->_surfaceSyncReadStart = clock_gettime_nsec_np(CLOCK_MONOTONIC_RAW) - 6000000000ull;
    assert(!nvmtl_surface_sync_dependencies(reader) && reader->_cbError);
    assert(nvmtl_surface_sync_finish(a, YES)); nvSurfaceForget(&timeline, &s);
    assert(!nvmtl_surface_sync_holds.count && !nvmtl_surface_sync_publications.count);
    // A late publication blocked by a CPU page-off must release the GPU reservation and leave the execution worker
    // available. Neither the completed status nor callbacks may be announced before publication actually returns.
    publicationEntered = dispatch_semaphore_create(0); publicationRelease = dispatch_semaphore_create(0);
    callbacksFinished = dispatch_semaphore_create(0); blockPublication = YES;
    a = buffer(other); a->_surfaceSyncLatePublications = [NSMutableArray arrayWithObject:other];
    assert(nvmtl_surface_sync_finish_later(a));
    assert(a->_surfaceSyncAsyncFinish && a->reservationsReleased == 1);
    assert(!dispatch_semaphore_wait(publicationEntered, dispatch_time(DISPATCH_TIME_NOW, 5*NSEC_PER_SEC)));
    assert(!a->finished && !a->callbacks);
    b = buffer(t); nvmtl_surface_sync_begin(b);
    assert(nvmtl_surface_sync_part_landed(b, 0, @[t])); // another buffer retires while publication is blocked
    dispatch_semaphore_signal(publicationRelease);
    assert(!dispatch_semaphore_wait(callbacksFinished, dispatch_time(DISPATCH_TIME_NOW, 5*NSEC_PER_SEC)));
    assert(a->finished && a->callbacks == 1 && !a->_cbError); blockPublication = NO;
    puts("PASS: asynchronous handoff, split/overlapping writes, snapshots, fallback, partial registration, producer error and timeout");
} }
'''
with tempfile.TemporaryDirectory() as directory:
    path = pathlib.Path(directory)
    (path / "test.mm").write_text(fixture)
    subprocess.run(["xcrun", "clang++", "-std=c++17", "-fobjc-arc", "-I", str(root),
                    "-framework", "Foundation", "-framework", "Metal", str(path / "test.mm"),
                    "-o", str(path / "test")], check=True)
    subprocess.run([str(path / "test")], check=True)
