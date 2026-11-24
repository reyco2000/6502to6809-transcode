; rom_pread: monitor PREAD ($FB1E): X = paddle 0-3 in, Y = 0..255 out from the CoCo stick (0..63), at once.
; Paddles 0/2 = joy_x, 1/3 = joy_y.  Out: Y, A = 0, N=0 Z=1; B (6502 X) and C kept.
@SYM@	pshs	cc,x
	ldx	#@SYM@_tab
	bitb	#1
	bne	@SYM@_y
	lda	joy_x
	bra	@SYM@_1
@SYM@_y	lda	joy_y
@SYM@_1	anda	#63
	lda	a,x
	sta	RY
	puls	cc,x
	pshs	cc
	clra
	puls	cc
	andcc	#$F3
	orcc	#$04
@EXIT@
@SYM@_tab	fcb	@TAB@
