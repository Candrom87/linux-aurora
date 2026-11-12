// SPDX-License-Identifier: MIT
fn region_cases() {
    use std::io::Read;
    let mut bytes=Vec::new();std::io::stdin().read_to_end(&mut bytes).unwrap();
    let mut offset=0;let mut count=0;let mut accepted=0;
    while offset<bytes.len() {
        let word=|at|u32::from_le_bytes(bytes[at..at+4].try_into().unwrap());
        let long=|at|u64::from_le_bytes(bytes[at..at+8].try_into().unwrap());
        let chip=word(offset);let want=word(offset+4)!=0;
        let text=long(offset+8)..long(offset+16);let data=long(offset+24)..long(offset+32);
        let length=word(offset+40) as usize;offset+=44;
        let raw=bytes[offset..offset+length].to_vec();offset+=length;
        let size=text.end.checked_sub(text.start).filter(|&n|n>=0x4000&&n<=0x200000).unwrap_or(0x4000) as usize;
        let mut loaded=vec![0x21;size];loaded[32..40].copy_from_slice(&[0x47,0x4b,0x54,0x53,8,0,0,0]);loaded[40..48].fill(0xe7);
        STATE.with(|s|*s.borrow_mut()=State{bytes:loaded,physical:text.start,digest:[0x5a;32],machine:"apple,j514m",prefix:0x5a5a5a5a5a5a5a5a,
            maps:0,unmaps:0,hashes:0,prefix_reads:0,mapped:false,map_fail:false,alloc_fail:false,logs:Vec::new(),device_refs:0});
        let adt=adt::GpuAdt{text,data,native:Some(adt::NativeAdt::with_data(raw))};
        let source=adt.native.as_ref().unwrap();
        assert_eq!(source.check_firmware_regions(chip,&adt.text,&adt.data).is_ok(),want,"native region parser case {count}");
        let mut build=build(Some("apple,j514m"),Some(board::record::NATIVE_CONTRACT));
        build.text_size=size as u64;
        build.data_size=adt.data.end.checked_sub(adt.data.start).unwrap_or(0x4000);
        let builds=Box::leak(vec![build].into_boxed_slice());let dev=device::Device{id:77};
        let outcome=identity::native_firmware(&dev,&adt,builds,chip);
        assert_eq!(outcome.is_ok(),want,"native region entry case {count}");
        if let Ok(token)=outcome {assert!(token.matches(&dev,source));accepted+=1;}
        STATE.with(|s|{let s=s.borrow();assert_eq!((s.maps,s.unmaps,s.hashes),(u32::from(want),u32::from(want),u32::from(want)),"native region actor order case {count}");
            assert_eq!(s.prefix_reads,0);assert_eq!(s.device_refs,0);assert!(!s.mapped);});
        count+=1;
    }
    assert_eq!(offset,bytes.len());println!("native_region_cases={count} accepted={accepted}");
}
