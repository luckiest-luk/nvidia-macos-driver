// Check the IOSurface handoff used by Chromium's Metal commands-scheduled fence.
// Build: clang -fobjc-arc tools/bench_surface_scheduled.m -o probe
//        -framework Foundation -framework Metal -framework IOSurface -framework IOKit
// Usage: probe /path/to/NVMTLDriver [completed]
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#import <IOSurface/IOSurface.h>
#import <IOKit/IOKitLib.h>
#import <objc/message.h>
#include <dlfcn.h>

int main(int argc, char **argv) { @autoreleasepool {
    if (argc < 2 || !dlopen(argv[1], RTLD_NOW | RTLD_LOCAL)) return 2;
    io_service_t svc = IOServiceGetMatchingService(kIOMainPortDefault, IOServiceMatching("NVAccel"));
    if (!svc) return 3;
    id<MTLDevice> d = ((id(*)(id,SEL,mach_port_t))objc_msgSend)(
        [NSClassFromString(@"NVMTLDevice") alloc], NSSelectorFromString(@"initWithAcceleratorPort:"), svc);
    IOObjectRelease(svc);
    if (!d) return 4;
    id<MTLCommandQueue> q = [d newCommandQueue];
    const NSUInteger w = 1024, h = 512;
    IOSurfaceRef surface = IOSurfaceCreate((__bridge CFDictionaryRef)@{
        (id)kIOSurfaceWidth:@(w), (id)kIOSurfaceHeight:@(h),
        (id)kIOSurfaceBytesPerElement:@4, (id)kIOSurfacePixelFormat:@((uint32_t)'BGRA')});
    if (!surface || !q) return 5;
    MTLTextureDescriptor *td = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm width:w height:h mipmapped:NO];
    td.storageMode = MTLStorageModeShared;
    td.usage = MTLTextureUsageRenderTarget | MTLTextureUsageShaderRead;
    id<MTLTexture> texture = [d newTextureWithDescriptor:td iosurface:surface plane:0];
    if (!texture) return 6;
    unsigned bad = 0;
    for (unsigned frame = 0; frame < 64; frame++) { @autoreleasepool {
        uint8_t red = (frame * 37 + 53) % 256;
        MTLRenderPassDescriptor *pass = [MTLRenderPassDescriptor renderPassDescriptor];
        pass.colorAttachments[0].texture = texture;
        pass.colorAttachments[0].loadAction = MTLLoadActionClear;
        pass.colorAttachments[0].storeAction = MTLStoreActionStore;
        pass.colorAttachments[0].clearColor = MTLClearColorMake(red / 255.0, 0.5, 0, 1);
        id<MTLCommandBuffer> cb = [q commandBuffer];
        [[cb renderCommandEncoderWithDescriptor:pass] endEncoding];
        [cb commit];
        if (argc > 2 && !strcmp(argv[2], "completed")) [cb waitUntilCompleted];
        else [cb waitUntilScheduled];
        if (IOSurfaceLock(surface, kIOSurfaceLockReadOnly, NULL) != kIOReturnSuccess) return 7;
        const uint8_t *pixels = IOSurfaceGetBaseAddress(surface);
        size_t row = IOSurfaceGetBytesPerRow(surface);
        unsigned wrong = 0;
        for (NSUInteger y=0; y<h; y++) for (NSUInteger x=0; x<w; x++) {
            const uint8_t *p = pixels + y * row + x * 4;
            if (p[0] || abs(p[1]-128)>1 || abs(p[2]-red)>1 || p[3]!=255) wrong++;
        }
        if (wrong) { bad++; printf("FAIL frame=%u wrong=%u first=%u,%u,%u,%u red=%u\n", frame, wrong, pixels[0], pixels[1], pixels[2], pixels[3], red); }
        IOSurfaceUnlock(surface, kIOSurfaceLockReadOnly, NULL);
        [cb waitUntilCompleted];
        if (cb.error) { fprintf(stderr, "%s\n", cb.error.description.UTF8String); return 8; }
    }}
    printf("RESULT frames=64 bad=%u wait=%s\n", bad, argc>2 ? argv[2] : "scheduled");
    CFRelease(surface);
    return bad ? 1 : 0;
}}
