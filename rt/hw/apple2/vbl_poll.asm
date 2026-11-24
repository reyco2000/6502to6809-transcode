; vbl_poll: a RDVBL polling loop (L: LDA|BIT $C019 / BMI|BPL L) -> rt_vbl, which keeps the Apple frame rate.
; Out: the N/Z the loop leaves (exit_n), A as LDA leaves it (or kept, for BIT); C, B, RY kept.
@SYM@	pshs	cc
	jsr	rt_vbl
	puls	cc
@SETA@
@EXIT@
