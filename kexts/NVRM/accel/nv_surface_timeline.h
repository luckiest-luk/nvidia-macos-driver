/* Caller holds one lock for every operation. No allocation or object callbacks here. */
#ifndef NV_SURFACE_TIMELINE_H
#define NV_SURFACE_TIMELINE_H
#include "../nvrm_surface_sync_abi.h"
enum { kNVSurfaceTickets = 512, kNVSurfaceOwnerTickets = 128 };
struct NVSurfaceTicket {
    void *owner, *resource, *surface;
    unsigned int id;
    unsigned long long ticket;
    bool failed, armed;
};
struct NVSurfaceTimeline {
    NVSurfaceTicket entries[kNVSurfaceTickets];
    unsigned long long serial;
};
static inline unsigned nvSurfaceSnapshot(const NVSurfaceTimeline *t, unsigned id,
                                         unsigned long long *ticket) {
    *ticket = 0;
    unsigned status = NV_SURFACE_SYNC_READY;
    for (const auto &e : t->entries) if (e.id == id && e.ticket && (e.armed || e.failed)) {
        if (e.ticket > *ticket) *ticket = e.ticket;
        if (e.failed) status = NV_SURFACE_SYNC_FAILED;
        else if (status != NV_SURFACE_SYNC_FAILED) status = NV_SURFACE_SYNC_PENDING;
    }
    return status;
}
static inline unsigned nvSurfacePoll(const NVSurfaceTimeline *t, unsigned id,
                                     unsigned long long ticket) {
    if (ticket > t->serial) return NV_SURFACE_SYNC_INVALID;
    unsigned status = NV_SURFACE_SYNC_READY;
    for (const auto &e : t->entries) if (e.id == id && e.ticket && e.ticket <= ticket && (e.armed || e.failed)) {
        if (e.failed) return NV_SURFACE_SYNC_FAILED;
        status = NV_SURFACE_SYNC_PENDING;
    }
    return status;
}
static inline unsigned nvSurfaceBegin(NVSurfaceTimeline *t, void *owner, unsigned id,
                                      void *resource, void *surface, unsigned long long *ticket, bool armed = true) {
    *ticket = 0;
    if (!owner || !id || !resource || !surface) return NV_SURFACE_SYNC_INVALID;
    NVSurfaceTicket *slot = nullptr;
    unsigned count = 0;
    for (auto &e : t->entries) {
        if (e.id == id && e.failed) return NV_SURFACE_SYNC_FAILED;
        if (e.owner == owner) ++count;
        if (!e.ticket && !slot) slot = &e;
    }
    // Exhaustion never evicts an unfinished write or wraps a generation.
    if (!slot || count >= kNVSurfaceOwnerTickets || t->serial == ~0ull) return NV_SURFACE_SYNC_BUSY;
    *ticket = ++t->serial;
    *slot = {owner, resource, surface, id, *ticket, false, armed};
    return NV_SURFACE_SYNC_READY;
}
static inline unsigned nvSurfaceArm(NVSurfaceTimeline *t, void *owner, unsigned id, unsigned long long ticket) {
    if (!owner || !id || !ticket) return NV_SURFACE_SYNC_INVALID;
    for (auto &e : t->entries) if (e.id == id && e.ticket == ticket && e.owner == owner && !e.failed) {
        e.armed = true; return NV_SURFACE_SYNC_READY;
    }
    return NV_SURFACE_SYNC_INVALID;
}
static inline unsigned nvSurfaceEnd(NVSurfaceTimeline *t, void *owner, unsigned id,
                                    unsigned long long ticket, bool failed, void **surface) {
    *surface = nullptr;
    if (!owner || !id || !ticket) return NV_SURFACE_SYNC_INVALID;
    for (auto &e : t->entries) if (e.id == id && e.ticket == ticket && e.owner == owner) {
        *surface = e.surface;
        if (failed) { e.owner = nullptr; e.surface = nullptr; e.failed = true; }
        else e = {};
        return NV_SURFACE_SYNC_READY;
    }
    return NV_SURFACE_SYNC_INVALID;
}
static inline void nvSurfaceForget(NVSurfaceTimeline *t, void *resource) {
    // A failed producer leaves a tombstone until the associated backing is destroyed.
    for (auto &e : t->entries) if (e.resource == resource && e.failed) e = {};
}
#endif
