; paddle_count: a paddle-timer count loop ($C070 trigger, count while $C064+n bit 7 is set) -> the count the
; Apple timer would give for the CoCo stick position (table @SYM@_tab: kit.hw.lib.paddle_model per stick value
; 0..63).  Returns at once (the Apple loop takes up to ~3 ms).  Out: the count in the counter register, the
; registers and N/Z the loop leaves on its usual exit (or on its max exit); C kept.
@SYM@	pshs	cc,a,x
	ldx	#@SYM@_tab
	lda	@JOY@
	anda	#63
	lda	a,x
@STORE@
	cmpa	#@MAX@
	beq	@SYM@_mx
	puls	cc,a,x
@EXITREGS@
@EXIT@
@SYM@_mx	puls	cc,a,x
@EXITREGS_MAX@
@EXIT@
@SYM@_tab	fcb	@TAB@
