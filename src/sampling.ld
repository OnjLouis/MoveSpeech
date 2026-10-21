SECTIONS
{
    . = 0x1dc1000;
    .text : { *(.text*) *(.rodata*) }
    /DISCARD/ : { *(.comment) *(.eh_frame*) *(.note*) }
}
