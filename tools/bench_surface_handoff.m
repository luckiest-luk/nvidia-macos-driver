// Two independent Metal processes, sharing an IOSurface through an actual Mach send right.
// clang -fobjc-arc tools/bench_surface_handoff.m -o probe -framework Foundation -framework Metal \
//       -framework IOSurface -framework IOKit
// probe /path/to/NVMTLDriver [--require-async]
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#import <IOSurface/IOSurface.h>
#import <IOKit/IOKitLib.h>
#import <objc/message.h>
#include <servers/bootstrap.h>
#include <mach/mach.h>
#include <spawn.h>
#include <sys/wait.h>
#include <signal.h>
#include <dlfcn.h>
#include <unistd.h>
extern char **environ;
enum { Hello = 1, Surface, Ready, Frame, Result, Quit, Width = 3840, Height = 2160 };
typedef struct {
    mach_msg_header_t header;
    mach_msg_body_t body;
    mach_msg_port_descriptor_t port;
    uint32_t kind, value;
    mach_msg_max_trailer_t trailer;
} Message;
static void sendMessage(mach_port_t to, unsigned kind, unsigned value, mach_port_t right) {
    Message m = {0};
    m.header.msgh_bits = MACH_MSGH_BITS(MACH_MSG_TYPE_COPY_SEND, 0) | MACH_MSGH_BITS_COMPLEX;
    m.header.msgh_remote_port = to; m.header.msgh_size = offsetof(Message, trailer);
    m.body.msgh_descriptor_count = 1;
    m.port.name = right; m.port.disposition = MACH_MSG_TYPE_COPY_SEND; m.port.type = MACH_MSG_PORT_DESCRIPTOR;
    m.kind = kind; m.value = value;
    kern_return_t kr = mach_msg(&m.header, MACH_SEND_MSG | MACH_SEND_TIMEOUT, m.header.msgh_size, 0, 0, 10000, 0);
    if (kr) { fprintf(stderr, "send: %s\n", mach_error_string(kr)); exit(20); }
}
static Message receiveMessage(mach_port_t port) {
    Message m = {0};
    kern_return_t kr = mach_msg(&m.header, MACH_RCV_MSG | MACH_RCV_TIMEOUT, 0, sizeof m, port, 10000, 0);
    if (kr) { fprintf(stderr, "receive: %s\n", mach_error_string(kr)); exit(21); }
    if (m.header.msgh_size != offsetof(Message, trailer) || m.body.msgh_descriptor_count != 1 ||
        m.port.type != MACH_MSG_PORT_DESCRIPTOR) { fprintf(stderr, "invalid message\n"); exit(22); }
    return m;
}
static mach_port_t receivePort(void) {
    mach_port_t port;
    if (mach_port_allocate(mach_task_self(), MACH_PORT_RIGHT_RECEIVE, &port) ||
        mach_port_insert_right(mach_task_self(), port, port, MACH_MSG_TYPE_MAKE_SEND)) exit(23);
    return port;
}
static id<MTLDevice> device(const char *path, BOOL requireAsync) {
    void *library = dlopen(path, RTLD_NOW | RTLD_LOCAL);
    if (!library) { fprintf(stderr, "%s\n", dlerror()); exit(2); }
    int (*available)(void) = dlsym(library, "nvmtl_vk_surface_sync_available");
    int active = available ? available() : 0;
    if (requireAsync && !active) { fprintf(stderr, "async protocol inactive; matching kernel/plugin and opt-in required\n"); exit(77); }
    io_service_t svc = IOServiceGetMatchingService(kIOMainPortDefault, IOServiceMatching("NVAccel"));
    if (!svc) exit(3);
    id<MTLDevice> d = ((id(*)(id,SEL,mach_port_t))objc_msgSend)([NSClassFromString(@"NVMTLDevice") alloc],
        NSSelectorFromString(@"initWithAcceleratorPort:"), svc);
    IOObjectRelease(svc);
    if (!d) exit(4);
    printf("%s async=%d\n", getprogname(), active);
    return d;
}
static id<MTLTexture> texture(id<MTLDevice> d, IOSurfaceRef surface) {
    MTLTextureDescriptor *td = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm
        width:Width height:Height mipmapped:NO];
    td.storageMode = MTLStorageModeShared; td.usage = MTLTextureUsageShaderRead | MTLTextureUsageRenderTarget;
    id<MTLTexture> t = [d newTextureWithDescriptor:td iosurface:surface plane:0];
    if (!t || ![[(NSObject *)t valueForKey:@"_vramOn"] boolValue]) { fprintf(stderr, "shared VRAM required\n"); exit(78); }
    return t;
}
static int consumer(const char *path, const char *name, BOOL requireAsync) {
    id<MTLDevice> d = device(path, requireAsync);
    id<MTLCommandQueue> q = [d newCommandQueue];
    mach_port_t server;
    if (bootstrap_look_up(bootstrap_port, (char *)name, &server)) return 5;
    mach_port_t inbox = receivePort();
    sendMessage(server, Hello, 0, inbox);
    Message m = receiveMessage(inbox);
    if (m.kind != Surface) return 6;
    IOSurfaceRef surface = IOSurfaceLookupFromMachPort(m.port.name);
    mach_port_deallocate(mach_task_self(), m.port.name);
    if (!surface) return 7;
    id<MTLTexture> t = texture(d, surface);
    id<MTLBuffer> readback = [d newBufferWithLength:Width * Height * 4 options:MTLResourceStorageModeShared];
    if (!q || !readback) return 8;
    // Warm up before the producer starts, so first-use initialization cannot hide an incomplete producer frame.
    for (unsigned n = 0; ; n++) { @autoreleasepool {
        id<MTLCommandBuffer> cb = [q commandBuffer];
        id<MTLBlitCommandEncoder> blit = [cb blitCommandEncoder];
        [blit copyFromTexture:t sourceSlice:0 sourceLevel:0 sourceOrigin:MTLOriginMake(0,0,0)
            sourceSize:MTLSizeMake(Width,Height,1) toBuffer:readback destinationOffset:0
            destinationBytesPerRow:Width*4 destinationBytesPerImage:Width*Height*4];
        [blit endEncoding]; [cb commit]; [cb waitUntilCompleted];
        if (cb.error) { fprintf(stderr, "%s\n", cb.error.description.UTF8String); return 9; }
        if (!n) sendMessage(server, Ready, 0, MACH_PORT_NULL);
        else {
            unsigned wrong = 0; const uint8_t *bytes = readback.contents;
            for (size_t p = 0; p < Width*Height; p++, bytes += 4)
                if (bytes[0] || abs(bytes[1]-128)>1 || abs(bytes[2]-(int)m.value)>1 || bytes[3]!=255) wrong++;
            sendMessage(server, Result, wrong, MACH_PORT_NULL);
        }
        m = receiveMessage(inbox);
        if (m.kind == Quit) break;
        if (m.kind != Frame) return 10;
    }}
    CFRelease(surface); mach_port_deallocate(mach_task_self(), inbox);
    mach_port_mod_refs(mach_task_self(), inbox, MACH_PORT_RIGHT_RECEIVE, -1); mach_port_deallocate(mach_task_self(), server);
    return 0;
}
int main(int argc, char **argv) { @autoreleasepool {
    if (argc > 3 && !strcmp(argv[1], "--consumer")) return consumer(argv[2], argv[3], argc > 4);
    if (argc < 2) return 1;
    BOOL requireAsync = argc > 2 && !strcmp(argv[2], "--require-async");
    id<MTLDevice> d = device(argv[1], requireAsync);
    id<MTLCommandQueue> q = [d newCommandQueue];
    IOSurfaceRef surface = IOSurfaceCreate((__bridge CFDictionaryRef)@{(id)kIOSurfaceWidth:@(Width),
        (id)kIOSurfaceHeight:@(Height), (id)kIOSurfaceBytesPerElement:@4, (id)kIOSurfacePixelFormat:@((uint32_t)'BGRA')});
    if (!surface || !q) return 11;
    id<MTLTexture> t = texture(d, surface);
    mach_port_t inbox = receivePort(); char name[128]; snprintf(name, sizeof name, "com.nullmoth.handoff.%d", getpid());
    kern_return_t kr = bootstrap_register(bootstrap_port, name, inbox);
    if (kr) { fprintf(stderr, "bootstrap_register: %s\n", mach_error_string(kr)); return 12; }
    char *childArgs[] = {argv[0], "--consumer", argv[1], name, requireAsync ? "--require-async" : NULL, NULL};
    pid_t child;
    if (posix_spawn(&child, argv[0], NULL, NULL, childArgs, environ)) return 13;
    Message hello = receiveMessage(inbox);
    if (hello.kind != Hello) { kill(child, SIGTERM); return 14; }
    mach_port_t surfacePort = IOSurfaceCreateMachPort(surface);
    sendMessage(hello.port.name, Surface, 0, surfacePort);
    mach_port_deallocate(mach_task_self(), surfacePort);
    if (receiveMessage(inbox).kind != Ready) { kill(child, SIGTERM); return 15; }
    unsigned bad = 0; uint64_t commitNs = 0, scheduledNs = 0;
    for (unsigned frame = 0; frame < 64; frame++) { @autoreleasepool {
        unsigned red = (frame * 37 + 53) % 256;
        id<MTLCommandBuffer> cb = [q commandBuffer];
        // Enough VRAM traffic for the independent consumer to encounter an actually pending write.
        for (unsigned pass = 0; pass < 16; pass++) {
            MTLRenderPassDescriptor *rp = [MTLRenderPassDescriptor renderPassDescriptor];
            rp.colorAttachments[0].texture = t; rp.colorAttachments[0].loadAction = MTLLoadActionClear;
            rp.colorAttachments[0].storeAction = MTLStoreActionStore;
            rp.colorAttachments[0].clearColor = MTLClearColorMake(red / 255.0, 0.5, 0, 1);
            [[cb renderCommandEncoderWithDescriptor:rp] endEncoding];
        }
        uint64_t start = clock_gettime_nsec_np(CLOCK_MONOTONIC_RAW);
        [cb commit]; uint64_t committed = clock_gettime_nsec_np(CLOCK_MONOTONIC_RAW);
        [cb waitUntilScheduled]; uint64_t scheduled = clock_gettime_nsec_np(CLOCK_MONOTONIC_RAW);
        commitNs += committed - start; scheduledNs += scheduled - committed;
        sendMessage(hello.port.name, Frame, red, MACH_PORT_NULL);
        Message result = receiveMessage(inbox);
        if (result.kind != Result || result.value) { bad++; printf("FAIL frame=%u wrong=%u\n", frame, result.value); }
        [cb waitUntilCompleted];
        if (cb.error) { fprintf(stderr, "%s\n", cb.error.description.UTF8String); kill(child, SIGTERM); return 16; }
    }}
    sendMessage(hello.port.name, Quit, 0, MACH_PORT_NULL);
    int status; waitpid(child, &status, 0);
    mach_port_deallocate(mach_task_self(), hello.port.name); mach_port_deallocate(mach_task_self(), inbox);
    mach_port_mod_refs(mach_task_self(), inbox, MACH_PORT_RIGHT_RECEIVE, -1); CFRelease(surface);
    printf("RESULT frames=64 bad=%u commit_avg_ms=%.3f scheduled_wait_avg_ms=%.3f\n", bad, commitNs/64e6, scheduledNs/64e6);
    return bad || !WIFEXITED(status) || WEXITSTATUS(status) ? 1 : 0;
}}
