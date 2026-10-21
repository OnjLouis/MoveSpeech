.text
.extern stock_draw_text
.extern speak_filtered
.extern stock_waveform_draw
.extern speak_slice
.extern speak_sample

.macro speech_hook name, surface, gain
.global \name
.type \name, %function
\name:
    stp x29, x30, [sp, #-64]!
    mov x29, sp
    str x5, [sp, #16]
    bl stock_draw_text
    stp x0, x1, [sp, #32]
    ldr x8, [sp, #16]
    ldrb w9, [x8]
    ldp x10, x11, [x8, #8]
    lsr x12, x9, #1
    tst w9, #1
    csinc x1, x11, x8, ne
    csel x2, x12, x10, eq
    mov x0, \surface
    mov w3, #\gain
    bl speak_filtered
    ldp x0, x1, [sp, #32]
    ldp x29, x30, [sp], #64
    ret
.size \name, . - \name
.endm

speech_hook sampling_speech, x20, 0
speech_hook gain_speech, x19, 1
speech_hook slice_count_speech, x19, 2

.macro waveform_hook name, speaker
.global \name
.type \name, %function
\name:
    stp x29, x30, [sp, #-64]!
    mov x29, sp
    stp x0, x1, [sp, #16]
    bl stock_waveform_draw
    stp x0, x1, [sp, #32]
    ldp x1, x0, [sp, #16]
    bl \speaker
    ldp x0, x1, [sp, #32]
    ldp x29, x30, [sp], #64
    ret
.size \name, . - \name
.endm

waveform_hook slice_waveform_speech, speak_slice
waveform_hook sample_waveform_speech, speak_sample

.extern stock_draw_lines
.extern speak_velocity
.global step_velocity_speech
.type step_velocity_speech, %function
step_velocity_speech:
    sub sp, sp, #80
    stp x29, x30, [sp, #16]
    add x29, sp, #16
    stp x6, x7, [sp, #32]
    str x19, [sp, #48]
    ldr x9, [sp, #80]
    str x9, [sp]
    bl stock_draw_lines
    stp x0, x1, [sp, #64]
    ldr x0, [sp, #48]
    sub x0, x0, #0x50
    ldp x1, x2, [sp, #32]
    bl speak_velocity
    ldp x0, x1, [sp, #64]
    ldp x29, x30, [sp, #16]
    add sp, sp, #80
    ret
.size step_velocity_speech, . - step_velocity_speech
