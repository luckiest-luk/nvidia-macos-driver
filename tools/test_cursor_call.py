#!/usr/bin/env python3
"""Exercise the real cursor scheduling/lifetime methods with XNU ONCE semantics.

This models the documented enqueue/cancel return values, not GPU execution.
It covers moves submitted before/during execution, cancellation, stopping,
and reference balance when a running callback has a deferred invocation.
Running calls hold an internal XNU reference, so free may defer destruction.
"""
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / 'kexts/NVRMFB/fb/nvrm-fb.cpp').read_text()


def method(signature):
    start = SOURCE.index(signature)
    end = SOURCE.index('{', start)
    depth = 1
    end += 1
    while depth:
        depth += (SOURCE[end] == '{') - (SOURCE[end] == '}')
        end += 1
    return SOURCE[start:end]


allocation = re.search(r'fCursorCall = (thread_call_allocate[^;]+);', SOURCE)[1]
methods = '\n'.join(method(signature) for signature in (
    'void NVRMNVDAFramebuffer::scheduleCall(',
    'IOReturn NVRMNVDAFramebuffer::setCursorState(',
    'void NVRMNVDAFramebuffer::cursorFire(',
    'void NVRMNVDAFramebuffer::stopCursorCall(',
))

HARNESS = r'''
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <future>
#include <mutex>
#include <thread>
using SInt32 = int;
using IOReturn = int;
using IOInterruptState = int;
using thread_call_param_t = void *;
const int kIOReturnSuccess=0, kIOReturnUnsupported=1;
const int NV_KMS_FLIP_RESULT_SUCCESS=0;
const int THREAD_CALL_PRIORITY_KERNEL=1, THREAD_CALL_OPTIONS_ONCE=1;
struct Call { void (*fn)(void*,void*); void *owner;
    bool pending=false, running=false, once; int priority; bool freed=false; };
using thread_call_t = Call *;
std::mutex scheduler;
thread_call_t thread_call_allocate_with_options(void (*fn)(void*,void*), void *p, int pri, int opts) {
    auto *c = new Call{fn,p,false,false,opts==THREAD_CALL_OPTIONS_ONCE,pri};
    return c;
}
bool thread_call_enter(thread_call_t c) {
    std::lock_guard<std::mutex> lock(scheduler);
    bool was=c->pending; c->pending=true; return was;
}
bool thread_call_enter_delayed(thread_call_t c, uint64_t) { return thread_call_enter(c); }
void begin(thread_call_t c) {
    std::lock_guard<std::mutex> lock(scheduler);
    assert(c->pending && !c->running);
    c->pending=false; c->running=true;
}
void finish(thread_call_t c) {
    assert(c->running); c->fn(c->owner,nullptr);
    std::lock_guard<std::mutex> lock(scheduler);
    c->running=false;
    if (c->freed) delete c;
}
bool thread_call_cancel(thread_call_t c) {
    std::lock_guard<std::mutex> lock(scheduler);
    bool canceled=c->pending; c->pending=false;
    return canceled;
}
bool thread_call_free(thread_call_t c) {
    std::lock_guard<std::mutex> lock(scheduler);
    assert(!c->pending);
    if (c->running) c->freed=true; else delete c;
    return true;
}
std::recursive_mutex displayGate;
struct NVRMDisplayGate {
    std::lock_guard<std::recursive_mutex> lock{displayGate};
    bool valid() const { return true; }
};
std::mutex applyMutex;
std::condition_variable applyCV;
bool pauseApply=false, enteredApply=false, resumeApply=false;
IOInterruptState IOSimpleLockLockDisableInterrupt(void*) { return 0; }
void IOSimpleLockUnlockEnableInterrupt(void*, IOInterruptState) {}
int OSAddAtomic(int a, int *p) { return __atomic_fetch_add(p,a,__ATOMIC_SEQ_CST); }
struct NVRMNVDAFramebuffer {
    thread_call_t fCursorCall=nullptr;
    void *fFlipCacheLock=this, *fCurStateLock=this, *fCurSurf=this;
    int fStopping=0, fCurX=0, fCurY=0, fCurRc=0, applied=0, lastX=-1;
    std::atomic<int> refs{1};
    bool fCurVisible=false;
    void retain() { ++refs; }
    void release() { assert(refs>1); --refs; }
    void cursorApply() {
        ++applied; lastX=fCurX;
        std::unique_lock<std::mutex> lock(applyMutex);
        if (pauseApply) {
            enteredApply=true; applyCV.notify_all();
            applyCV.wait(lock,[]{return resumeApply;});
        }
    }
    void init() { fCursorCall=ALLOCATION; assert(fCursorCall->once);
        assert(fCursorCall->priority==THREAD_CALL_PRIORITY_KERNEL); }
    void scheduleCall(thread_call_t, uint64_t);
    IOReturn setCursorState(SInt32,SInt32,bool);
    static void cursorFire(void*,void*);
    void stopCursorCall();
};
struct NVRMCallReference {
    NVRMNVDAFramebuffer *owner;
    explicit NVRMCallReference(NVRMNVDAFramebuffer *p):owner(p) {}
    ~NVRMCallReference() { owner->release(); }
};
METHODS
int main() {
    {
        NVRMNVDAFramebuffer f; f.init();
        for (int i=1;i<=10000;++i) f.setCursorState(i,i,true);
        assert(f.refs==2 && f.fCursorCall->pending);
        begin(f.fCursorCall);
        // While a callback is blocked on the display gate, later moves reserve
        // one deferred callback/reference, regardless of the event count.
        for (int i=10001;i<=20000;++i) f.setCursorState(i,i,true);
        assert(f.refs==3 && f.fCursorCall->pending);
        finish(f.fCursorCall);
        assert(f.refs==2 && f.lastX==20000);
        begin(f.fCursorCall); finish(f.fCursorCall);
        assert(f.refs==1 && f.applied==2);
        f.fStopping=1; f.stopCursorCall(); assert(!f.fCursorCall);
    }
    for (int running=0;running<2;++running) {
        for (int deferred=0;deferred<2;++deferred) {
            NVRMNVDAFramebuffer f; f.init();
            f.setCursorState(1,1,true);
            if (running) begin(f.fCursorCall);
            if (deferred) f.setCursorState(2,2,true);
            f.fStopping=1;
            f.setCursorState(3,3,true); // stop rejects new submissions
            auto *call=f.fCursorCall;
            f.stopCursorCall();
            if (running) {
                assert(f.refs==2); // running invocation still owns its retain
                finish(call);     // sees stopping, touches no cursor resources
            }
            assert(f.refs==1 && !f.fCursorCall && f.applied==0);
        }
    }
    {
        NVRMNVDAFramebuffer f; f.init(); f.setCursorState(1,1,true);
        auto *call=f.fCursorCall;
        pauseApply=true; begin(call);
        std::thread callback([&]{finish(call);});
        {
            std::unique_lock<std::mutex> lock(applyMutex);
            applyCV.wait(lock,[]{return enteredApply;});
        }
        __atomic_store_n(&f.fStopping,1,__ATOMIC_SEQ_CST);
        std::promise<void> started, stopped;
        auto start=started.get_future(), done=stopped.get_future();
        std::thread stop([&]{started.set_value(); f.stopCursorCall(); stopped.set_value();});
        start.wait();
        assert(done.wait_for(std::chrono::milliseconds(20))==std::future_status::timeout);
        { std::lock_guard<std::mutex> lock(applyMutex); resumeApply=true; }
        applyCV.notify_all();
        callback.join(); stop.join();
        assert(f.refs==1 && f.applied==1 && !f.fCursorCall);
    }
    std::puts("PASS: cursor storms coalesce; latest state retained; late callbacks skip work; references balance");
}
'''

with tempfile.TemporaryDirectory() as tmp:
    cpp, binary = Path(tmp) / 'cursor.cpp', Path(tmp) / 'cursor'
    cpp.write_text(HARNESS.replace('ALLOCATION', allocation).replace('METHODS', methods))
    subprocess.run(['c++', '-std=c++17', '-O1', str(cpp), '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True, timeout=15)
