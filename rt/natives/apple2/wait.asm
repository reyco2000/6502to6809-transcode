; wait.asm - monitor WAIT ($FCA8) in CoCo cycles (6502to6809 kit).  Spec: NATIVE[0xFCA8] = 'nt_wait' with
; NATIVE_CONTRACTS['nt_wait'] = Native('nt_wait', use=A_, defs=A_ | FLAGS)  (A = 0, C = 1 on return).
; Apple: (26 + 27A + 5A^2) / 2 cycles at 1.023 MHz.  The CoCo runs 1.79 MHz, so each Apple cycle is about 1.75
; CoCo cycles: the inner loop (SBC #1 / BNE = 5 Apple cycles) takes 9, the outer step (12) takes 21.
; Many games use WAIT for sound pitch or delays: keep it in Apple time, not "as fast as possible".
nt_wait
ntw2	pshs	a			; PHA
ntw3	nop				; SBC #1 / BNE: 5 Apple cycles -> 9
	nop
	deca
	bne	ntw3
	puls	a			; PLA / SBC #1 / BNE: 12 Apple cycles -> 21
	nop
	nop
	deca
	bne	ntw2
	orcc	#1
	rts
