; dly.asm - hw_dly: wait hw_dc Apple cycles in CoCo time (6502to6809 kit, hardware idiom library).
; In: hw_dc = 24-bit Apple cycle count (big endian; < 2^21).  Burns hw_dc x 1.75 CoCo cycles (7/32 iterations of
; an 8-cycle loop per Apple cycle; 24-bit iteration count), the call included.  Preserves A, B, X, CC;
; hw_dc is destroyed.  Rendered once into hw_inst.asm when an idiom instance needs it (kit/hw/lib.py).
HW_OVH	equ	@OVH@			; this routine's fixed cost, in 8-cycle loop units
hw_dc	rmb	3
hw_n	rmb	3			; iterations (24 bits)
hw_q	rmb	3
hw_r	rmb	1
hw_dly	pshs	cc,d,x
	ldb	hw_dc+2
	andb	#31
	lda	#7
	mul				; D = (D24 & 31) * 7 <= 217
	lsrb
	lsrb
	lsrb
	lsrb
	lsrb
	stb	hw_r
	ldb	#5
hwd1	lsr	hw_dc			; q = D24 >> 5 (16 bits)
	ror	hw_dc+1
	ror	hw_dc+2
	decb
	bne	hwd1
	ldd	hw_dc+1
	std	hw_q+1
	std	hw_n+1
	clr	hw_q
	clr	hw_n
	ldb	#3
hwd3	lsl	hw_n+2			; n = q * 8 (24 bits)
	rol	hw_n+1
	rol	hw_n
	decb
	bne	hwd3
	ldd	hw_n+1			; n = q * 7 + r - HW_OVH
	subd	hw_q+1
	std	hw_n+1
	lda	hw_n
	sbca	#0
	sta	hw_n
	ldd	hw_n+1
	addb	hw_r
	adca	#0
	std	hw_n+1
	lda	hw_n
	adca	#0
	sta	hw_n
	ldd	hw_n+1
	subd	#HW_OVH
	std	hw_n+1
	lda	hw_n
	sbca	#0
	sta	hw_n
	bcs	hwd9			; below the fixed cost: return at once
	ldx	hw_n+1
	beq	hwd4
hwd2	leax	-1,x			; 8 cycles per pass
	bne	hwd2
hwd4	tst	hw_n			; then 65536 passes per unit of the high byte
	beq	hwd9
	dec	hw_n
hwd5	leax	-1,x
	bne	hwd5
	bra	hwd4
hwd9	puls	cc,d,x,pc
