// SPDX-License-Identifier: MIT
#![allow(dead_code, non_camel_case_types)]
extern crate self as kernel;
use std::{cell::RefCell, ops::{Deref,DerefMut,Range}};
pub use std::fmt;
#[derive(Clone,Copy,Debug,PartialEq,Eq)]
pub struct Error(i32);
impl From<std::num::TryFromIntError> for Error { fn from(_: std::num::TryFromIntError)->Self { EINVAL } }
type Result<T=()> = std::result::Result<T,Error>;
const EINVAL:Error=Error(-22);
const ENODEV:Error=Error(-19);
const ENOMEM:Error=Error(-12);
const GFP_KERNEL:u32=0;
mod fixture_macros {
    macro_rules! c_str {($value:literal)=>{$value};}
    pub(crate) use c_str;
}
pub(crate) use fixture_macros::c_str;
pub mod str { pub type CStr = str; }
pub mod device { pub struct Device {pub id:usize} impl Device {pub fn as_raw(&self)->usize{self.id}} }
pub mod sync {
    pub struct Arc<T>(std::sync::Arc<T>);
    thread_local! {pub static FAIL_ORIGIN:std::cell::Cell<bool>=const{std::cell::Cell::new(false)};}
    impl<T> Arc<T>{pub fn new(value:T,_:u32)->crate::Result<Self>{
        if FAIL_ORIGIN.with(|v|v.get()){Err(crate::ENOMEM)}else{Ok(Self(std::sync::Arc::new(value)))}}
        pub fn ptr_eq(left:&Self,right:&Self)->bool{std::sync::Arc::ptr_eq(&left.0,&right.0)}}
    impl<T> Clone for Arc<T>{fn clone(&self)->Self{Self(self.0.clone())}}
    pub mod aref {
        pub struct ARef<T>{value:T}
        impl<T> std::ops::Deref for ARef<T>{type Target=T;fn deref(&self)->&T{&self.value}}
        impl From<&crate::device::Device> for ARef<crate::device::Device>{fn from(dev:&crate::device::Device)->Self{
            crate::STATE.with(|s|s.borrow_mut().device_refs+=1);Self{value:crate::device::Device{id:dev.id}}}}
        impl<T> Drop for ARef<T>{fn drop(&mut self){crate::STATE.with(|s|s.borrow_mut().device_refs-=1);}}
    }
}
mod prelude { pub(crate) use crate::{Result,EINVAL,ENODEV,ENOMEM,KVVec,GFP_KERNEL}; }
mod hw { FIRMWARE_BUILD_TYPE FIRMWARE_MEMORY_CONSTANTS }
mod board { pub mod record { NATIVE_CONTRACT } }
mod adt {
    use super::{Range,Result,EINVAL};
    ADT_HELPERS
    mod native {use super::{Adt,le32};type R<T>=std::result::Result<T,&'static str>;NATIVE_HELPERS}
    #[path="NATIVE_REGIONS_SOURCE"] pub mod native_firmware;
    #[path="SOURCE_IDENTITY"] pub mod source;
    pub struct NativeAdt{source:source::Source,data:Vec<u8>}
    impl NativeAdt{pub fn new()->Self{Self::with_data(vec![NATIVE_ADT_BYTES])}
        pub fn with_data(data:Vec<u8>)->Self{Self{source:source::Source::new().unwrap(),data}}
        pub fn source_identity(&self)->&source::Source{&self.source}
        NATIVE_REGION_METHOD
    }
    pub fn allocation_failure(){
        crate::sync::FAIL_ORIGIN.with(|v|v.set(true));assert!(matches!(source::Source::new(),Err(crate::ENOMEM)));
        crate::sync::FAIL_ORIGIN.with(|v|v.set(false));
    }
    pub struct GpuAdt { pub text:Range<u64>,pub data:Range<u64>,pub native:Option<NativeAdt> }
}
struct State {
    bytes:Vec<u8>,physical:u64,digest:[u8;32],machine:&'static str,prefix:u64,
    maps:u32,unmaps:u32,hashes:u32,prefix_reads:u32,mapped:bool,map_fail:bool,alloc_fail:bool,
    logs:Vec<String>,device_refs:u32,
}
thread_local! { static STATE:RefCell<State>=RefCell::new(State {
    bytes:Vec::new(),physical:0x10000,digest:[0;32],machine:"apple,j514m",prefix:0,maps:0,unmaps:0,hashes:0,
    prefix_reads:0,mapped:false,map_fail:false,alloc_fail:false,logs:Vec::new(),device_refs:0,
}); }
macro_rules! dev_info { ($dev:expr,$($arg:tt)*) => {{ let _=$dev; $crate::STATE.with(|s|s.borrow_mut().logs.push(format!($($arg)*))); }}; }
macro_rules! dev_warn { ($($arg:tt)*) => { dev_info!($($arg)*); }; }
macro_rules! dev_err { ($($arg:tt)*) => { dev_info!($($arg)*); }; }
pub struct KVVec<T>(Vec<T>);
impl<T:Clone> KVVec<T> {
    fn from_elem(value:T,count:usize,flags:u32)->Result<Self> {
        assert_eq!(flags,GFP_KERNEL);
        if STATE.with(|s|s.borrow().alloc_fail) { Err(ENOMEM) } else { Ok(Self(vec![value;count])) }
    }
}
impl<T> Deref for KVVec<T> { type Target=Vec<T>;fn deref(&self)->&Vec<T>{ &self.0 } }
impl<T> DerefMut for KVVec<T> { fn deref_mut(&mut self)->&mut Vec<T>{ &mut self.0 } }
pub mod bindings {
    use super::*;
    pub const MEMREMAP_WB:u64=2;
    pub unsafe fn memremap(physical:u64,size:usize,mode:u64)->*mut core::ffi::c_void {
        STATE.with(|s| { let mut s=s.borrow_mut();
            assert_eq!((physical,size,mode),(s.physical,s.bytes.len(),MEMREMAP_WB));
            assert!(!s.mapped);s.maps+=1;
            if s.map_fail { core::ptr::null_mut() } else { s.mapped=true;s.bytes.as_mut_ptr().cast() }
        })
    }
    pub unsafe fn memunmap(pointer:*mut core::ffi::c_void) {
        STATE.with(|s| {let mut s=s.borrow_mut();assert!(s.mapped);
            assert_eq!(pointer,s.bytes.as_mut_ptr().cast());s.mapped=false;s.unmaps+=1;
        });
    }
}
mod boot { pub mod glue {
    use crate::*;
    pub fn machine(name:&str)->bool { STATE.with(|s|s.borrow().machine==name) }
    pub fn sha256(bytes:&[u8])->[u8;32] {
        STATE.with(|s| {let mut s=s.borrow_mut();assert!(!s.mapped);
            assert_eq!(bytes.len(),s.bytes.len());assert_eq!(&bytes[40..48],&[0;8]);
            s.hashes+=1;s.digest
        })
    }
} }
mod params { pub fn accept_text_prefix()->u64 { crate::STATE.with(|s| {let mut s=s.borrow_mut();s.prefix_reads+=1;s.prefix}) } }
#[path="IDENTITY_SOURCE"] mod identity;

fn build(machine:Option<&'static str>,contract:Option<u32>)->hw::FirmwareBuild {
    hw::FirmwareBuild {name:"invented image",machine,native_board_contract:contract,
        text_size:0x4000,data_size:0x4000,marker:32,text_sha256:[0x5a;32]}
}
fn case(native:bool,scenario:u32)
{
    let contract=board::record::NATIVE_CONTRACT;
    let mut bytes=vec![0x21;0x4000];bytes[32..40].copy_from_slice(&[0x47,0x4b,0x54,0x53,8,0,0,0]);
    bytes[40..48].fill(0xe7);
    STATE.with(|s|*s.borrow_mut()=State {bytes,physical:0x10000,digest:[0x5a;32],machine:"apple,j514m",prefix:0x5a5a5a5a5a5a5a5a,
        maps:0,unmaps:0,hashes:0,prefix_reads:0,mapped:false,map_fail:false,alloc_fail:false,logs:Vec::new(),device_refs:0});
    let mut builds=vec![build(Some("apple,j514m"),Some(contract))];
    let mut chip=0x6034;
    let mut adt=adt::GpuAdt{text:0x10000..0x14000,data:0x20000..0x24000,native:Some(adt::NativeAdt::new())};
    let mut expected:Result<Option<usize>>=Ok(Some(0));
    let mut early=false;let mut no_hash=false;
    match scenario {
        0=>{},
        1=>{builds[0].text_sha256=[0xa1;32];expected=if native{Err(EINVAL)}else{Ok(None)};},
        2=>{builds[0].text_sha256[31]^=1;expected=if native{Err(EINVAL)}else{Ok(None)};},
        3=>{builds[0].text_size+=8;expected=if native{early=true;Err(EINVAL)}else{Ok(None)};},
        4=>{builds[0].data_size+=8;expected=if native{early=true;Err(EINVAL)}else{Ok(None)};},
        5=>{builds[0].marker+=8;expected=if native{Err(EINVAL)}else{Ok(None)};},
        6=>{builds[0].machine=Some("other");expected=if native{early=true;Err(EINVAL)}else{Ok(None)};},
        7=>{builds[0].machine=None;if native{expected=Err(EINVAL);early=true;}},
        8=>{builds[0].native_board_contract=None;if native{expected=Err(EINVAL);early=true;}},
        9=>{builds[0].native_board_contract=Some(contract^1);if native{expected=Err(EINVAL);early=true;}},
        10=>{builds.clear();expected=if native{early=true;Err(EINVAL)}else{Ok(None)};},
        11=>{builds[0].text_sha256[31]^=1;STATE.with(|s|s.borrow_mut().prefix=0);expected=Err(EINVAL);},
        12=>{builds[0].native_board_contract=None;builds.push(build(Some("apple,j514m"),Some(contract)));expected=Ok(Some(if native{1}else{0}));},
        13=>{builds[0].machine=Some("other");builds.push(build(Some("apple,j514m"),Some(contract)));expected=Ok(Some(1));},
        14=>{STATE.with(|s|s.borrow_mut().bytes[32]=0);expected=Err(EINVAL);no_hash=true;},
        15=>{STATE.with(|s|{let marker=s.borrow().bytes[32..40].to_vec();s.borrow_mut().bytes[64..72].copy_from_slice(&marker);});expected=Err(EINVAL);no_hash=true;},
        16=>{STATE.with(|s|{let marker=s.borrow().bytes[32..40].to_vec();let mut s=s.borrow_mut();s.bytes[32..40].fill(0);s.bytes[33..41].copy_from_slice(&marker);});expected=Err(EINVAL);no_hash=true;},
        17=>{STATE.with(|s|{let marker=s.borrow().bytes[32..40].to_vec();let mut s=s.borrow_mut();s.bytes[32..40].fill(0);s.bytes[0x3ff8..0x4000].copy_from_slice(&marker);});expected=Err(EINVAL);no_hash=true;},
        18=>{STATE.with(|s|s.borrow_mut().map_fail=true);expected=Err(ENOMEM);no_hash=true;},
        19=>{STATE.with(|s|s.borrow_mut().alloc_fail=true);expected=Err(ENOMEM);no_hash=true;},
        20=>{chip=0x6031;if native{expected=Err(ENODEV);early=true;}},
        21=>{adt.native=None;if native{expected=Err(ENODEV);early=true;}},
        22=>{builds[0].text_sha256[31]^=1;STATE.with(|s|s.borrow_mut().prefix=1);expected=Err(EINVAL);},
        23=>{STATE.with(|s|s.borrow_mut().prefix=1);},
        24=>{STATE.with(|s|s.borrow_mut().bytes[40..48].copy_from_slice(&[1,2,3,4,5,6,7,8]));},
        25=>{STATE.with(|s|s.borrow_mut().machine="other");builds[0].machine=Some("other");if native{expected=Err(ENODEV);early=true;}},
        26=>{adt.text.end=adt.text.start;expected=Err(EINVAL);early=true;},
        27=>{adt.text.end=adt.text.start-1;expected=Err(EINVAL);early=true;},
        28=>{adt.data.end=adt.data.start;expected=Err(EINVAL);early=true;},
        29=>{adt.data.end=adt.data.start-1;expected=Err(EINVAL);early=true;},
        30=>{if native{adt.text.end=u64::MAX;expected=Err(EINVAL);early=true;}},
        31=>{builds[0].marker+=1;expected=if native{early=true;Err(EINVAL)}else{Ok(None)};},
        32=>{builds[0].marker=0x4000;expected=if native{early=true;Err(EINVAL)}else{Ok(None)};},
        33=>{builds[0].marker=0x3ff8;expected=if native{early=true;Err(EINVAL)}else{Ok(None)};},
        34=>{builds[0].text_size+=8;builds.push(build(Some("apple,j514m"),Some(contract)));expected=Ok(Some(1));},
        35=>{builds[0].native_board_contract=None;STATE.with(|s|s.borrow_mut().map_fail=true);
             expected=if native{early=true;Err(EINVAL)}else{no_hash=true;Err(ENOMEM)};},
        _=>unreachable!(),
    }
    if !native && scenario == 10 { expected=Err(EINVAL); }
    let builds: &'static [hw::FirmwareBuild]=Box::leak(builds.into_boxed_slice());
    let original=STATE.with(|s|s.borrow().bytes.clone());
    let dev=device::Device{id:1};
    let outcome=if native {
        identity::native_firmware(&dev,&adt,builds,chip).map(|token|{
            let moved=adt.native.take().unwrap();
            assert!(token.matches_source(&moved));assert!(token.matches(&dev,&moved));
            assert!(!token.matches(&device::Device{id:2},&moved));
            assert!(!token.matches_source(&adt::NativeAdt::new()));
            STATE.with(|s|assert_eq!(s.borrow().device_refs,1));drop(moved);
            for _ in 0..64{assert!(!token.matches_source(&adt::NativeAdt::new()));}
            Some(builds.iter().position(|b|core::ptr::eq(b,token.build())).unwrap())})
    } else {
        identity::firmware(&dev,&adt,builds,true).map(|found|
            found.map(|found|builds.iter().position(|b|core::ptr::eq(b,found)).unwrap()))
    };
    assert_eq!(outcome,expected,"native {native} scenario {scenario}");
    STATE.with(|s| {let s=s.borrow();
        assert_eq!(s.bytes,original);assert!(!s.mapped);
        assert_eq!(s.device_refs,0);
        assert_eq!(s.prefix_reads,u32::from(!native && scenario != 10));
        assert_eq!(s.maps,u32::from(!early),"mapping before admission: native {native} scenario {scenario}");
        assert_eq!(s.unmaps,u32::from(!early&&scenario!=18&&scenario!=35));
        assert_eq!(s.hashes,u32::from(!early&&!no_hash));
        if native {assert!(!s.logs.iter().any(|log|log.contains("admitted by g15_accept")));}
    });
}
REGION_CASES
fn main()
{
    std::panic::set_hook(Box::new(|info|{eprintln!("{info}");std::process::exit(101);}));
    for native in [true,false] { for scenario in 0..36 {case(native,scenario);} }
    adt::allocation_failure();
    region_cases();
    println!("72 actual firmware identity/policy/token cases; native descriptor preflight before any copy, invalid-range rejection, actual copy/normalization/lookup; modeled RAM/allocation/SHA/machine/parameter/log services; no hardware");
}
