/* SPDX-License-Identifier: MIT */
#include "native_arm64_guard.h"
    .text
    .balign 4
    .global native_arm64_takeover
    .type native_arm64_takeover, %function
native_arm64_takeover:
    mrs x9, CurrentEL
    cmp x9, #8
    b.ne .Lcpu_refuse
    mrs x9, SPSel
    cbz x9, .Lcpu_refuse
    mrs x9, hcr_el2
    tbnz x9, #0, .Lcpu_refuse       /* no stage-2 VM */
    tbnz x9, #34, .Lcpu_refuse      /* no VHE register aliasing */
    mrs x9, sctlr_el2
    tbz x9, #0, .Lcpu_refuse
    tbnz x9, #25, .Lcpu_refuse      /* little endian only */
    mrs x9, tcr_el2
    lsr x10, x9, #32
    cbnz x10, .Lcpu_refuse          /* no extended translation format */
    ubfx x10, x9, #14, #2
    cbnz x10, .Lcpu_refuse          /* inherited 4K granule */
    tbnz x9, #20, .Lcpu_refuse      /* no top-byte ignore */
    ubfx x10, x9, #16, #3
    cmp x10, #5
    b.hi .Lcpu_refuse              /* physical addresses <=48 bits */
    and x10, x9, #0x3f
    cmp x10, #16
    b.lo .Lcpu_refuse
    cmp x10, #39
    b.hi .Lcpu_refuse
    msr daifset, #15
    adrp x9, native_arm64_vectors
    add x9, x9, :lo12:native_arm64_vectors
    msr vbar_el2, x9
    isb
    msr daifclr, #4                /* errors never silently deferred */
    isb
    mov w0, #NG_OK
    ret
.Lcpu_refuse:
    mov w0, #NG_CPU
    ret
    .size native_arm64_takeover, .-native_arm64_takeover

    .global native_arm64_raw_access
    .hidden native_arm64_raw_access
    .type native_arm64_raw_access, %function
native_arm64_raw_access:
    cmp w2, #NG_READ32
    b.eq .Lread32
    cmp w2, #NG_READ64
    b.eq .Lread64
    cmp w2, #NG_WRITE32
    b.eq .Lwrite32
    mov w0, #NG_ARGUMENT
    ret
.Lread32:
.Lload32:
    ldr w3, [x0]
    b .Lread_done
.Lread64:
.Lload64:
    ldr x3, [x0]
.Lread_done:
    dsb sy
    isb
    str x3, [x1]
    mov w0, #NG_OK
    ret
.Lwrite32:
    ldr w3, [x1]
.Lstore32:
    str w3, [x0]
    dsb sy
    isb
    mov w0, #NG_OK
    ret
.Laccess_fault:
    mov w0, #NG_ABORT
    ret
    .size native_arm64_raw_access, .-native_arm64_raw_access

    .macro slot target
    b \target
    .space 124
    .endm
    .balign 2048
    .global native_arm64_vectors
native_arm64_vectors:
    slot native_arm64_fault_stop
    slot native_arm64_fault_stop
    slot native_arm64_fault_stop
    slot native_arm64_fault_stop
    slot .Lsync_spx
    slot native_arm64_fault_stop
    slot native_arm64_fault_stop
    slot native_arm64_fault_stop
    slot native_arm64_fault_stop
    slot native_arm64_fault_stop
    slot native_arm64_fault_stop
    slot native_arm64_fault_stop
    slot native_arm64_fault_stop
    slot native_arm64_fault_stop
    slot native_arm64_fault_stop
    slot native_arm64_fault_stop
    .type .Lsync_spx, %function
.Lsync_spx:
    stp x0, x1, [sp, #-32]!
    stp x2, x3, [sp, #16]
    mrs x0, esr_el2
    ubfx x2, x0, #26, #6
    cmp x2, #0x25                  /* data abort at same EL only */
    b.ne native_arm64_fault_stop
    tbnz x0, #10, native_arm64_fault_stop /* FAR must be valid */
    and x2, x0, #0x3f
    cmp x2, #15
    b.hi native_arm64_fault_stop   /* no external-abort recovery */
    mrs x1, elr_el2
    adr x2, .Lload32
    cmp x1, x2
    b.eq .Lcheck_read
    adr x2, .Lload64
    cmp x1, x2
    b.eq .Lcheck_read
    adr x2, .Lstore32
    cmp x1, x2
    b.ne native_arm64_fault_stop
    tbz x0, #6, native_arm64_fault_stop
    b .Lcheck_far
.Lcheck_read:
    tbnz x0, #6, native_arm64_fault_stop
.Lcheck_far:
    mrs x2, far_el2
    ldr x3, [sp]
    cmp x2, x3
    b.ne native_arm64_fault_stop
    adr x1, .Laccess_fault
    msr elr_el2, x1
    ldp x2, x3, [sp, #16]
    ldp x0, x1, [sp], #32
    eret
    .global native_arm64_fault_stop
    .type native_arm64_fault_stop, %function
native_arm64_fault_stop:
    msr daifset, #15
1:  wfe
    b 1b
    .size native_arm64_fault_stop, .-native_arm64_fault_stop
    .section .note.GNU-stack,"",%progbits
