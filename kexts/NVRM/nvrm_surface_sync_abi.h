/* Application IOSurface writes: owned, generation-specific completion records. */
#ifndef NVRM_SURFACE_SYNC_ABI_H
#define NVRM_SURFACE_SYNC_ABI_H
#define NV_SURFACE_SYNC_CLIENT 0x4e565359u
#define NV_SURFACE_SYNC_VERSION 1u
enum {
    NV_SURFACE_SYNC_BEGIN = 1, NV_SURFACE_SYNC_END,
    NV_SURFACE_SYNC_SNAPSHOT, NV_SURFACE_SYNC_POLL
};
/* END: READY and FAILED both retire the owned ticket; FAILED forbids publication of the associated backing. */
enum {
    NV_SURFACE_SYNC_READY = 0, NV_SURFACE_SYNC_PENDING,
    NV_SURFACE_SYNC_FAILED, NV_SURFACE_SYNC_BUSY,
    NV_SURFACE_SYNC_INVALID
};
struct NVSurfaceSyncRequest {
    unsigned int version, op, surfaceID, plane, status, reserved;
    unsigned long long ticket;
};
#endif
