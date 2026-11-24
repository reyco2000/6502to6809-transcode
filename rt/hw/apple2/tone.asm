; tone: a single-voice speaker loop (one $C030 access per pass, an inner counted delay, an outer duration count)
; in Apple time.  Apple cycles per pass = outer_pass + period * inner_pass (counts of 0 meaning 256).
; The CoCo pass is that x 1.75: a fixed part, k passes of an 8-cycle loop, and one more pass each time the
; fraction (x & 7) accumulates to 8, so the average period is exact (jitter: 8 CoCo cycles).
; Shortest pass: the fixed part, about 43 Apple cycles (kit.hw.lib.TONE_FIX / 1.75): shorter passes run long.
; The click is the runtime's: the DAC between its extremes.  Out: the registers and N/Z the loop leaves; C kept.
@SYM@	pshs	cc
	sta	@SYM@_ea
@LOAD_DURATION@
	sta	@SYM@_n+1		; passes (0 = 256)
	clr	@SYM@_n
	tsta
	bne	@SYM@_d1
	inc	@SYM@_n
@SYM@_d1	lda	@SYM@_ea
@LOAD_PERIOD@
	ldb	#@INNER_PASS@
	tsta
	beq	@SYM@_p0
	mul				; period * inner_pass
	bra	@SYM@_p1
@SYM@_p0	tfr	b,a			; period 0 = 256
	clrb
@SYM@_p1	addd	#@OUTER_PASS@		; Apple cycles per pass (< 9362)
	std	@SYM@_k
	lslb				; x 7 / 4 = CoCo cycles per pass
	rola
	lslb
	rola
	lslb
	rola
	subd	@SYM@_k
	lsra
	rorb
	lsra
	rorb
	subd	#@FIX@			; minus the fixed part of a pass
	bcc	@SYM@_p2
	ldd	#0
@SYM@_p2	stb	@SYM@_f
	lsra				; whole 8-cycle passes
	rorb
	lsra
	rorb
	lsra
	rorb
	std	@SYM@_k
	lda	@SYM@_f
	anda	#7
	sta	@SYM@_f			; fraction (eighths)
	clr	@SYM@_a
@SYM@_lp	lda	$FF20			; click
	eora	#$FC
	sta	$FF20
	ldx	@SYM@_k
	lda	@SYM@_a
	adda	@SYM@_f
	cmpa	#8
	blo	@SYM@_nx
	suba	#8
	leax	1,x			; one more 8-cycle pass
	bra	@SYM@_dl
@SYM@_nx	nop				; balance the two paths (13 cycles each)
	nop
	nop
	nop
	nop
@SYM@_dl	sta	@SYM@_a
	cmpx	#0
	beq	@SYM@_dz
@SYM@_dw	leax	-1,x
	bne	@SYM@_dw
@SYM@_dz	ldd	@SYM@_n
	subd	#1
	std	@SYM@_n
	bne	@SYM@_lp
	puls	cc
@EXITREGS@
@EXIT@
@SYM@_ea	rmb	1
@SYM@_n	rmb	2
@SYM@_k	rmb	2
@SYM@_f	rmb	1
@SYM@_a	rmb	1
