# Experimental application/compositor handoff

This is an opt-in implementation, not a verified fix for Chromium/VS Code latency.
`NVMTL_ASYNC_SURFACES=1` enables it only with an accelerator advertising the matching
`NVAccelSurfaceSync` protocol. It is **off by default**. Release builds read this
setting from the driver's configuration file rather than the application's environment.
Keep the existing scheduled-after-publication path until the matching kernel and
plugin have passed the hardware tests below.

The shared-VRAM path registers an owned, 64-bit write generation before allowing
the command buffer to submit. Registration uses a try-lock; commit never waits for
the accelerator lock. A successful registration marks the GPU backing current and
installs the CPU page-off dependency while holding the family lock. The scheduled
notification can then precede GPU completion. Private backings and refused/partial
registrations keep scheduled-after-publication behavior.

Every GPU consumer captures the producer generation before the SurfaceIn seed/tag
fast paths. Encoding returns without importing unfinished CPU pixels. The execution
worker polls the captured generations before submission, with a coalesced 1 ms retry;
it does not hold a thread asleep while waiting for another process. Later generations
do not prolong an earlier snapshot's wait. This is a software submission dependency,
not an imported cross-process GPU semaphore. `waitUntilScheduled` still waits if the
consumer's actual GPU submission is deferred.

Completion retires only the connection's own tickets, after the last GPU part writing
the surface. Local overlapping writers defer publication until the last owner retires.
A shared-VRAM publication whose registration was refused can need the family lock;
it runs after GPU completion on a separate queue. Its GPU reservation is released
first, while status and callbacks remain pending, allowing later GPU work to retire
the tickets a CPU page-off may be waiting on.

Connection loss, a failed GPU write, and timeouts fail closed. Failures remain attached
to the backing until its resource is destroyed. No unfinished entry is evicted to make
room; capacity exhaustion falls back. IOSurfaceRoot authenticates BEGIN/SNAPSHOT/POLL
against the owning task, and END requires the exact connection, SID and generation.

## Validation

`tools/test_surface_timeline.cpp` tests generation/owner isolation, overlapping writes,
failure, resource reuse, quotas, exhaustion and wrap prevention. `tools/test_surface_async.py`
runs the production userland helpers against that timeline, including a deliberately
blocked publication and independently progressing writer. The existing submission,
publication, pending-resource and stream-copy tests must continue to pass.

`tools/bench_surface_handoff.m` transfers an IOSurface Mach send right to a second
Metal process. The producer reports scheduled after repeated 4K clears; the consumer
immediately copies the shared texture on its GPU and checks every pixel for 64 frames.
Use `--require-async` to reject a run on the old/fallback protocol (exit 77); otherwise
a successful run establishes fallback compatibility only. The probe also reports
commit duration separately from the scheduled wait.

Before enabling on a desktop, load both matching components and restart the compositor.
Required hardware checks are the scheduled/CPU-read probe, two-process shared-VRAM
producer/consumer ordering, process exit and GPU failure, Chromium's continuous
Graphite page, and input-to-frame latency during VS Code sidebar resizing at 4K.
Mixed private/shared backings, cross-queue event dependencies, CPU locking during
overlapping multi-part writes, and callbacks after deferred publication need explicit
stress testing. These are not established by the host-side timeline tests.

## Current local results (2026-10-20)

The plugin and standalone macOS 15 x86_64 kext compile/link, timeline sanitizers,
userland handoff harness, and existing regression suites pass. Both the 64-frame
CPU-lock probe and the independent-process 4K probe pass against the currently loaded
old kernel (fallback protocol only). The latter reports 0.026 ms average commit and
6.722 ms average scheduled wait; this is a baseline, not a new-protocol latency result.

The isolated auxiliary-collection build passes when run as administrator against a
root-owned temporary repository. The earlier unprivileged attempt returned code 31
(only one codeless kext reported) and was not a valid link test. The successful
collection contains the candidate NVAccel and the currently installed companion
drivers; it is for preflight only and must never replace the live collection because
its bundle paths point to temporary files. Runtime validation of the new kernel is
still outstanding. No installed bundle or live kernel collection was changed.
