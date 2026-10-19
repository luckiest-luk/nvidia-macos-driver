#!/usr/bin/env python3
"""Check AIR digest reuse and SHA256 keys using production code and real Foundation strings."""
import pathlib
import subprocess
import tempfile

root = pathlib.Path(__file__).resolve().parents[1]
source = (root / 'plugin/NVMTLLibraryLoad.m').read_text()
start = source.index('@interface NVMTLAirDigest')
body = source[start:source.index('static NSString *nvmtl_spvcache_path_air(', start)]
fixture = r'''
#import <Foundation/Foundation.h>
#import <objc/runtime.h>
#include <CommonCrypto/CommonDigest.h>
#include <assert.h>
#include <string.h>
'''
checks = r'''
static void verify(NSString *s, NVMTLAirDigest *d) {
    NSData *data=[s dataUsingEncoding:NSUTF8StringEncoding];
    uint64_t size=data.length;
    CC_SHA256_CTX expected; CC_SHA256_Init(&expected);
    CC_SHA256_Update(&expected,&size,sizeof size);
    CC_SHA256_Update(&expected,data.bytes,(CC_LONG)data.length);
    unsigned char a[CC_SHA256_DIGEST_LENGTH],b[CC_SHA256_DIGEST_LENGTH];
    CC_SHA256_CTX actual=d->_ctx;
    CC_SHA256_Final(a,&actual); CC_SHA256_Final(b,&expected);
    assert(!memcmp(a,b,sizeof a));
}
int main(int argc,char **argv) { @autoreleasepool {
    NSString *text=[@"shader AIR utf8 测试\n" stringByPaddingToLength:32768
                                      withString:@"shader AIR utf8 测试\n" startingAtIndex:0];
    assert([text writeToFile:@(argv[1]) atomically:YES encoding:NSUTF8StringEncoding error:nil]);
    NSString *hit=[NSString stringWithContentsOfFile:@(argv[1]) encoding:NSUTF8StringEncoding error:nil];
    for (NSString *s in @[text,hit]) {
        nvmtl_air_text_made(s);
        NVMTLAirDigest *a=nvmtl_air_state(s), *b=nvmtl_air_state(s);
        assert(a && a==b); verify(s,a);
    }
    NSMutableString *mutable=[text mutableCopy];
    NVMTLAirDigest *before=nvmtl_air_state(mutable);
    [mutable appendString:@"changed"];
    NVMTLAirDigest *after=nvmtl_air_state(mutable);
    assert(before!=after); verify(mutable,after);
    NSString *shortText=nvmtl_air_text_made(@"short AIR");
    assert(!objc_getAssociatedObject(shortText,NVMTL_AIRTEXT_KEY));
    NVMTLAirDigest *shortA=nvmtl_air_state(shortText), *shortB=nvmtl_air_state(shortText);
    assert(shortA!=shortB); verify(shortText,shortB);
    assert(!nvmtl_air_state(nil));
    puts("PASS: file and memory AIR reuse digest; SHA256 unchanged; unmarked mutable and short AIR are not cached; nil accepted");
} }
'''
with tempfile.TemporaryDirectory() as directory:
    path=pathlib.Path(directory)
    (path/'test.m').write_text(fixture+body+checks)
    subprocess.run(['xcrun','clang','-fobjc-arc','-Wno-deprecated-declarations',
                    '-framework','Foundation',str(path/'test.m'),'-o',str(path/'test')],check=True)
    subprocess.run([str(path/'test'),str(path/'air.ll')],check=True)
