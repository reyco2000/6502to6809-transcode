; fill_template.asm - skeleton of a hi-res rectangle native (6502to6809 kit).  Not assembled as is.
; Replace <...> with the game's ABI.  Typical 6502 original:  for y = top..bottom: ptr = rowtab[y] + col0;
; for x = 0..w-1: (ptr),Y = value.
; The Apple row address of line y is  $2000*page + (y&7)*$400 + ((y>>3)&7)*$80 + (y>>6)*$28.
; Most games keep a row table in RAM (lo/hi, 192 entries): read it (it is game state, already in Apple RAM).
	include	hires.inc
nf_fill					; <entry: the 6502 routine's address goes in spec.NATIVE>
	pshs	b			; keep X (B) if the 6502 routine preserves it
	lda	<$TOP			; <zero page inputs>
	sta	vr_y0
	lda	<$BOTTOM
	sta	vr_y1
	lda	<$COL0
	sta	vr_c0
	lda	<$COL1
	sta	vr_c1
	HRON
nf_row	; U = row address: rowtab_lo/hi[y] + page high byte + col0
	; inner loop: unroll (STD ,U++) for spans; keep the 6502's byte order if the routine reads back
	; ...
	HROFF
	lda	<$PAGEHI		; report the rectangle once
	jsr	vm_rect
	; exit state: write back every zero-page scratch byte the 6502 loop leaves (counters, pointers), then set
	; A / RY / flags as the 6502 RTS sees them (only what report.txt says the callers read)
	puls	b,pc
