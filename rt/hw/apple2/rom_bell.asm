; rom_bell: monitor BELL ($FF3A) / BELL1 ($FBDD): WAIT(64), then 192 speaker clicks with WAIT(12) between them
; (about 950 Hz for 0.1 s) in Apple time.  Out: Y = 0, A = 0, N=0 Z=1; B (6502 X) and C kept.
@SYM@	pshs	cc,b
	ldd	#@PRE@			; WAIT(64) + the LDY
	std	hw_dc+1
	clr	hw_dc
	jsr	hw_dly
	ldb	#192
@SYM@_lp	lda	$FF20			; click
	eora	#$FC
	sta	$FF20
	pshs	b
	ldd	#@PASS@			; WAIT(12) + LDA $C030 / DEY / BNE, minus this loop's own cost
	std	hw_dc+1
	clr	hw_dc
	jsr	hw_dly
	puls	b
	decb
	bne	@SYM@_lp
	clr	RY
	puls	cc,b
	pshs	cc
	clra
	puls	cc
	andcc	#$F3
	orcc	#$04
@EXIT@
