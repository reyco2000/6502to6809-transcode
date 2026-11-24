; delay: counted delay loops (no memory writes, no I/O) in Apple time.
; Apple cycles = const + outer * (outer_pass + inner * inner_pass), counts of 0 meaning 256 (the matcher derives
; the coefficients from the 6502 loop).  hw_dly burns them x 1.75.  Out: the registers and N/Z the loop leaves.
@SYM@	pshs	cc,d
@LOAD_OUTER@
	sta	@SYM@_o
@LOAD_INNER@
	ldb	#@INNER_PASS@
	tsta
	beq	@SYM@_i0
	mul				; D = inner * inner_pass
	bra	@SYM@_i1
@SYM@_i0	tfr	b,a			; inner = 256
	clrb
@SYM@_i1	addd	#@OUTER_PASS@
	std	@SYM@_t			; T = outer_pass + inner * inner_pass
	lda	@SYM@_o
	beq	@SYM@_o0
	ldb	@SYM@_t+1
	mul				; outer * T (24 bits)
	std	hw_dc+1
	clr	hw_dc
	lda	@SYM@_o
	ldb	@SYM@_t
	mul
	addb	hw_dc+1
	stb	hw_dc+1
	adca	#0
	sta	hw_dc
	bra	@SYM@_c
@SYM@_o0	ldd	@SYM@_t			; outer = 256: T << 8
	sta	hw_dc
	stb	hw_dc+1
	clr	hw_dc+2
@SYM@_c	ldd	hw_dc+1			; plus const, minus this routine's own cost; not below 0
	subd	#@SUB@
	std	hw_dc+1
	lda	hw_dc
	sbca	#0
	sta	hw_dc
	bcc	@SYM@_d
	clr	hw_dc
	clr	hw_dc+1
	clr	hw_dc+2
@SYM@_d	jsr	hw_dly
	puls	cc,d
@EXITREGS@
@EXIT@
@SYM@_o	rmb	1
@SYM@_t	rmb	2
