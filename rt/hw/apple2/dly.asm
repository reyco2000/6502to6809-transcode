; dly.asm - hw_dly: wait hw_dc Apple cycles in CoCo time (6502to6809 kit, hardware idiom library).
; In: hw_dc = 24-bit Apple cycle count (big endian; < 2^21).  Burns hw_dc x 1.75 CoCo cycles (7/32 iterations of
; an 8-cycle loop per Apple cycle), the call included.  Preserves A, B, X, CC; hw_dc is destroyed.
; Rendered once into hw_inst.asm when an idiom instance needs it (kit/hw/lib.py).
HW_OVH	equ	@OVH@			; this routine's fixed cost, in 8-cycle loop units
hw_dc	rmb	3
hw_q	rmb	2
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
hwd1	lsr	hw_dc			; D24 >> 5
	ror	hw_dc+1
	ror	hw_dc+2
	decb
	bne	hwd1
	ldd	hw_dc+1
	std	hw_q
	lslb				; q * 8
	rola
	lslb
	rola
	lslb
	rola
	subd	hw_q			; q * 7
	addb	hw_r
	adca	#0
	subd	#HW_OVH
	bls	hwd9
	tfr	d,x
hwd2	leax	-1,x			; 8 cycles per pass
	bne	hwd2
hwd9	puls	cc,d,x,pc
