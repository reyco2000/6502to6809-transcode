; speaker.asm - speaker sound (6502to6809 kit, Apple II source).
; The runtime's io_wr_C030 / io_rd_C030 handler toggles the 6-bit DAC between its extremes: a translated
; `STA $C030` already clicks.  That is enough for occasional clicks, but a tone loop is cycle-timed: its pitch
; is the number of 6502 cycles between clicks, and transcoded code runs at a different speed.  Replace each tone
; routine with a native that keeps the 6502 period structure in Apple time (1 Apple cycle ~ 1.75 CoCo cycles):
;
;   ns_tone: in <duration>, <period> (the game's zero page / registers)
;   loop:  click (lda $FF20 / eora #$FC / sta $FF20)
;          delay period * k CoCo cycles   (k from the 6502 loop: count its cycles per pass, x 1.75)
;          dec duration / bne loop
;
; Pattern used in a two-voice player: keep both counters, click when either expires, and poll the keyboard where
; the 6502 did (call the translated key routine through its E<addr> label).  Interrupts: the runtime IRQ (60 Hz
; keyboard/joystick) steals ~2% and makes a faint buzz; masking it during a note is allowed if the note is short.
; The joystick read shares the DAC: rt.asm restores the DAC level after reading the stick.
; Test: test_native compares memory/registers with the 6502 (timing is not compared); listen with MAME
; -wavwrite out.wav.
