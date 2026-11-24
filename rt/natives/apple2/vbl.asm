; vbl.asm - native vertical-blank wait (6502to6809 kit, Apple II source).
; Replaces a 6502 loop such as   L: LDA $C019 / BMI L   (IIe RDVBL: bit 7 = 0 during blanking) or the IIc
; variant, or a game's own frame-pacing loop.  rt_vbl first converts the shown page (the game may draw on it),
; then returns at once inside the vertical-blank window or waits for the next 60 Hz tick: the game keeps the
; Apple's frame rate.  Exit: set what the 6502 loop leaves (here: A = a RDVBL value with bit 7 clear, N=0 Z=1).
; Adjust the flags to the loop you replace (report.txt "must produce").
nt_vbl
	jsr	rt_vbl
	lda	#0
	rts
