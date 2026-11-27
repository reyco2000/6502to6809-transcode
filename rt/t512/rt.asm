; 6502to6809 resident runtime (CoCo 3, 512K).  Game-independent: it runs a statically transcoded Apple II hi-res game.
; Logical $E000-$FDFF in task 0 and task 1 (block BLK_RT).  $FE00-$FEFF is the GIME constant page (vectors,
; and the vconv variables at $FE00).
;
; The build supplies these equates (layout.inc):
;   BLK_GIME1/2  first of 4 blocks of each GIME 320x192x16 buffer
;   BLK_SH1      shadow of hi-res page 1 (page 2 = +1)
;   BLK_TA/TB    conversion tables
;   BLK_ARAM     Apple RAM $0000-$BFFF as 6 consecutive blocks
;                (+1/+2 = hi-res pages 1/2: the authoritative bitmaps)
;   BLK_EC1/2    transcoded engine code, logical $2000-$5FFF outside hi-res routines
;   BLK_VID      vconv + rowtab (task 1 slot 6)
;   BLK_RT       this block
;   BLK_STG      4-block staging area
;   RT_START_GAME  (label) game entry after init
;
; Task 0 map while the game runs:
;   $0000 Apple RAM, $2000/$4000 engine code (hi-res bitmaps inside hr_on/hr_off),
;   $6000-$BFFF Apple RAM, $C000 game bank (cur_s6), $E000 runtime.
; Register convention of the transcoded code (see kit/xc65.py):
;   A = 6502 A, B = 6502 X, RY = 6502 Y, S = 6502 stack, DP = 0.
;   I/O handlers pass the value in IOV and preserve A, B and CC.

VR_MAX	equ	24			; dirty rectangles per page before it counts as fully dirty
VVPAGE	equ	$FE			; vconv direct page (constant page)

	org	$E000
rt_go	jmp	rt_start

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
rt_start
	orcc	#$50
	lds	#rt_stack
	lda	#$4C			; CoCo 3 mode, MMU on, constant page $FExx, no ROM
	sta	$FF90
	clr	$FF91			; task 0
	clr	$FF92
	clr	$FF93
	sta	$FFD9			; 1.79 MHz
	sta	$FFDF			; SAM all RAM
	lda	#BLK_RT
	sta	$FFA7
	sta	$FFAF
	; clear the GIME buffers and the shadows through slot 2
	lda	#BLK_GIME1
rs_clr	pshs	a
	sta	$FFA2
	ldx	#$4000
	ldd	#0
rs_c1	std	,x++
	std	,x++
	cmpx	#$6000
	bne	rs_c1
	puls	a
	inca
	cmpa	#BLK_SH1+2
	bne	rs_clr
	lda	#$FF			; both pages fully dirty
	sta	vr_n1
	sta	vr_n2
	; unpack the compressed blobs (table from the build: src block, src offset, dest block; 0 ends)
	lda	#VVPAGE
	tfr	a,dp
	setdp	VVPAGE
	ldy	#rt_blobs
rs_b1	lda	,y
	beq	rs_b2
	pshs	y
	ldx	1,y
	ldb	3,y
	jsr	unlz
	puls	y
	leay	4,y
	bra	rs_b1
rs_b2	clra
	tfr	a,dp
	setdp	0
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
	clr	$FF9D
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
vr_o1	fcb	0			; offset in the list of the entry that last absorbed or took a rectangle (vr_n1+2)
vr_o2	fcb	0			; (vr_n2+2)
vr_l1	rmb	VR_MAX*4
vr_l2	rmb	VR_MAX*4
vr_rfon	fcb	0			; 1: vs_scroll takes vr_rflag instead of comparing shadow rows (cleared by vs_scroll)
vr_rflag rmb	190			; per destination line: 0 = the destination already holds the source's line
vr_y0	fcb	0			; vm_rect arguments
vr_y1	fcb	0
vr_c0	fcb	0
vr_c1	fcb	0

; vm_rect: A = page high byte ($20-$3F page 1, $40-$5F page 2), rectangle in vr_y0/vr_y1/vr_c0/vr_c1.
; Preserves all but CC.  Runs with interrupts off (the IRQ empties the lists).
vm_rect	pshs	cc,d,x,y,u
	orcc	#$50
	ldx	#vr_n1
	ldu	#vr_l1
	cmpa	#$40
	blo	vmr1
	leax	1,x
	leau	VR_MAX*4,u
vmr1	cmpa	#$60
	bhs	vmr_x
	ldb	,x
	cmpb	#$FF
	beq	vmr_x
	cmpb	#VR_MAX
	bhs	vmr_full
	tstb
	beq	vmr_sl0
	lslb				; fast path: inside the entry that matched last time (the sprites of a frame
	lslb				; are drawn inside the area erased just before)
	pshs	b
	ldb	2,x
	cmpb	,s+
	bhs	vmr_sl
	leay	b,u
	lda	vr_y0
	cmpa	,y
	blo	vmr_sl
	lda	vr_y1
	cmpa	1,y
	bhi	vmr_sl
	lda	vr_c0
	cmpa	2,y
	blo	vmr_sl
	lda	vr_c1
	cmpa	3,y
	bls	vmr_x
vmr_sl	ldb	,x
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
vmr_hit	tfr	y,d			; remember the entry
	pshs	u
	subd	,s++
	stb	2,x
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
	stb	2,x
	leau	b,u
vmr_add	ldd	vr_y0
	std	,u
	ldd	vr_c0
	std	2,u
	inc	,x
vmr_x	puls	cc,d,x,y,u,pc
vmr_full
	lda	#$FF
	sta	,x
	bra	vmr_x
; vm_page: A = page high byte: the whole page.  Preserves all.
vm_page	pshs	cc,b
	ldb	#$FF
	cmpa	#$40
	bhs	vmp1
	stb	vr_n1
	puls	cc,b,pc
vmp1	cmpa	#$60
	bhs	vmp2
	stb	vr_n2
vmp2	puls	cc,b,pc

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

; vshow: A = page (0/1): display its GIME buffer
vshow	pshs	a
	tsta
	beq	vs1
	lda	#BLK_GIME2*4
vs1	sta	$FF9D
	clr	$FF9E
	puls	a,pc

; vconv: A = page.  The converter lives in BLK_VID (task 1 slot 6): switch to task 1 here and come back.
; Interrupts stay on: the IRQ runs on its own stack in the runtime block and touches neither the MMU nor DP.
vconv	pshs	cc,d,dp,x,y,u
	sts	vc_sv			; task 1 maps the shadow at $0000: use a runtime stack
	lds	#rt_vstk
	ldb	#1
	stb	$FF91
	jsr	vid_conv		; in BLK_VID
	clr	$FF91
	lds	vc_sv
	puls	cc,d,dp,x,y,u,pc
vc_sv	fdb	0

; vscroll: A = destination page, B = source page (0/1): the GIME side of a 2-line scroll (vid.asm vs_scroll)
vscroll	pshs	cc,d,dp,x,y,u
	sts	vc_sv
	lds	#rt_vstk
	pshs	b
	ldb	#1
	stb	$FF91
	puls	b
	jsr	vid_scroll
	clr	$FF91
	lds	vc_sv
	puls	cc,d,dp,x,y,u,pc

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

; ---------------------------------------------------------------- LZSS decoder (format: kit/disk/lzss.py)
; From the zaxxon cocoport unlz (via a2cc3/runtime/unlz.asm); here the source may start inside its block.
; In: A = source block, X = source offset ($0000-$1FFF), B = destination block (output from its start).
; DP must be VVPAGE.  Uses task 1 slots 4-6, ends in task 0.  Clobbers A, B, X, Y, U.
lz_sp	equ	$A0
lz_dp	equ	$A1
lz_f	equ	$A2
lz_n	equ	$A3
lz_len	equ	$A4
unlz	sta	<lz_sp
	stb	<lz_dp
	sta	$FFAE
	stb	$FFAC
	incb
	stb	$FFAD
	lda	#1
	sta	$FF91
	leax	$C000,x
	ldy	#$8000
lz_grp	bsr	lz_get
	sta	<lz_f
	lda	#8
	sta	<lz_n
lz_item	cmpy	#$B000
	blo	lz_i1
	leay	-$2000,y
	inc	<lz_dp
	ldb	<lz_dp
	stb	$FFAC
	incb
	stb	$FFAD
lz_i1	lsr	<lz_f
	bcc	lz_mt
	bsr	lz_get
	sta	,y+
	bra	lz_nx
lz_mt	bsr	lz_get
	tfr	a,b
	lsrb
	lsrb
	lsrb
	lsrb
	addb	#3
	stb	<lz_len
	anda	#$0F
	pshs	a
	bsr	lz_get
	tfr	a,b
	puls	a
	cmpd	#0
	bne	lz_m1
	lda	<lz_len
	cmpa	#3
	beq	lz_end
	clra
lz_m1	addd	#1
	pshs	d
	tfr	y,d
	subd	,s++
	tfr	d,u
	ldb	<lz_len
lz_m2	lda	,u+
	sta	,y+
	decb
	bne	lz_m2
lz_nx	dec	<lz_n
	bne	lz_item
	bra	lz_grp
lz_end	lda	#BLK_VID		; task 1 slot 6 back to the converter
	sta	$FFAE
	clr	$FF91
	rts
lz_get	lda	,x+
	cmpx	#$E000
	bne	lz_g1
	ldx	#$C000
	inc	<lz_sp
	pshs	b
	ldb	<lz_sp
	stb	$FFAE
	puls	b
lz_g1	rts

; ---------------------------------------------------------------- stacks
	rmb	64
rt_vstk				; vconv stack
	rmb	64
rt_istk				; IRQ stack (grows down)
	rmb	96
rt_stack			; startup / loader stack
