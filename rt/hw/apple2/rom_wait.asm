; rom_wait: monitor WAIT ($FCA8) in Apple time.  In: A (0 = 256).  Out: A = 0, N=0 Z=1 C=1; B (6502 X), RY kept.
; Apple: (26 + 27A + 5A^2) / 2 cycles.  Short waits (A < @SHORT@) keep the monitor's loop structure: an inner pass
; of 9 CoCo cycles for 5 Apple (8.75) and an outer one of 17 for 12 (21): the two errors cancel to within ~1%.
; Longer waits (and A = 0 = 256) are computed and burnt by hw_dly.
@SYM@	cmpa	#@SHORT@
	bhs	@SYM@_c
	tsta
	beq	@SYM@_c
@SYM@_s2	pshs	a			; PHA
@SYM@_s3	nop				; SBC #1 / BNE
	nop
	deca
	bne	@SYM@_s3
	puls	a			; PLA / SBC #1 / BNE
	deca
	bne	@SYM@_s2
	orcc	#1
@EXIT@
@SYM@_c	pshs	b
	sta	@SYM@_k
	tfr	a,b
	mul				; D = A^2 (A = 0: 0, fixed below)
	std	hw_dc+1
	clr	hw_dc
	tst	@SYM@_k
	bne	@SYM@_1
	inc	hw_dc			; A = 0 means 256: 256^2 = $010000
@SYM@_1	ldd	hw_dc+1			; 5 * A^2 = 4 * A^2 + A^2 (24 bits)
	std	@SYM@_t+1
	lda	hw_dc
	sta	@SYM@_t
	ldb	#2
@SYM@_2	lsl	hw_dc+2
	rol	hw_dc+1
	rol	hw_dc
	decb
	bne	@SYM@_2
	ldd	hw_dc+1
	addd	@SYM@_t+1
	std	hw_dc+1
	lda	hw_dc
	adca	@SYM@_t
	sta	hw_dc
	lda	#27			; + 27 * A
	ldb	@SYM@_k
	mul
	tst	@SYM@_k
	bne	@SYM@_3
	ldd	#27*256
@SYM@_3	addd	#26			; + 26
	addd	hw_dc+1
	std	hw_dc+1
	lda	hw_dc
	adca	#0
	sta	hw_dc
	lsr	hw_dc			; / 2
	ror	hw_dc+1
	ror	hw_dc+2
	ldd	hw_dc+1			; minus this routine's own cost (Apple cycles), not below 0
	subd	#@OVA@
	std	hw_dc+1
	lda	hw_dc
	sbca	#0
	sta	hw_dc
	bcc	@SYM@_4
	clr	hw_dc
	clr	hw_dc+1
	clr	hw_dc+2
@SYM@_4	jsr	hw_dly
	puls	b
	lda	#0
	orcc	#1
@EXIT@
@SYM@_k	rmb	1
@SYM@_t	rmb	3
