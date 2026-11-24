; paddle.asm - paddles from the CoCo right joystick (6502to6809 kit, Apple II source).
; The runtime IRQ reads the right stick into joy_x / joy_y (0..63, 32 = centre; an axis reads as centred until it
; has moved once: MAME leaves an untouched axis at 0).  Buttons: btn0 / btn1 (bit 7), read by io_rd_C061/2.
;
; (a) Monitor PREAD ($FB1E): X = paddle 0/1 -> Y = count 0..255.  Spec: NATIVE[0xFB1E] = 'nj_pread',
;     NATIVE_CONTRACTS: Native('nj_pread', use=X_, defs=Y_ | A_ | FLAGS).
nj_pread
	tstb				; B = 6502 X
	bne	njp1
	lda	joy_x
	bra	njp2
njp1	lda	joy_y
njp2	lsla				; 0..63 -> 0..252
	lsla
	sta	RY			; Y = count
	clr	RYW
	rts
;
; (b) A game's own count loop (LDA $C070 / loop: LDA $C064,X / BPL done / INY / BNE loop): replace the whole loop
;     with a native that produces the count, or better, the value the game derives from it (e.g. a game that
;     classifies counts into left/centre/right: one native computes the class directly from joy_x).  Exit through
;     `jmp E<addr>` (the instruction after the loop; list it in spec.NATIVE_EXITS) with the registers the loop
;     leaves.
