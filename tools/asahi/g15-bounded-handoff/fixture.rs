// SPDX-License-Identifier: GPL-2.0-only OR MIT
#![allow(dead_code, unused_variables, dropping_copy_types)]
use std::{cell::Cell, marker::PhantomData, mem::ManuallyDrop, ops::{Deref, DerefMut, Range}, sync::{Arc, Mutex, MutexGuard, atomic::{AtomicBool, AtomicU8, AtomicU32, AtomicUsize, Ordering}}};
type Result<T = (), E = i32> = std::result::Result<T, E>;
const EIO: i32 = -5;
const ETIMEDOUT: i32 = -110;
macro_rules! mod_pr_debug { ($($x:tt)*) => {{}}; }
macro_rules! pr_err { ($($x:tt)*) => {{}}; }
macro_rules! dev_err { ($($x:tt)*) => {{}}; }
macro_rules! mod_dev_dbg { ($($x:tt)*) => {{}}; }
thread_local! {
    static CLOCK: Cell<u64> = const { Cell::new(0) };
    static BARRIERS: Cell<u64> = const { Cell::new(0) };
}
struct Monotonic;
struct Instant<T>(u64, PhantomData<T>);
#[derive(PartialEq, PartialOrd)]
struct Delta(u64);
impl Delta { fn from_millis(n: u64) -> Self { Self(n) } }
impl<T> Instant<T> {
    fn now() -> Self { Self(CLOCK.with(Cell::get), PhantomData) }
    fn elapsed(&self) -> Delta { CLOCK.with(|c| { let n=c.get()+1; c.set(n); Delta(n-self.0) }) }
}
mod mem {
    use super::*;
    pub fn sync() {
        std::sync::atomic::fence(Ordering::SeqCst);
        BARRIERS.with(|b| b.set(b.get()+1));
    }
}
struct MutexBackend;
struct Guard<'a, T, B> { guard: MutexGuard<'a, T>, backend: PhantomData<B> }
impl<T, B> Deref for Guard<'_, T, B> { type Target=T; fn deref(&self)->&T { &self.guard } }
impl<T, B> DerefMut for Guard<'_, T, B> { fn deref_mut(&mut self)->&mut T { &mut self.guard } }
struct Handoff { lock_ap: AtomicU8, lock_fw: AtomicU8, turn: AtomicU32 }
struct UatShared { handoff: Arc<Handoff>, legacy: Arc<AtomicUsize>, count: usize }
impl UatShared {
    fn handoff(&self)->&Handoff { &self.handoff }
    fn handoff_lock(&self) { self.legacy.fetch_add(1, Ordering::Relaxed); }
    fn handoff_unlock(&self) { self.legacy.fetch_sub(1, Ordering::Relaxed); }
}
struct UatInner { firmware: UatFirmware, bounded_handoff: bool, failed: AtomicBool, shared: Mutex<UatShared>, locks: AtomicUsize }
impl UatInner {
    fn lock(&self)->Guard<'_, UatShared, MutexBackend> {
        self.locks.fetch_add(1, Ordering::Release);
        Guard { guard: self.shared.lock().unwrap(), backend: PhantomData }
    }
}
fn inner(bounded: bool, interested: bool, turn: u32)->(Arc<UatInner>, Arc<Handoff>) {
    let handoff=Arc::new(Handoff {lock_ap: AtomicU8::new(0), lock_fw: AtomicU8::new(interested.into()), turn: AtomicU32::new(turn)});
    (Arc::new(UatInner {firmware:UatFirmware::Handoff,bounded_handoff: bounded, failed:AtomicBool::new(false), shared:Mutex::new(UatShared {handoff: handoff.clone(), legacy:Arc::new(AtomicUsize::new(0)),count:0}), locks:AtomicUsize::new(0)}), handoff)
}
#[derive(Clone, Copy)]
struct Prot(bool);
impl Prot { fn is_cached_noncoherent(&self)->bool { self.0 } }
#[derive(PartialEq)]
enum UatFirmware { Handoff }
mod gpuvm {
    use super::*;
    pub struct GpuVaFlag;
    impl GpuVaFlag { pub const INVALIDATED: Self=Self; }
    pub struct Flags(pub bool);
    impl Flags { pub fn contains(&self,_:GpuVaFlag)->bool { self.0 } }
    pub struct GpuVa<T> { pub invalidated:bool, pub ty:PhantomData<T> }
    impl<T> GpuVa<T> { pub fn addr(&self)->u64 {0} pub fn length(&self)->u64 {16384} pub fn flags(&self)->Flags { Flags(self.invalidated) } }
    pub struct GpuVmBo<T> { pub backing:Arc<()>, pub ty:PhantomData<T> }
}
use gpuvm::GpuVaFlag;
struct ARef<T>(Arc<()>, PhantomData<T>);
impl<T> From<&gpuvm::GpuVmBo<T>> for ARef<gpuvm::GpuVmBo<T>> {
    fn from(bo:&gpuvm::GpuVmBo<T>)->Self { Self(bo.backing.clone(), PhantomData) }
}
struct PageTable { calls:usize, fail:bool }
impl PageTable {
    fn unmap_pages(&mut self,_:Range<u64>)->Result { self.calls+=1; if self.fail {Err(-14)} else {Ok(())} }
    fn discard_partial_map(&mut self,r:Range<u64>)->Result { self.unmap_pages(r) }
}
struct VmInner { uat_inner:Arc<UatInner>, page_table:PageTable, is_kernel:bool, invalidations:usize }
impl VmInner {
    fn mapping_mutation(&mut self) {}
    fn slot(&self)->Option<u32> {None}
    fn tlbi_range(&self,_:u8,_:u64,_:usize) {}
    fn tlbi_contexts_naming_root(&self,_:u64,_:usize) {}
    fn finish_failed_map(&mut self,_:u64,_:usize) { self.invalidations+=1; }
}
fn vm(uat:Arc<UatInner>, fail:bool)->VmInner { VmInner {uat_inner:uat, page_table:PageTable {calls:0,fail}, is_kernel:true,invalidations:0} }
struct Owner(Mutex<VmInner>);
impl Owner { fn lock_inner(&self)->MutexGuard<'_,VmInner> { self.0.lock().unwrap() } }
struct Node { uat_inner:Arc<UatInner>, owner:Arc<Owner>, prot:Prot, freed:Arc<AtomicUsize> }
impl Drop for Node { fn drop(&mut self) { self.freed.fetch_add(1,Ordering::Relaxed); } }
struct KernelMapping(ManuallyDrop<Node>);
impl KernelMapping {
    fn iova(&self)->u64 {0}
    fn size(&self)->usize {16384}
    fn iova_range(&self)->Range<u64> {0..16384}
    fn remap_uncached_and_flush(&mut self) { panic!("legacy cached teardown reached"); }
}
fn mapping(uat:Arc<UatInner>, cached:bool, fail:bool)->(KernelMapping, Arc<AtomicUsize>, Arc<Owner>) {
    let owner=Arc::new(Owner(Mutex::new(vm(uat.clone(),fail))));
    let freed=Arc::new(AtomicUsize::new(0));
    (KernelMapping(ManuallyDrop::new(Node {uat_inner:uat, owner:owner.clone(), prot:Prot(cached),freed:freed.clone()})),freed,owner)
}
// @PRODUCTION@

#[test]
fn transport_free_and_guard_drop() {
    let (uat,h)=inner(true,false,0);
    let before=BARRIERS.with(Cell::get);
    { let mut g=uat.lock_handoff().unwrap_or_else(|_| panic!()); g.shared_mut().count=7; assert_eq!(h.lock_ap.load(Ordering::Relaxed),1); assert!(uat.shared.try_lock().is_err()); }
    assert_eq!(h.turn.load(Ordering::Relaxed),1);
    assert_eq!(h.lock_ap.load(Ordering::Relaxed),0);
    assert_eq!(uat.shared.lock().unwrap().count,7);
    assert_eq!(BARRIERS.with(Cell::get)-before,4);
    assert!(!uat.failed.load(Ordering::Acquire));
}
#[test]
fn transport_timeout_outer_and_turn_loops() {
    for turn in [0,1] {
        let (uat,h)=inner(true,true,turn);
        let fw=h.lock_fw.load(Ordering::Relaxed);
        assert!(matches!(uat.lock_handoff(),Err(ETIMEDOUT)));
        assert_eq!(h.lock_ap.load(Ordering::Relaxed),0);
        assert_eq!(h.lock_fw.load(Ordering::Relaxed),fw);
        assert_eq!(h.turn.load(Ordering::Relaxed),turn);
        assert!(uat.failed.load(Ordering::Acquire));
        assert!(uat.shared.try_lock().is_ok());
        h.lock_fw.store(0,Ordering::Relaxed);
        let n=uat.locks.load(Ordering::Relaxed);
        assert!(matches!(uat.lock_handoff(),Err(EIO)));
        assert_eq!(uat.locks.load(Ordering::Relaxed),n);
    }
}
#[test]
fn failed_lower_and_upper_mutations_refused() {
    let (uat,h)=inner(true,false,0);
    assert!(matches!(uat.lock_bounded_upper(false),Ok(None)));
    uat.failed.store(true,Ordering::Release);
    for upper in [false,true] { assert!(matches!(uat.lock_bounded_upper(upper),Err(EIO))); }
    assert_eq!(h.lock_ap.load(Ordering::Relaxed),0);
}
#[test]
fn legacy_selection_does_not_use_new_latch_or_transport() {
    let (uat,h)=inner(false,true,1);
    uat.failed.store(true,Ordering::Release);
    {let _g=uat.lock_handoff().unwrap_or_else(|_|panic!()); assert!(uat.shared.try_lock().is_err());}
    assert_eq!(h.lock_ap.load(Ordering::Relaxed),0);
    assert_eq!(uat.shared.lock().unwrap().legacy.load(Ordering::Relaxed),0);
}
#[test]
fn recheck_after_host_mutex_wait() {
    let (uat,h)=inner(true,false,0);
    let held=uat.shared.lock().unwrap();
    let other=uat.clone();
    let task=std::thread::spawn(move || matches!(other.lock_handoff(),Err(EIO)));
    while uat.locks.load(Ordering::Acquire)==0 { std::thread::yield_now(); }
    uat.failed.store(true,Ordering::Release);
    drop(held);
    assert!(task.join().unwrap());
    assert_eq!(h.lock_ap.load(Ordering::Relaxed),0);
}
#[test]
fn two_host_threads_share_one_mutex() {
    let (uat,h)=inner(true,false,0);
    let mut tasks=Vec::new();
    for _ in 0..2 {
        let u=uat.clone();
        tasks.push(std::thread::spawn(move || {
            for _ in 0..2000 {let mut g=u.lock_handoff().unwrap_or_else(|_|panic!()); g.shared_mut().count+=1;}
        }));
    }
    for task in tasks {task.join().unwrap();}
    assert_eq!(uat.shared.lock().unwrap().count,4000);
    assert_eq!(h.lock_ap.load(Ordering::Relaxed),0);
}

struct Model { log:Arc<Mutex<Vec<&'static str>>>, fw:Cell<bool>, turn:Cell<bool>, ticks:Cell<u32>, yield_once:bool }
impl ppl::Memory for Model {
    fn firmware_interested(&self)->bool {self.fw.get()}
    fn firmware_turn(&self)->bool {self.turn.get()}
    fn ap_interest(&self,b:bool) {self.log.lock().unwrap().push(if b {"ap1"} else {"ap0"});}
    fn give_firmware_turn(&self) {self.log.lock().unwrap().push("turn1");}
    fn barrier(&self) {self.log.lock().unwrap().push("barrier");}
    fn expired(&self)->bool {let t=self.ticks.get()+1;self.ticks.set(t);t>3}
    fn relax(&self) {if self.yield_once {self.fw.set(false);self.turn.set(false);}}
    fn fail(&self) {self.log.lock().unwrap().push("failed");}
}
impl Drop for Model {fn drop(&mut self) {self.log.lock().unwrap().push("host-released");}}
fn model(fw:bool,turn:bool,yield_once:bool)->(Model,Arc<Mutex<Vec<&'static str>>>) {
    let log=Arc::new(Mutex::new(Vec::new()));
    (Model {log:log.clone(),fw:Cell::new(fw),turn:Cell::new(turn),ticks:Cell::new(0),yield_once},log)
}
#[test]
fn protocol_orders_unlock_before_host_release() {
    let (m,log)=model(false,false,false);
    let g=ppl::Guard::acquire(m).unwrap_or_else(|_|panic!());
    drop(g);
    assert_eq!(*log.lock().unwrap(),vec!["ap1","barrier","barrier","turn1","barrier","ap0","barrier","host-released"]);
}
#[test]
fn protocol_timeout_cleanup_order_in_both_loops() {
    for turn in [false,true] {
        let (m,log)=model(true,turn,false);
        assert!(ppl::Guard::acquire(m).is_err());
        assert!(log.lock().unwrap().ends_with(&["ap0","barrier","failed","host-released"]));
    }
}
#[test]
fn protocol_yield_reassert_and_complete() {
    let (m,log)=model(true,true,true);
    drop(ppl::Guard::acquire(m).unwrap_or_else(|_|panic!()));
    assert_eq!(*log.lock().unwrap(),vec!["ap1","barrier","ap0","barrier","ap1","barrier","barrier","turn1","barrier","ap0","barrier","host-released"]);
}
#[test]
fn failed_gpuva_unmap_keeps_leaves_untouched() {
    for invalidated in [false,true] {
        let (u,_)=inner(true,false,0);u.failed.store(true,Ordering::Release);
        let mut v=vm(u,false);
        assert_eq!(v.unmap_gpuva(&gpuvm::GpuVa {invalidated,ty:PhantomData}),Err(EIO));
        assert_eq!(v.page_table.calls,0);
    }
}
#[test]
fn gpuva_partial_unmap_latches_and_preserves_logical_owner() {
    for invalidated in [false,true] {
        let (u,_)=inner(true,false,0);
        let mut v=vm(u.clone(),true);
        assert_eq!(v.unmap_gpuva(&gpuvm::GpuVa {invalidated,ty:PhantomData}),Err(-14));
        assert!(u.failed.load(Ordering::Acquire));
        assert_eq!(v.page_table.calls,1);
    }
}
#[test]
fn successful_gpuva_unmap_is_not_latched() {
    let (u,_)=inner(true,false,0);let mut v=vm(u.clone(),false);
    assert_eq!(v.unmap_gpuva(&gpuvm::GpuVa {invalidated:false,ty:PhantomData}),Ok(()));
    assert_eq!(v.page_table.calls,1);assert!(!u.failed.load(Ordering::Acquire));
}
#[test]
fn failed_uninserted_map_retains_backing_without_allocation() {
    let (u,_)=inner(true,false,0);let mut v=vm(u.clone(),true);
    let backing=Arc::new(());let bo=gpuvm::GpuVmBo::<VmInner>{backing:backing.clone(),ty:PhantomData};
    v.rollback_failed_map(0,16384,Some(&bo));
    assert_eq!(Arc::strong_count(&backing),3);
    assert!(u.failed.load(Ordering::Acquire));assert_eq!(v.invalidations,1);
}
#[test]
fn successful_rollback_releases_normal_backing() {
    let (u,_)=inner(true,false,0);let mut v=vm(u.clone(),false);
    let backing=Arc::new(());let bo=gpuvm::GpuVmBo::<VmInner>{backing:backing.clone(),ty:PhantomData};
    v.rollback_failed_map(0,16384,Some(&bo));
    assert_eq!(Arc::strong_count(&backing),2);assert!(!u.failed.load(Ordering::Acquire));
}
#[test]
fn cached_mapping_retained_before_legacy_flush() {
    let (u,_)=inner(true,false,0);let (map,freed,owner)=mapping(u.clone(),true,false);
    drop(map);assert_eq!(freed.load(Ordering::Relaxed),0);assert_eq!(owner.0.lock().unwrap().page_table.calls,0);assert!(u.failed.load(Ordering::Acquire));
}
#[test]
fn mapping_timeout_retains_node_and_backing() {
    let (u,h)=inner(true,true,1);let (map,freed,owner)=mapping(u.clone(),false,false);
    drop(map);assert_eq!(freed.load(Ordering::Relaxed),0);assert_eq!(owner.0.lock().unwrap().page_table.calls,0);assert_eq!(h.lock_ap.load(Ordering::Relaxed),0);assert!(u.failed.load(Ordering::Acquire));
}
#[test]
fn latched_lower_mapping_retains_node_and_backing() {
    let (u,_)=inner(true,false,0);let (map,freed,owner)=mapping(u.clone(),false,false);
    owner.0.lock().unwrap().is_kernel=false;u.failed.store(true,Ordering::Release);
    drop(map);assert_eq!(freed.load(Ordering::Relaxed),0);assert_eq!(owner.0.lock().unwrap().page_table.calls,0);
}
#[test]
fn partial_kernel_unmap_retains_node_and_backing() {
    let (u,_)=inner(true,false,0);let (map,freed,owner)=mapping(u.clone(),false,true);
    drop(map);assert_eq!(freed.load(Ordering::Relaxed),0);assert_eq!(owner.0.lock().unwrap().page_table.calls,1);assert!(u.failed.load(Ordering::Acquire));
}
#[test]
fn successful_uncached_drop_releases_node_once() {
    let (u,h)=inner(true,false,0);let (map,freed,owner)=mapping(u.clone(),false,false);
    drop(map);assert_eq!(freed.load(Ordering::Relaxed),1);assert_eq!(owner.0.lock().unwrap().page_table.calls,1);assert!(!u.failed.load(Ordering::Acquire));assert_eq!(h.lock_ap.load(Ordering::Relaxed),0);
}


mod backend {
    use super::*;
    type Completions=Vec<i32>;
    struct Shared { failures:AtomicUsize }
    impl Shared { fn fail_stop(&self,_:i32) {self.failures.fetch_add(1,Ordering::Relaxed);} }
    struct Session { uat:Arc<UatInner>, shared:Shared }
    impl Session {fn uat(&self)->Arc<UatInner> {self.uat.clone()} }
    struct Firmware { session:Session, failures:usize }
    impl Firmware {
        fn fail_stop(&mut self,error:i32,done:&mut Completions) {self.failures+=1;done.push(error);}
        // @BACKEND@
    }
    #[test]
    fn runtime_uat_failure_bridges_to_backend_and_shared_stop() {
        let (u,_)=inner(true,false,0);
        let mut f=Firmware {session:Session {uat:u.clone(),shared:Shared {failures:AtomicUsize::new(0)}},failures:0};
        let mut done=Vec::new();
        assert_eq!(f.check_uat(&mut done),Ok(()));assert_eq!(f.failures,0);assert!(done.is_empty());
        u.failed.store(true,Ordering::Release);
        assert_eq!(f.check_uat(&mut done),Err(EIO));assert_eq!(f.failures,1);assert_eq!(done,vec![EIO]);
        assert_eq!(f.session.shared.failures.load(Ordering::Relaxed),1);
    }
    #[test]
    fn runtime_legacy_uat_is_unchanged() {
        let (u,_)=inner(false,false,0);u.failed.store(true,Ordering::Release);
        let mut f=Firmware {session:Session {uat:u,shared:Shared {failures:AtomicUsize::new(0)}},failures:0};
        let mut done=Vec::new();assert_eq!(f.check_uat(&mut done),Ok(()));assert_eq!(f.failures,0);assert!(done.is_empty());
    }
}
