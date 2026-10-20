/* IOSurface handoff: commit records the write; only consumers wait for its generation. */
static void nvmtl_pump_buffers(void);
@interface NVMTLCommandBuffer (SurfaceSyncCompletion)
- (void)nvmtlFinishExecution:(NSError *)error;
@end
static NSObject *nvmtl_surface_sync_lock(void) {
    static NSObject *lock; static dispatch_once_t once;
    dispatch_once(&once, ^{ lock = [NSObject new]; });
    return lock;
}
static NSCountedSet *nvmtl_surface_sync_holds;
static NSMutableDictionary *nvmtl_surface_sync_publications;
static NSArray *nvmtl_surface_sync_key(NVMTLTexture *t) {
    return @[@((uint32_t)IOSurfaceGetID(t->_surf)), @(t->_plane)];
}
static BOOL nvmtl_surface_sync_read(NVMTLCommandBuffer *cb, NVMTLTexture *t) {
    if (!cb || !t->_surf || !t->_vramOn || !nvmtl_vk_surface_sync_available()) return NO;
    NSNumber *sid = @((uint32_t)IOSurfaceGetID(t->_surf));
    if (cb->_surfaceSyncReads[sid]) return YES;
    uint64_t ticket = 0;
    int status = nvmtl_vk_surface_sync(NV_SURFACE_SYNC_SNAPSHOT, sid.unsignedIntValue, (uint32_t)t->_plane, &ticket, 0);
    if (status == NV_SURFACE_SYNC_READY) return NO;
    if (status != NV_SURFACE_SYNC_PENDING) {
        [cb nvmtlRecordEncodingError:[NSError errorWithDomain:MTLCommandBufferErrorDomain code:MTLCommandBufferErrorInternal
            userInfo:@{NSLocalizedDescriptionKey:@"IOSurface producer failed or could not be queried"}]];
        return YES;
    }
    if (!cb->_surfaceSyncReads) cb->_surfaceSyncReads = [NSMutableDictionary new];
    cb->_surfaceSyncReads[sid] = @[@(ticket), t]; // retain the surface while its generation is in use
    return YES;
}
static BOOL nvmtl_surface_sync_dependencies(NVMTLCommandBuffer *cb) {
    if (!cb->_surfaceSyncReads.count) return YES;
    uint64_t now = clock_gettime_nsec_np(CLOCK_MONOTONIC_RAW);
    if (!cb->_surfaceSyncReadStart) cb->_surfaceSyncReadStart = now;
    BOOL pending = NO;
    for (NSNumber *sid in cb->_surfaceSyncReads) {
        uint64_t ticket = [cb->_surfaceSyncReads[sid][0] unsignedLongLongValue];
        int status = nvmtl_vk_surface_sync(NV_SURFACE_SYNC_POLL, sid.unsignedIntValue, 0, &ticket, 0);
        if (status == NV_SURFACE_SYNC_READY) continue;
        if (status == NV_SURFACE_SYNC_PENDING && now - cb->_surfaceSyncReadStart < 5000000000ull) { pending = YES; continue; }
        [cb nvmtlRecordEncodingError:[NSError errorWithDomain:MTLCommandBufferErrorDomain code:MTLCommandBufferErrorInternal
            userInfo:@{NSLocalizedDescriptionKey:@"IOSurface producer failed or timed out"}]];
        return NO;
    }
    if (!pending) { cb->_surfaceSyncReads = nil; return YES; }
    // No thread sleeps in commit/encoding, and no busy loop holds the command-buffer class lock. One retry serves
    // all pending buffers; another process retiring its ticket does not otherwise wake this process's worker.
    static BOOL retry;
    @synchronized ([NVMTLCommandBuffer class]) {
        if (!retry) {
            retry = YES;
            dispatch_after(dispatch_time(DISPATCH_TIME_NOW, NSEC_PER_MSEC), dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0), ^{
                @synchronized ([NVMTLCommandBuffer class]) { retry = NO; }
                nvmtl_pump_buffers();
            });
        }
    }
    return NO;
}
static void nvmtl_surface_sync_begin(NVMTLCommandBuffer *cb) {
    if (!strcmp(getprogname(), "WindowServer") || !nvmtl_vk_surface_sync_available()) return;
    NSMutableDictionary *textures = [NSMutableDictionary new];
    for (NVMTLSubmissionPart *part in cb->_parts) for (NVMTLTexture *t in part->surfaces) {
        // Private/headless backings still require the existing CPU publication before the scheduled notification.
        if (!t->_surf) continue;
        if (!t->_vramOn) return;
        textures[nvmtl_surface_sync_key(t)] = t;
    }
    BOOL all = textures.count != 0;
    for (NSArray *key in textures) {
        uint64_t ticket = 0;
        int status = nvmtl_vk_surface_sync(NV_SURFACE_SYNC_BEGIN, [key[0] unsignedIntValue], [key[1] unsignedIntValue], &ticket, 0);
        if (status != NV_SURFACE_SYNC_READY) {
            all = NO;
            if (status != NV_SURFACE_SYNC_BUSY)
                [cb nvmtlRecordEncodingError:[NSError errorWithDomain:MTLCommandBufferErrorDomain code:MTLCommandBufferErrorInternal
                    userInfo:@{NSLocalizedDescriptionKey:@"IOSurface producer connection or backing failed"}]];
            continue;
        }
        if (!cb->_surfaceSyncWrites) cb->_surfaceSyncWrites = [NSMutableDictionary new];
        cb->_surfaceSyncWrites[key] = @[@(ticket), textures[key]];
        @synchronized (nvmtl_surface_sync_lock()) {
            if (!nvmtl_surface_sync_holds) nvmtl_surface_sync_holds = [NSCountedSet new];
            [nvmtl_surface_sync_holds addObject:key[0]];
        }
    }
    // A successful BEGIN both marks the VRAM current and installs the kernel wait before commit can hand it off.
    // Explicit diagnostic serialization remains available. Partial/unsupported registrations retain the old wait.
    const char *e = getenv("NVMTL_SCHEDULE_AFTER_SURFACE_WRITES");
    if (all && !(e && e[0] == '1')) cb->_scheduleAfterPublish = NO;
}
static BOOL nvmtl_surface_sync_publish(NVMTLCommandBuffer *cb, NSArray *settled) {
    BOOL ok = YES;
    for (NSArray *p in settled) {
        NVMTLTexture *t = p[0];
        if (![p[1] boolValue] && t->_vramOn && nvmtl_vk_surface_sync_available()) {
            // A refused BEGIN did not mark this backing current. Publishing it can need the family lock, which a
            // CPU reader may hold while waiting on a different, later buffer's ticket. Finish its GPU work first,
            // then publish off the execution worker so that later buffer can retire and wake the reader.
            if (!cb->_surfaceSyncLatePublications) cb->_surfaceSyncLatePublications = [NSMutableArray new];
            [cb->_surfaceSyncLatePublications removeObjectIdenticalTo:t];
            [cb->_surfaceSyncLatePublications addObject:t];
            continue;
        }
        if (![t nvmtlSurfaceOutDirtied:[p[1] boolValue]]) ok = NO;
    }
    return ok;
}
// Release this buffer's tickets for one SID, never a later buffer's tickets. Publications wait until its last local
// writer, as on the WindowServer path, so they cannot lock a surface whose pending mark this worker still must retire.
static BOOL nvmtl_surface_sync_release(NVMTLCommandBuffer *cb, NSNumber *sid, BOOL failed, NSMutableArray *settled) {
    BOOL ok = YES;
    BOOL poisoned = failed;
    @synchronized (nvmtl_surface_sync_lock()) {
        for (NSArray *key in [cb->_surfaceSyncWrites.allKeys copy]) {
            if (![key[0] isEqual:sid]) continue;
            uint64_t ticket = [cb->_surfaceSyncWrites[key][0] unsignedLongLongValue];
            int status = nvmtl_vk_surface_sync(NV_SURFACE_SYNC_END, sid.unsignedIntValue, [key[1] unsignedIntValue], &ticket, failed);
            // FAILED also acknowledges retirement, but another writer (or this one) poisoned the shared backing.
            if (status != NV_SURFACE_SYNC_READY && status != NV_SURFACE_SYNC_FAILED) { ok = NO; continue; }
            if (status == NV_SURFACE_SYNC_FAILED) { poisoned = YES; if (!failed) ok = NO; }
            [cb->_surfaceSyncWrites removeObjectForKey:key];
            [nvmtl_surface_sync_holds removeObject:sid];
        }
        if (![nvmtl_surface_sync_holds countForObject:sid]) {
            if (!poisoned) [settled addObjectsFromArray:nvmtl_surface_sync_publications[sid] ?: @[]];
            [nvmtl_surface_sync_publications removeObjectForKey:sid];
        }
    }
    return ok;
}
static BOOL nvmtl_surface_sync_part_landed(NVMTLCommandBuffer *cb, NSUInteger index, NSArray *surfaces) {
    NSMutableArray *settled = [NSMutableArray new];
    NSMutableSet *last = [NSMutableSet new];
    @synchronized (nvmtl_surface_sync_lock()) {
        for (NVMTLTexture *t in surfaces) {
            NSArray *key = t->_surf ? nvmtl_surface_sync_key(t) : nil;
            NSNumber *sid = key[0];
            BOOL early = key && cb->_surfaceSyncWrites[key] != nil;
            if (!key || ![nvmtl_surface_sync_holds countForObject:sid]) { [settled addObject:@[t, @NO]]; continue; }
            if (!nvmtl_surface_sync_publications) nvmtl_surface_sync_publications = [NSMutableDictionary new];
            NSMutableArray *waiting = nvmtl_surface_sync_publications[sid];
            if (!waiting) nvmtl_surface_sync_publications[sid] = waiting = [NSMutableArray new];
            for (NSUInteger i = 0; i < waiting.count; ++i)
                if (waiting[i][0] == t) { [waiting removeObjectAtIndex:i]; break; }
            [waiting addObject:@[t, @(early)]];
        }
    }
    for (NSArray *key in cb->_surfaceSyncWrites) {
        BOOL later = NO;
        for (NSUInteger i = index + 1; i < cb->_parts.count && !later; ++i) {
            NVMTLSubmissionPart *next = cb->_parts[i];
            if (![next isKindOfClass:[NVMTLSubmissionPart class]]) continue;
            for (NVMTLTexture *t in next->surfaces)
                if (t->_surf && IOSurfaceGetID(t->_surf) == [key[0] unsignedIntValue]) { later = YES; break; }
        }
        if (!later) [last addObject:key[0]];
    }
    BOOL ok = YES;
    for (NSNumber *sid in last) if (!nvmtl_surface_sync_release(cb, sid, NO, settled)) ok = NO;
    return nvmtl_surface_sync_publish(cb, settled) && ok;
}
static BOOL nvmtl_surface_sync_finish(NVMTLCommandBuffer *cb, BOOL failed) {
    NSMutableArray *settled = [NSMutableArray new];
    NSMutableSet *ids = [NSMutableSet new];
    for (NSArray *key in cb->_surfaceSyncWrites) [ids addObject:key[0]];
    BOOL ok = YES;
    for (NSNumber *sid in ids) if (!nvmtl_surface_sync_release(cb, sid, failed, settled)) ok = NO;
    cb->_surfaceSyncReads = nil;
    return nvmtl_surface_sync_publish(cb, settled) && ok;
}
static BOOL nvmtl_surface_sync_finish_later(NVMTLCommandBuffer *cb) {
    if (!cb->_surfaceSyncLatePublications.count) return NO;
    NSArray *publish = [cb->_surfaceSyncLatePublications copy]; cb->_surfaceSyncLatePublications = nil;
    @synchronized ([NVMTLCommandBuffer class]) {
        cb->_surfaceSyncAsyncFinish = YES;
        // All GPU parts have finished. Let the next buffer retire its tickets, even while CPU publication waits for
        // the family lock. Status, scheduled-after-publication handlers and completion handlers still wait below.
        [cb nvmtlReleaseReservation];
    }
    dispatch_async(dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0), ^{
        NSError *failure = nil;
        for (NVMTLTexture *t in publish) if (![t nvmtlSurfaceOutDirtied:NO])
            failure = [NSError errorWithDomain:MTLCommandBufferErrorDomain code:MTLCommandBufferErrorInternal
                userInfo:@{NSLocalizedDescriptionKey:@"Could not publish IOSurface writes"}];
        [cb nvmtlFinishExecution:failure];
        @synchronized ([NVMTLCommandBuffer class]) { [cb nvmtlFinishCallbacks]; }
        nvmtl_pump_buffers();
    });
    return YES;
}
