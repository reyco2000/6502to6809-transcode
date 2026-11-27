; 6502to6809 resident runtime (CoCo 3, 128K: physical blocks $30-$3F).  Game-independent: it runs a statically
; transcoded Apple II hi-res game.  Logical $E000-$FDFF in task 0 and task 1 (block BLK_RT).  $FE00-$FEFF is
; the GIME constant page (vectors, and the vconv variables at $FE00).
;
; The build supplies these equates (layout.inc):
;   BLK_GIME1    first of the 4 blocks of the single GIME 320x192x16 buffer (30720 bytes)
;   BLK_VID      its last block, whose tail ($1800-$1FFF) holds vconv (task 1 slot 6, $D800)
;   BLK_SH1      the converter's log block (= BLK_GIME1+4; vid.asm); also the disk track buffer while loading
;   BLK_TA/TB    conversion tables
;   BLK_ARAM     Apple RAM $0000-$BFFF as 6 consecutive blocks
;                (+1/+2 = hi-res pages 1/2: the authoritative bitmaps)
;   BLK_EC1/2    transcoded engine code, logical $2000-$5FFF outside hi-res routines
;   BLK_RT       this block
;   DS_TBLK, DS_TOTAL, DS_BARLEN, DS_BARATTR  boot title screen and progress bar
;   ds_cold      (data) the cold-start file: its runs, then the runs only the boot loads
;   RT_START_GAME  (label) game entry after init
;
; Task 0 map while the game runs:
;   $0000 Apple RAM, $2000/$4000 engine code (hi-res bitmaps inside hr_on/hr_off),
;   $6000-$BFFF Apple RAM, $C000 code block B again (stage code at OVORG, nb_sh), $E000 runtime.
; Register convention of the transcoded code (see kit/xc65.py):
;   A = 6502 A, B = 6502 X, RY = 6502 Y, S = 6502 stack, DP = 0.
;   I/O handlers pass the value in IOV and preserve A, B and CC.

VR_MAX	equ	32			; dirty rectangles per page before it counts as fully dirty (max 32: offsets b,u)
VVPAGE	equ	$FE			; vconv direct page (constant page)

	org	$E000
rt_go	jmp	rt_boot

; ---------------------------------------------------------------- shared variables (fixed addresses)
RYW	fcb	0			; 0:Y word for (zp),Y helpers
RY	fcb	0			; 6502 Y
IOV	fcb	0			; I/O handler value
cur_s6	fcb	0			; block in task 0 slot 6
rt_tick	fdb	0			; 60 Hz ticks
kb_kbd	fcb	0			; $C000 latch (bit 7 = strobe)
kb_any	fcb	0			; $80 while a key is held
kb_last	fcb	$FF
kb_ctl	fcb	0
kb_ign	fcb	0
kb_new	fcb	0
kb_col	fcb	0
kb_stb	fcb	0
btn0	fcb	0			; $C061 / $C062: bit 7
btn1	fcb	0
joy_x	fcb	32			; CoCo right joystick, 0..63
joy_y	fcb	32
joy_v	fcb	0
v_dpage	fcb	0			; Apple page selected by $C054/$C055 (0/1)
v_shown	fcb	$FF			; page currently converted and shown
trap_at	fdb	0
vbl_over	fcb	0			; 1 once the vertical-blank window of this frame has passed
v_flips	fdb	0			; $C054/$C055 accesses (statistics)
joy_seen	fcb	0			; bit 0 / bit 1: X / Y has read non-zero once (MAME leaves an untouched axis at 0)

; ---------------------------------------------------------------- startup
; rt_boot: from the fast loader (<loader>.BIN), which has read this block from the disk and left the motor on.
; A = DSKREG value, B = track under the head, X/Y = progress bar cell / accumulator.  Streams the cold image,
; the tables, the engine and the converter from the disk (dstream.asm), then starts like a reboot.
rt_boot	orcc	#$50
	lds	#rt_stack
	sta	ds_dreg
	stb	ds_ctrk
	stx	ds_bar
	sty	ds_acc
	bsr	rt_hw
	inc	ds_mot
	lda	#DS_TBLK		; title screen (progress bar) at $2000 in task 1
	sta	$FFA9
	ldx	#ds_cold
	jsr	ds_open
	jsr	ds_runs			; cold Apple RAM and code
	jsr	ds_runs			; then what only the boot loads
	jsr	ds_stop
	clr	ds_bar
	clr	ds_bar+1
	bra	rt_st1
; rt_hw: CoCo 3 mode, MMU on, constant page $FExx, no ROM, task 0, 1.79 MHz, this block in slot 7 of both tasks,
; NMI (disk INTRQ) = RTI.
rt_hw	lda	#$4C
	sta	$FF90
	clr	$FF91
	sta	$FFD9
	sta	$FFDF
	lda	#BLK_RT
	sta	$FFA7
	sta	$FFAF
	lda	#$3B			; RTI
	sta	$FEFD
	rts

; rt_start: (re)start the game; Apple RAM and the code are in place.
rt_start
	orcc	#$50
	lds	#rt_stack
	bsr	rt_hw
rt_st1	clr	vp_on
	; clear the GIME buffer (not the converter in its last block's tail) and the log block through slot 2
	lda	#BLK_GIME1
rs_clr	sta	$FFA2
	ldx	#$4000
	ldu	#0
	ldy	#$1000
	cmpa	#BLK_VID
	bne	rs_c1
	ldy	#$0C00
rs_c1	stu	,x++
	leay	-1,y
	bne	rs_c1
	inca
	cmpa	#BLK_SH1+1
	bne	rs_clr
	lda	#$FF			; both pages fully dirty, the screen shows no page (full conversion first)
	sta	vr_n1
	sta	vr_n2
	sta	v_ref
	ldx	#vr_all
	stx	vr_p1
	stx	vr_p2
	; task 0 game map
	lda	#BLK_ARAM
	sta	$FFA0
	lda	#BLK_EC1
	sta	$FFA1
	lda	#BLK_EC2
	sta	$FFA2
	lda	#BLK_ARAM+3
	sta	$FFA3
	inca
	sta	$FFA4
	inca
	sta	$FFA5
	lda	#BLK_EC2		; slot 6 shows code block B (stage code + tables, visible under hr_on)
	sta	cur_s6
	sta	$FFA6
	lda	#BLK_VID
	sta	$FFAE			; task 1 slot 6
	; palette, video mode (graphics 320x192x16, 160 bytes per row), page 1 buffer
	ldx	#rt_pal
	ldu	#$FFB0
rs_p	lda	,x+
	sta	,u+
	cmpu	#$FFC0
	bne	rs_p
	lda	#$80
	sta	$FF98
	lda	#$1E
	sta	$FF99
	clr	$FF9A
	clr	$FF9C
	lda	#BLK_GIME1*4
	sta	$FF9D
	clr	$FF9E
	clr	$FF9F
	; PIAs: keyboard columns released, VSYNC IRQ on (PIA0 CB1), sound on (PIA1 CB2 high), DAC low, mux = DAC
	lda	#$FF
	sta	$FF02
	lda	$FF03
	ora	#$01
	sta	$FF03
	lda	$FF01
	anda	#$FE			; HSYNC IRQ off
	sta	$FF01
	lda	$FF20
	anda	#$03
	sta	$FF20
	lda	$FF03			; analog mux 00 = DAC (also the right joystick X)
	anda	#$F7
	sta	$FF03
	lda	$FF01
	anda	#$F7
	sta	$FF01
	lda	$FF23
	ora	#$3C
	sta	$FF23
	lda	$FF02
	lda	#$20			; GIME timer flag latched in $FF92 (no GIME interrupt: FF90 bit 5 = 0)
	sta	$FF92
	; IRQ vector
	lda	#$7E			; JMP
	sta	$FEF7
	ldx	#rt_irq
	stx	$FEF8
	jmp	RT_START_GAME		; game code: sets the stack and the bank, enables interrupts

; RGB palette (xxRGBrgb): black purple green blue orange white, rest black
rt_pal	fcb	$00,$2F,$12,$1B,$26,$3F,$00,$00
	fcb	$00,$00,$00,$00,$00,$00,$00,$00

; ---------------------------------------------------------------- bank switch
; rt_bank: A = block for task 0 slot 6 (cur_s6 first, so an IRQ always sees the right block)
rt_bank	sta	cur_s6
	sta	$FFA6
	rts

; ---------------------------------------------------------------- hi-res mapping (preserve everything)
hr_on	pshs	cc,a
	lda	#BLK_ARAM+1
	sta	$FFA1
	inca
	sta	$FFA2
	puls	cc,a,pc
hr_off	pshs	cc,a
	lda	#BLK_EC1
	sta	$FFA1
	lda	#BLK_EC2
	sta	$FFA2
	puls	cc,a,pc

	ifdef	HRACC
; hr_rd / hr_wr: translated code's hi-res accesses (spec HIRES_ACCESS; kit/xc65.py).  X = Apple address (any
; RAM below $C000 works); the byte goes through IOV.  hr_wr keeps, per hi-res page, the bounding box of the bytes
; it wrote (line and column from the address; the 8 unshown bytes of each 128 are skipped), and hr_flush reports
; it with vm_rect at the next flip / VBL.  Preserve everything but IOV.
hr_rd	pshs	cc,a
	lda	#BLK_ARAM+1
	sta	$FFA1
	inca
	sta	$FFA2
	lda	,x
	sta	IOV
	lda	#BLK_EC1
	sta	$FFA1
	lda	#BLK_EC2
	sta	$FFA2
	puls	cc,a,pc
hr_wr	pshs	cc,a
	cmpx	#$C000			; ROM / slot space: the Apple drops the write (here it would hit the runtime)
	bhs	hrw_x
	lda	#BLK_ARAM+1
	sta	$FFA1
	inca
	sta	$FFA2
	lda	IOV
	sta	,x
	lda	#BLK_EC1
	sta	$FFA1
	lda	#BLK_EC2
	sta	$FFA2
	cmpx	#$2000
	blo	hrw_x
	cmpx	#$6000
	bhs	hrw_x
	pshs	b,u
	ldu	#hr_bx1
	cmpx	#$4000
	bhs	hrw_p2
	sta	hr_d1			; any non-zero value: A = BLK_EC2
	bra	hrw_1
hrw_p2	sta	hr_d2
	leau	4,u
hrw_1	tfr	x,d			; offset in the page = (y AND 7)*$400 + (y/8 AND 7)*$80 + (y/64)*40 + column
	anda	#$1F
	pshs	d
	andb	#$7F
	clra				; A = 64 * third of the screen, B = column
	cmpb	#40
	blo	hrw_g
	subb	#40
	lda	#64
	cmpb	#40
	blo	hrw_g
	subb	#40
	lda	#128
	cmpb	#40
	bhs	hrw_h			; $78-$7F of each 128: not shown
hrw_g	pshs	d
	ldd	2,s
	lslb
	rola				; A = offset bits 12-7
	tfr	a,b
	andb	#7
	lslb
	lslb
	lslb				; (y/8 AND 7) * 8
	lsra
	lsra
	lsra				; y AND 7
	pshs	b
	adda	,s+
	adda	,s			; A = line
	cmpa	,u
	bhs	hrw_2
	sta	,u
hrw_2	cmpa	1,u
	bls	hrw_3
	sta	1,u
hrw_3	ldb	1,s
	cmpb	2,u
	bhs	hrw_4
	stb	2,u
hrw_4	cmpb	3,u
	bls	hrw_5
	stb	3,u
hrw_5	leas	2,s
hrw_h	leas	2,s
	puls	b,u
hrw_x	puls	cc,a,pc
; hr_flush: report the boxes hr_wr collected (vm_rect), then empty them.  Preserves all.
hr_flush
	pshs	cc,d,u
	ldu	#hr_bx1
	lda	hr_d1
	beq	hrf_2
	clr	hr_d1
	lda	#$20
	bsr	hrf_r
hrf_2	ldu	#hr_bx2
	lda	hr_d2
	beq	hrf_x
	clr	hr_d2
	lda	#$40
	bsr	hrf_r
hrf_x	puls	cc,d,u,pc
hrf_r	ldb	,u
	cmpb	1,u
	bhi	hrf_e			; only unshown bytes
	ldb	,u
	stb	vr_y0
	ldb	1,u
	stb	vr_y1
	ldb	2,u
	stb	vr_c0
	ldb	3,u
	stb	vr_c1
	jsr	vm_rect
hrf_e	ldd	#$FF00
	std	,u
	std	2,u
	rts
hr_d1	fcb	0			; page written since the last flush (hr_d2 must follow: probes read hr_d1 + page)
hr_d2	fcb	0
hr_bx1	fcb	$FF,0,$FF,0		; lines y0-y1, columns c0-c1
hr_bx2	fcb	$FF,0,$FF,0
	endc

; ---------------------------------------------------------------- dirty rectangles
; Per hi-res page: a list of up to VR_MAX rectangles (line0, line1, col0, col1, inclusive), or vr_n = $FF for
; the whole page.  vconv converts (and empties) the list of the page it shows.
vr_n1	fcb	0
vr_n2	fcb	0
vr_p1	fdb	vr_none			; the entry that last absorbed or took a rectangle of page 1 (vr_none: list empty,
vr_p2	fdb	vr_none			; vr_all: whole page)
vr_none	fcb	$FF,0,$FF,0		; contains no rectangle
vr_all	fcb	0,$FF,0,$FF		; contains every rectangle
vr_l1	rmb	VR_MAX*4
vr_l2	rmb	VR_MAX*4
vr_rflag rmb	190			; per destination line: 0 = the destination already holds the source's line
vr_y0	fcb	0			; vm_rect arguments
vr_y1	fcb	0
vr_c0	fcb	0
vr_c1	fcb	0

; vm_rect: A = page high byte ($20-$3F page 1, $40-$5F page 2), rectangle in vr_y0/vr_y1/vr_c0/vr_c1.
; Preserves all but CC.  Runs with interrupts off (the IRQ empties the lists).
vm_rect	pshs	cc,d,x,y,u
	ldx	#vr_n1
	ldy	vr_p1
	ldu	#vr_l1
	cmpa	#$40
	blo	vmr1
	cmpa	#$60
	bhs	vmr_x
	leax	1,x
	ldy	vr_p2
	leau	VR_MAX*4,u
vmr1	ldd	vr_y0			; fast path: inside the entry that took the last one (the sprites of a frame are
	cmpa	,y			; drawn inside the area erased just before; vr_all: the whole page is dirty)
	blo	vmr_sl
	cmpb	1,y
	bhi	vmr_sl
	ldd	vr_c0
	cmpa	2,y
	blo	vmr_sl
	cmpb	3,y
	bls	vmr_x
vmr_sl	ldb	,x
	cmpb	#VR_MAX
	bhs	vmr_full		; (or $FF: whole page, but then vr_all took it above)
vmr_sl0	pshs	b			; merge with / inside one already listed (a sprite inside the erased area)?
	tstb				; newest first: the match is nearly always one of the last added
	beq	vmr_in
	lslb
	lslb
	leay	b,u
	leay	-4,y
vmr_ch	ldd	vr_c0			; same columns and lines that overlap or touch (a column stripe drawn a few lines
	cmpd	2,y			; at a time): lengthen it.  Without this a stripe overflowed the list every frame
	bne	vmr_h			; and the whole page was converted.
	lda	1,y			; (a one-line gap is bridged: lines drawn every other line.  Scanning the
	adda	#2			; gap costs little, the conversion skips unchanged pairs)
	cmpa	vr_y0
	blo	vmr_h
	lda	vr_y1
	adda	#2
	cmpa	,y
	blo	vmr_h
	lda	vr_y0
	cmpa	,y
	bhs	vmr_v1
	sta	,y
vmr_v1	lda	vr_y1
	cmpa	1,y
	bls	vmr_m2
	sta	1,y
	bra	vmr_m2
vmr_h	ldd	vr_y0			; same lines and columns that overlap or touch (a run of glyphs): widen it
	cmpd	,y
	bne	vmr_ct
	lda	3,y
	inca
	cmpa	vr_c0
	blo	vmr_ct
	lda	vr_c1
	inca
	cmpa	2,y
	blo	vmr_ct
	lda	vr_c0
	cmpa	2,y
	bhs	vmr_m1
	sta	2,y
vmr_m1	lda	vr_c1
	cmpa	3,y
	bls	vmr_m2
	sta	3,y
vmr_m2	leas	1,s
vmr_hit	cmpx	#vr_n1			; remember the entry (Y)
	bne	vmh_2
	sty	vr_p1
	bra	vmr_x
vmh_2	sty	vr_p2
	bra	vmr_x
vmr_ct	lda	vr_y0
	cmpa	,y
	blo	vmr_no
	lda	vr_y1
	cmpa	1,y
	bhi	vmr_no
	lda	vr_c0
	cmpa	2,y
	blo	vmr_no
	lda	vr_c1
	cmpa	3,y
	bhi	vmr_no
	leas	1,s			; yes: nothing to add
	bra	vmr_hit
vmr_no	leay	-4,y
	dec	,s
	bne	vmr_ch
vmr_in	leas	1,s
	ldb	,x			; append at the end of the list
	lslb
	lslb
	leay	b,u
vmr_add	ldd	vr_y0
	std	,y
	ldd	vr_c0
	std	2,y
	inc	,x
	bra	vmr_hit
vmr_x	puls	cc,d,x,y,u,pc
vmr_full
	lda	#$FF
	sta	,x
	ldy	#vr_all
	bra	vmr_hit
; vr_clr: A = page 0/1: its list is empty (consumed).  Preserves all but CC.
vr_clr	pshs	d,x
	ldx	#vr_n1
	clr	a,x
	lsla
	ldx	#vr_p1
	leax	a,x
	ldd	#vr_none
	std	,x
	puls	d,x,pc
; vr_merge: A = count (< $FF), U = list: entries whose lines and columns both overlap become their bounding box,
; until none overlap (the converter has no shadow: a pair in two rectangles would be converted twice; scanning
; the few extra pairs of a bounding box costs little).  -> A = new count.  Clobbers B, X, Y; preserves U.
vr_merge
	pshs	u
	sta	vrm_n
vrm_rs	ldx	,s
	lda	vrm_n
	sta	vrm_i
vrm_i1	dec	vrm_i
	beq	vrm_x			; (also for 0 or 1 entries: vrm_i wraps from 0 only if n = 0, see below)
	leay	4,x
	lda	vrm_i
	sta	vrm_j
vrm_j1	lda	,x
	cmpa	1,y
	bhi	vrm_nj
	lda	,y
	cmpa	1,x
	bhi	vrm_nj
	lda	2,x
	cmpa	3,y
	bhi	vrm_nj
	lda	2,y
	cmpa	3,x
	bhi	vrm_nj
	lda	,y			; X = bounding box of X and Y
	cmpa	,x
	bhs	vrm_1
	sta	,x
vrm_1	lda	1,y
	cmpa	1,x
	bls	vrm_2
	sta	1,x
vrm_2	lda	2,y
	cmpa	2,x
	bhs	vrm_3
	sta	2,x
vrm_3	lda	3,y
	cmpa	3,x
	bls	vrm_4
	sta	3,x
vrm_4	ldb	vrm_n			; Y = the last entry, one less
	decb
	stb	vrm_n
	lslb
	lslb
	ldu	,s
	leau	b,u
	ldd	,u
	std	,y
	ldd	2,u
	std	2,y
	bra	vrm_rs			; again: X grew
vrm_nj	leay	4,y
	dec	vrm_j
	bne	vrm_j1
	leax	4,x
	bra	vrm_i1
vrm_x	lda	vrm_n
	puls	u,pc
vrm_n	fcb	0
vrm_i	fcb	0
vrm_j	fcb	0

; vr_sort: A = count (< $FF), U = list: sort the entries by first line (insertion).  Preserves A, U.
vr_sort	cmpa	#2
	blo	vso_r
	pshs	a,u
	ldb	#1
vso_i	stb	vrs_i
	lslb
	lslb
	leax	b,u			; X = entry i
	ldd	,x
	std	vrs_k
	ldd	2,x
	std	vrs_k+2
vso_j	cmpx	1,s
	bls	vso_p
	lda	-4,x
	cmpa	vrs_k
	bls	vso_p
	ldd	-4,x
	std	,x
	ldd	-2,x
	std	2,x
	leax	-4,x
	bra	vso_j
vso_p	ldd	vrs_k
	std	,x
	ldd	vrs_k+2
	std	2,x
	ldb	vrs_i
	incb
	cmpb	,s
	blo	vso_i
	puls	a,u
vso_r	rts
vrs_i	fcb	0
vrs_k	rmb	4

; vm_lines: A = page high byte.  Every line X (0..189) with vr_rflag[X] <> 0 changed in full: add runs of such
; lines as rectangles.  Preserves all.
vm_lines
	pshs	cc,d,y
	ldy	#vr_rflag		; Y walks the flags (lines go past 127: no b,y)
	clrb
vml_l	tst	,y+			; line B
	bne	vml_run
vml_n	incb
	cmpb	#190
	blo	vml_l
	puls	cc,d,y,pc
vml_run	stb	vr_y0
vml_r1	incb
	cmpb	#190
	bhs	vml_e
	tst	,y+
	bne	vml_r1
vml_e	pshs	b			; B = first line after the run (already looked at)
	decb
	stb	vr_y1
	clr	vr_c0
	ldb	#39
	stb	vr_c1
	jsr	vm_rect
	puls	b
	cmpb	#190
	blo	vml_n
	puls	cc,d,y,pc

; vm_page: A = page high byte: the whole page.  Preserves all.
vm_page	pshs	cc,b,x
	ldx	#vr_all
	ldb	#$FF
	cmpa	#$40
	bhs	vmp1
	stb	vr_n1
	stx	vr_p1
	puls	cc,b,x,pc
vmp1	cmpa	#$60
	bhs	vmp2
	stb	vr_n2
	stx	vr_p2
vmp2	puls	cc,b,x,pc

; ---------------------------------------------------------------- xbitb: 6502 BIT flags (transcoder helper)
; In: A, B = operand.  CC: N = operand bit 7, Z = (A AND operand) = 0, C kept.  Preserves A, B.
xbitb	pshs	b,cc
	ldb	,s
	andb	#$F1
	stb	,s
	ldb	1,s
	bpl	xb1
	ldb	,s
	orb	#$08
	stb	,s
xb1	ldb	1,s
	pshs	a
	andb	,s+
	bne	xb2
	ldb	,s
	orb	#$04
	stb	,s
xb2	puls	cc,b,pc

; ---------------------------------------------------------------- traps
; jsr x_trap / fdb 6502 address: code the transcoder could not reach in this image.  Red border, halt.
x_trap	orcc	#$50
	puls	x
	ldd	,x
	std	trap_at
	lda	#$24
	sta	$FF9A
xt_h	bra	xt_h

; ---------------------------------------------------------------- Apple I/O handlers (value in IOV)
io_rd_C000
	pshs	cc,a
	lda	kb_kbd
	sta	IOV
	puls	cc,a,pc
io_rd_C010
	pshs	cc,a
	lda	kb_kbd
	anda	#$7F
	sta	kb_kbd
	ora	kb_any
	sta	IOV
	puls	cc,a,pc
io_wr_C010
	pshs	cc,a
	lda	kb_kbd
	anda	#$7F
	sta	kb_kbd
	puls	cc,a,pc
io_rd_C030
io_wr_C030
	pshs	cc,a
	lda	$FF20			; speaker: DAC between its extremes
	eora	#$FC
	sta	$FF20
	clr	IOV
	puls	cc,a,pc
io_rd_C054
io_wr_C054
	pshs	cc,d
	ldd	v_flips
	addd	#1
	std	v_flips
	clr	v_dpage
	clr	IOV
	jsr	v_flip
	puls	cc,d,pc
io_rd_C055
io_wr_C055
	pshs	cc,d
	ldd	v_flips
	addd	#1
	std	v_flips
	lda	#1
	sta	v_dpage
	clr	IOV
	jsr	v_flip
	puls	cc,d,pc
io_rd_C061
	pshs	cc,a
	lda	btn0
	sta	IOV
	puls	cc,a,pc
io_rd_C062
	pshs	cc,a
	lda	btn1
	sta	IOV
	puls	cc,a,pc
io_rdx_C061				; B = 6502 X: $C061 + X
	pshs	cc,a
	clra
	tstb
	bne	irx1
	lda	btn0
	bra	irx2
irx1	cmpb	#1
	bne	irx2
	lda	btn1
irx2	sta	IOV
	puls	cc,a,pc

; ---------------------------------------------------------------- 60 Hz IRQ (PIA0 CB1 = VSYNC)
; Runs on its own stack: the interrupted S may be the 6502 stack page.  Does not touch task 0 slots or DP.
rt_irq	lda	$FF02			; acknowledge
	lda	$FF92			; vertical-blank window: 70 lines of 63.7 us from now
	clr	vbl_over
	ldd	#70
	std	$FF94
	tfr	s,x
	lds	#rt_istk
	pshs	x
	ldd	rt_tick
	addd	#1
	std	rt_tick
	jsr	kb_scan
	jsr	kb_key
	ldb	rt_tick+1
	andb	#1
	bne	ri_nj
	jsr	joy_read		; every 2nd tick
ri_nj
ri_x	puls	x
	tfr	x,s
	rti

; v_flip: the game selected page v_dpage.  If it is not the one shown: convert it, show it.  Preserves all.
v_flip	pshs	cc,a
	lda	v_dpage
	cmpa	v_shown
	beq	vf_x
	sta	v_shown
	ifdef	HRACC
	jsr	hr_flush
	endc
	jsr	vconv
	jsr	vshow
vf_x	puls	cc,a,pc

; rt_vbl: the game's VBL wait: return at once inside the vertical-blank window (GIME timer started by the IRQ),
; else wait for the next 60 Hz tick.  First bring the shown page up to date: the game
; may draw on the displayed page.  Preserves all but CC.
rt_vbl	pshs	a
	lda	v_shown
	bmi	rv1
	ifdef	HRACC
	jsr	hr_flush
	endc
	jsr	vconv
rv1	lda	$FF92			; Apple RDVBL: return at once while still in vertical blank
	anda	#$20
	ora	vbl_over
	sta	vbl_over
	bne	rv_w
	puls	a,pc
rv_w	lda	rt_tick+1
rv2	cmpa	rt_tick+1
	beq	rv2
	puls	a,pc

; v_redraw: show page v_dpage again from scratch (after a stage load).  Preserves all.
v_redraw
	pshs	cc,a
	lda	#$FF
	sta	v_shown
	jsr	v_flip
	puls	cc,a,pc

; v_inval: a stage was loaded (Apple RAM replaced; the log block was the disk track buffer).  The screen keeps the
; last frame, which is no page now: both pages fully dirty and the next conversion is a full one.  Preserves all.
v_inval	pshs	cc,a
	clr	vp_on
	lda	#$FF
	sta	v_ref
	lda	#$20
	jsr	vm_page
	lda	#$40
	jsr	vm_page
	puls	cc,a,pc

; vshow: show the (single) GIME buffer.  It holds conv(v_ref), the page converted last.
vshow	pshs	a
	lda	#BLK_GIME1*4
	sta	$FF9D
	clr	$FF9E
	puls	a,pc

; vconv: A = page P.  The converter lives in BLK_VID (task 1 slot 6): switch to task 1 here and come back.
; Interrupts stay on: the IRQ runs on its own stack in the runtime block and touches neither the MMU nor DP.
; Single display buffer, no shadow: the screen shows conv(L), L = v_ref, the page converted last (its own list
; R_L holds what was drawn on it since).  Invariant: page X equals L outside R_X, i.e. where the screen may be
; out of date for X.  Converting P against L (vid_conv B = L) converts the pairs where P <> L; the bounding boxes
; of those changes go into R_L (the screen changed there).  Then L = P.
; A full conversion (B = $FF: every pair of R_P, written straight to the screen) is needed when the old content
; is unknown: L unknown (start, stage load), P = L (drawn on while shown), and after a pending scroll.
; Before converting P against L, what was drawn on L while shown (R_L) is converted in full.
; A pending scroll (vp_on, vscroll) is applied first when its page is converted; converting the other page with
; something to do would change the screen, so the pending scroll is dropped and its page becomes fully dirty.
vconv	pshs	cc,d,dp,x,y,u
	sta	vcv_pg
	clr	vcv_sc
	tst	vp_on
	beq	vcv_go
	cmpa	vp_dst
	bne	vcv_ot
	clr	vp_on			; this page: scroll the screen first
	inc	vcv_sc
	bra	vcv_go
vcv_ot	ldx	#vr_n1			; other page: anything to convert?
	tst	a,x
	beq	vcv_go
	bsr	vp_drop
vcv_go	sts	vc_sv			; task 1 maps Apple RAM at $0000: use a runtime stack
	lds	#rt_vstk
	ldb	#1
	stb	$FF91
	tst	vcv_sc
	beq	vcv_n
	lda	vcv_pg
	jsr	vid_scroll		; in BLK_VID
	bra	vcv_f			; the rest of R_P (lines 190-191, later drawing): full
vcv_n	ldb	v_ref
	bmi	vcv_c			; unknown: full
	cmpb	vcv_pg
	beq	vcv_f			; P = L: full
	ldx	#vr_n1			; drawn on L while shown: that first, in full ...
	tst	b,x
	beq	vcv_c
	bsr	vr_ovl			; ... unless none of it touches P's list: outside R_P the screen already shows
	beq	vcv_c			; P, and L keeps its list (L differs from the screen there)
	tfr	b,a
	ldb	#$FF
	jsr	vid_conv
	ldb	v_ref
	bra	vcv_c
vcv_f	ldb	#$FF
vcv_c	lda	vcv_pg
	jsr	vid_conv		; in BLK_VID
	lda	vcv_pg
	sta	v_ref
	clr	$FF91
	lds	vc_sv
	puls	cc,d,dp,x,y,u,pc
; vr_ovl: B = page L (0/1).  Z = 1 when no rectangle of L's list overlaps one of the other page's list (lines
; overlap, columns within 3: the converter redoes a changed pair's neighbours).  A whole-page list overlaps.
; Preserves all but CC.
vr_ovl	pshs	d,x,y,u
	ldx	#vr_n1
	ldu	#vr_l1
	ldy	#vr_l2
	tstb
	beq	vro_1
	exg	u,y
vro_1	lda	b,x			; A = L's count, B = P's count
	eorb	#1
	ldb	b,x
	cmpa	#$FF
	beq	vro_y
	cmpb	#$FF
	beq	vro_y
	tsta
	beq	vro_n
	tstb
	beq	vro_n
	pshs	d,y
vro_l	ldy	2,s
	ldb	1,s
vro_p	lda	,y			; P.y0 <= L.y1, P.y1 >= L.y0
	cmpa	1,u
	bhi	vro_nx
	lda	1,y
	cmpa	,u
	blo	vro_nx
	lda	3,u			; P.c0 <= L.c1 + 3, P.c1 + 3 >= L.c0
	adda	#3
	cmpa	2,y
	blo	vro_nx
	lda	3,y
	adda	#3
	cmpa	2,u
	blo	vro_nx
	leas	4,s
vro_y	lda	#1			; Z = 0
	puls	d,x,y,u,pc
vro_nx	leay	4,y
	decb
	bne	vro_p
	leau	4,u
	dec	,s
	bne	vro_l
	leas	4,s
vro_n	clra				; Z = 1
	puls	d,x,y,u,pc
vc_sv	fdb	0
vcv_pg	fcb	0
vcv_sc	fcb	0
vp_on	fcb	0			; 1: page vp_dst = S scrolled up 2 lines (lines 0..189), not yet applied
vp_dst	fcb	0
v_ref	fcb	$FF			; page converted last: the screen shows conv(v_ref) ($FF: none)
; vp_drop: forget the pending scroll; its page is fully dirty.  Preserves all but CC.
vp_drop	pshs	a
	clr	vp_on
	lda	vp_dst
	bsr	vp_hi
	jsr	vm_page
	puls	a,pc
; vp_hi: A = page 0/1 -> page high byte $20/$40
vp_hi	lsla
	lsla
	lsla
	lsla
	lsla
	adda	#$20
	rts

; vscroll: A = destination page, B = source page (0/1): the Apple side (nh_scroll) made destination line X =
; source line X+2 for X = 0..189, and flagged in vr_rflag the lines it changed.
; If the source is the shown page, S is brought up to date with it, and the destination is S scrolled: the scroll
; is applied to S and the screen when the destination is converted (vid_scroll), and only lines 190-191 (not
; scrolled) are dirty.  Otherwise the changed lines are dirty.  Preserves all but CC.
vscroll	pshs	d,x
	tst	vp_on			; a pending scroll of the destination is overwritten
	beq	vsc_1
	cmpa	vp_dst
	bne	vsc_1
	jsr	vp_drop
vsc_1	cmpb	v_shown
	bne	vsc_fb
	pshs	a
	tfr	b,a
	jsr	vconv			; S = source (drops any pending scroll of another page that is in the way)
	puls	a
	tst	vp_on
	beq	vsc_2
	jsr	vp_drop			; (a pending scroll of a third case: not kept)
vsc_2	sta	vp_dst
	jsr	vr_clr			; destination = S scrolled, except lines 190-191
	pshs	a
	bsr	vp_hi
	ldx	#$BEBF
	stx	vr_y0
	ldx	#$0027
	stx	vr_c0
	jsr	vm_rect
	puls	a
	inc	vp_on
	puls	d,x,pc
vsc_fb	bsr	vp_hi
	jsr	vm_lines
	puls	d,x,pc

; ---------------------------------------------------------------- keyboard (CoCo 3 matrix -> Apple ASCII)
kb_key	cmpb	#$FF
	beq	kk_none
	lda	#$80
	sta	kb_any
	cmpb	kb_last
	bne	kk_new
	dec	kb_rpt			; held: auto-repeat like the Apple IIe (~0.5 s, then 15 per second)
	bne	kk_x
	lda	#KB_RATE
	sta	kb_rpt
	bra	kk_lat
kk_new	stb	kb_last
	lda	#KB_DELAY
	sta	kb_rpt
kk_lat	ldx	#kb_tab
	lda	b,x
	tst	kb_ctl
	beq	kk_l

	cmpa	#$C1
	blo	kk_l
	cmpa	#$DA
	bhi	kk_l
	anda	#$9F
kk_l	sta	kb_kbd
kk_x	rts
kk_none	clr	kb_any
	stb	kb_last
	rts
kb_rpt	fcb	0			; ticks to the next auto-repeat of the held key
KB_DELAY equ	32			; 60 Hz ticks before the first repeat
KB_RATE	equ	4			; then one every 4 ticks

kb_tab	fcb	$C0,$C1,$C2,$C3,$C4,$C5,$C6,$C7	; @ A B C D E F G
	fcb	$C8,$C9,$CA,$CB,$CC,$CD,$CE,$CF	; H I J K L M N O
	fcb	$D0,$D1,$D2,$D3,$D4,$D5,$D6,$D7	; P Q R S T U V W
	fcb	$D8,$D9,$DA,$8B,$8A,$88,$95,$A0	; X Y Z UP DOWN LEFT RIGHT SPACE
	fcb	$B0,$B1,$B2,$B3,$B4,$B5,$B6,$B7	; 0-7
	fcb	$B8,$B9,$BA,$BB,$AC,$AD,$AE,$AF	; 8 9 : ; , - . /
	fcb	$8D,$00,$9B,$00,$00,$00,$00,$00	; ENTER CLEAR BREAK(ESC) ALT CTRL F1 F2 SHIFT
KB_CTRL	equ	6*8+4

; kb_scan -> B = index of the first non-modifier key down ($FF none); kb_ctl; btn0/btn1 (right stick buttons)
kb_scan	lda	#$FF
	sta	$FF02
	lda	$FF00
	coma
	anda	#$7F
	sta	kb_ign
	clrb
	bita	#$01
	beq	ks_b1
	ldb	#$80
ks_b1	stb	btn0
	clrb
	bita	#$04
	beq	ks_b2
	ldb	#$80
ks_b2	stb	btn1
	clr	kb_ctl
	lda	#$FF
	sta	kb_new
	clr	$FF02			; all columns at once: no key down (the usual case) -> no column scan
	lda	$FF00
	ora	kb_ign
	ora	#$80
	coma
	bne	ks_any
	lda	#$FF
	sta	$FF02
	ldb	#$FF
	rts
ks_any	clr	kb_col
	lda	#$FE
	sta	kb_stb
ks_col	sta	$FF02
	lda	$FF00
	ora	kb_ign
	ora	#$80
	coma
	ldb	kb_col
	tsta
	beq	ks_nc
ks_row	lsra
	bcc	ks_nr
	cmpb	#KB_CTRL
	bne	ks_k
	inc	kb_ctl
	bra	ks_nr
ks_k	tst	kb_new
	bpl	ks_nr
	ldx	#kb_tab
	tst	b,x
	beq	ks_nr
	stb	kb_new
ks_nr	addb	#8
	tsta
	bne	ks_row
ks_nc	inc	kb_col
	lda	kb_stb
	orcc	#$01
	rola
	sta	kb_stb
	ldb	kb_col
	cmpb	#8
	blo	ks_col
	lda	#$FF
	sta	$FF02
	ldb	kb_new
	rts

; ---------------------------------------------------------------- joystick (right stick -> joy_x / joy_y, 0..63)
; Mux: PIA0 CA2 ($FF01 bit 3) = axis, PIA0 CB2 ($FF03 bit 3) = 0 (right stick).  DAC $FF20 bits 7-2,
; comparator $FF00 bit 7.  Sound enable (PIA1 CB2) is off while the DAC hunts, then the DAC goes back to mid.
joy_read
	lda	$FF20			; the DAC level (sound) to restore
	pshs	a
	lda	$FF23
	anda	#$F7
	sta	$FF23
	lda	$FF03
	anda	#$F7
	sta	$FF03
	lda	$FF01
	anda	#$F7
	sta	$FF01
	bsr	joy_sar
	ldb	#1
	bsr	joy_st
	sta	joy_x
	lda	$FF01
	ora	#$08
	sta	$FF01
	bsr	joy_sar
	ldb	#2
	bsr	joy_st
	sta	joy_y
	lda	$FF01
	anda	#$F7
	sta	$FF01
	puls	a
	sta	$FF20
	lda	$FF23
	ora	#$08
	sta	$FF23
	rts
; joy_st: A = axis value, B = its joy_seen bit.  An axis that has never read non-zero counts as centred (32):
; MAME writes an axis into the mux only once it moves, so an untouched Y would read as "full up".
joy_st	tsta
	beq	jst1
	orb	joy_seen
	stb	joy_seen
	rts
jst1	andb	joy_seen
	bne	jst2
	lda	#32
jst2	rts
joy_sar	lda	$FF20
	anda	#$03
	sta	joy_v
	ldb	#$80
js1	pshs	b
	lda	joy_v
	ora	,s+
	sta	$FF20
	tst	$FF00
	bpl	js2
	sta	joy_v
js2	lsrb
	cmpb	#$02
	bne	js1
	lda	joy_v
	lsra
	lsra
	rts

; ---------------------------------------------------------------- stacks
	rmb	64
rt_vstk				; vconv stack
	rmb	64
rt_istk				; IRQ stack (grows down)
	rmb	96
rt_stack			; startup / loader stack
