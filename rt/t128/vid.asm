; 6502to6809 video converter: Apple hi-res page -> GIME 320x192x16.  Game-independent.
; Tail of the GIME buffer's last block (BLK_VID, offset $1800), logical $D800 in task 1 (called by rt.asm vconv, which has already switched to task 1).
; Adapted from a2cc3/runtime/video.asm.
; No shadow: the screen shows conv(L), L = the page converted last (rt.asm v_ref), so a page P is compared with L
; itself.  Task 1 map here:
;   slot 0 = the reference: page L, or (B = $FF, "full") the log block holding NOT page over the rectangles
;   slot 1 = the Apple page P (bitmap); slots 2-3 = GIME window, or the log (slot 2) while logging
;   slots 4/5 = tables TA/TB; slot 6 = this block; slot 7 = runtime
; Only the dirty rectangles of the page are looked at (rt.asm vm_rect).  In them, only byte pairs that differ from
; the reference are converted, plus their neighbours (their artifact colours depend on the changed pair).
; vc_conv in: A = page P (0/1), B = reference page L (0/1, L <> P), or $FF: every pair of the rectangles.
; Clobbers all but S; DP must be VVPAGE (vc_conv sets it).
; Staged commit (B <> $FF, unless VDIRECT): converted pairs are not written to the screen but logged in the log
; block (BLK_SH1, slot 2) as runs of pairs with consecutive destinations: n (1-20), destination, 7n bytes; a
; 0,block,pad entry when the GIME block pair changes; 21 at the end.  vc_commit then copies the log to the screen at once
; (7 bytes per PULU/PSHS), so a frame's changes (a scroll step: up to ~900 pairs) appear together instead of
; while the beam scans the conversion.  A full log is committed early.
; A full conversion (B = $FF: the old content of P is unknown) writes to the screen directly.

vc_page	equ	$00
vc_gbase equ	$02
vc_rp	equ	$04			; rectangle list pointer
vc_force equ	$06
vc_cnt	equ	$07			; 20 - pair index (20 = first pair of the row, 1 = last)
vc_t	equ	$08
vc_n	equ	$09			; rectangles left
vc_y	equ	$0A			; line
vc_y1	equ	$0B
vc_p0	equ	$0C			; first pair, pairs per line
vc_np	equ	$0D
vc_left	equ	$0E
vc_rs	equ	$14			; (unused)
vc_rd	equ	$16
vc_end	equ	$18
vc_dif	equ	$1A			; changed bits of the pair (new EOR shadow), 2 bytes
vc_ry	equ	$1C			; rowtab entry of line vc_y
vc_blk	equ	$1E			; GIME block pair mapped at $4000 ($FF: none yet)
vc_o2	equ	$1F			; 2 * first pair (byte offset in the line)
vc_l2	equ	$20			; 2 * pairs per line (bytes scanned per line)
vc_last	equ	$21			; source address of the last pair converted in this call (0: none)
vc_ja	equ	$23			; scan entry for this rectangle's line width (vc_ent[np])
vc_t2	equ	$25			; (2 bytes)
vc_cf	equ	$28			; single buffer: 1 once this rectangle changed the shadow
vc_cy0	equ	$29			; lines and pairs where it changed (they go into the other page's list)
vc_cy1	equ	$2A
vc_cp0	equ	$2B
vc_cp1	equ	$2C
vc_lc	equ	$2D			; lines left in this rectangle
vc_ol	equ	$2E			; vc_o2 + vc_l2 (line end offset)
vc_lp	equ	$2F			; log pointer (2 bytes)
vc_dst	equ	$31			; pair destination being logged (2 bytes)
vc_stg	equ	$33			; 1: log + commit
vc_ref	equ	$34			; B on entry
vc_hdr	equ	$35			; log: header of the current run (2 bytes)
vc_nxd	equ	$37			; log: destination that continues the current run (2 bytes; 0: none)
vc_cc	equ	$39			; commit: CC on entry
vc_ar	equ	$3E			; area of the list (pairs x lines, 2 bytes)
VC_AREA	equ	0			; normal conversions with a smaller area go straight to the screen (0: none; all are staged)
VC_SYNC	equ	1500			; log bytes from which the commit waits for the beam (smaller: at once)
VC_BIG	equ	3900			; from here it takes over a frame: start at the top of the picture, else at VSYNC
VC_LOGEND equ	$2000			; log: the whole block

	org	$D800
	jmp	vc_conv			; $D800: convert page A
	jmp	vs_scroll		; $D803: line X of page A = line X+2 of page B, X = 0..189 (GIME + shadow)

; vs_scroll: A = page about to be converted, which is the source page (the other one, shown) scrolled up 2 lines
; (rt.asm vscroll).  Apply that to the screen in place: row X = row X+2 for X = 0..189 (ascending, so every
; source row is read before it is overwritten).  A row whose source line already equals the one 2 lines down is
; left alone.  Runs of rows that changed go into the source page's list, since the screen changed there.
vs_dst	equ	$10
vs_y0	equ	$11			; first row of the current run of changed rows ($FF: none)
vs_y	equ	$12
vs_n	equ	$13
vs_ss	equ	$14			; saved S while a GIME row is copied (2 bytes; vc_rs is unused)
vs_scroll
	pshs	a
	lda	#VVPAGE
	tfr	a,dp
	puls	a
	sta	<vs_dst
	ldb	#BLK_ARAM+2		; slot 0 = the source page (the other one, shown and converted last)
	subb	<vs_dst
	stb	$FFA8
	lda	#$FF
	sta	<vs_y0
	clr	<vs_y
vs_row	lda	<vs_y
	ldb	#5
	mul
	ldy	#rowtab
	leay	d,y			; Y = row X entry, Y+10 = row X+2
	ldx	10,y			; source page rows (Apple address - $2000: slot 0)
	leax	-$2000,x
	ldu	,y
	leau	-$2000,u
	ldd	0,x
	cmpd	0,u
	bne	vs_cp
	ldd	2,x
	cmpd	2,u
	bne	vs_cp
	ldd	4,x
	cmpd	4,u
	bne	vs_cp
	ldd	6,x
	cmpd	6,u
	bne	vs_cp
	ldd	8,x
	cmpd	8,u
	bne	vs_cp
	ldd	10,x
	cmpd	10,u
	bne	vs_cp
	ldd	12,x
	cmpd	12,u
	bne	vs_cp
	ldd	14,x
	cmpd	14,u
	bne	vs_cp
	ldd	16,x
	cmpd	16,u
	bne	vs_cp
	ldd	18,x
	cmpd	18,u
	bne	vs_cp
	ldd	20,x
	cmpd	20,u
	bne	vs_cp
	ldd	22,x
	cmpd	22,u
	bne	vs_cp
	ldd	24,x
	cmpd	24,u
	bne	vs_cp
	ldd	26,x
	cmpd	26,u
	bne	vs_cp
	ldd	28,x
	cmpd	28,u
	bne	vs_cp
	ldd	30,x
	cmpd	30,u
	bne	vs_cp
	ldd	32,x
	cmpd	32,u
	bne	vs_cp
	ldd	34,x
	cmpd	34,u
	bne	vs_cp
	ldd	36,x
	cmpd	36,u
	bne	vs_cp
	ldd	38,x
	cmpd	38,u
	bne	vs_cp
	lbsr	vs_endr			; unchanged row: a run ends here
	lbra	vs_nx
vs_cp	lda	<vs_y0			; a run of changed rows starts or goes on
	bpl	vs_c1
	lda	<vs_y
	sta	<vs_y0
vs_c1	lda	2,y			; GIME: row X window $4000, row X+2 window $8000
	adda	#BLK_GIME1
	sta	$FFAA
	inca
	sta	$FFAB
	lda	12,y
	adda	#BLK_GIME1
	sta	$FFAC
	inca
	sta	$FFAD
	ldx	13,y			; 140 bytes, 7 at a time: PULU from row X+2, PSHS to row X (S = end of the
	leau	$4000,x			; 7 bytes), then S up 14.  S and DP are data here: interrupts off for the row.
	ldx	3,y
	pshs	cc
	orcc	#$50
	sts	<vs_ss
	leas	7,x
	pulu	a,b,dp,x,y
	pshs	a,b,dp,x,y
	leas	14,s
	pulu	a,b,dp,x,y
	pshs	a,b,dp,x,y
	leas	14,s
	pulu	a,b,dp,x,y
	pshs	a,b,dp,x,y
	leas	14,s
	pulu	a,b,dp,x,y
	pshs	a,b,dp,x,y
	leas	14,s
	pulu	a,b,dp,x,y
	pshs	a,b,dp,x,y
	leas	14,s
	pulu	a,b,dp,x,y
	pshs	a,b,dp,x,y
	leas	14,s
	pulu	a,b,dp,x,y
	pshs	a,b,dp,x,y
	leas	14,s
	pulu	a,b,dp,x,y
	pshs	a,b,dp,x,y
	leas	14,s
	pulu	a,b,dp,x,y
	pshs	a,b,dp,x,y
	leas	14,s
	pulu	a,b,dp,x,y
	pshs	a,b,dp,x,y
	leas	14,s
	pulu	a,b,dp,x,y
	pshs	a,b,dp,x,y
	leas	14,s
	pulu	a,b,dp,x,y
	pshs	a,b,dp,x,y
	leas	14,s
	pulu	a,b,dp,x,y
	pshs	a,b,dp,x,y
	leas	14,s
	pulu	a,b,dp,x,y
	pshs	a,b,dp,x,y
	leas	14,s
	pulu	a,b,dp,x,y
	pshs	a,b,dp,x,y
	leas	14,s
	pulu	a,b,dp,x,y
	pshs	a,b,dp,x,y
	leas	14,s
	pulu	a,b,dp,x,y
	pshs	a,b,dp,x,y
	leas	14,s
	pulu	a,b,dp,x,y
	pshs	a,b,dp,x,y
	leas	14,s
	pulu	a,b,dp,x,y
	pshs	a,b,dp,x,y
	leas	14,s
	pulu	a,b,dp,x,y
	pshs	a,b,dp,x,y
	lds	>VVPAGE*256+vs_ss	; (extended: DP is data here)
	lda	#VVPAGE
	tfr	a,dp
	puls	cc
vs_nx	inc	<vs_y
	lda	<vs_y
	cmpa	#190
	lblo	vs_row
; vs_endr: close the run of changed rows (vs_y0 .. vs_y-1): add it to the other page's list
vs_endr	lda	<vs_y0
	bmi	vs_e1
	ldb	<vs_y
	decb
	std	>vr_y0
	ldd	#$0027
	std	>vr_c0
	lda	#$40
	tst	<vs_dst
	beq	vs_e0
	lda	#$20
vs_e0	jsr	>vm_rect
	lda	#$FF
	sta	<vs_y0
vs_e1	rts

vc_conv	pshs	b
	ldb	#VVPAGE
	tfr	b,dp
	setdp	VVPAGE
	sta	<vc_page
	puls	b
	stb	<vc_ref
	clr	<vc_stg
	cmpb	#$FF
	beq	vcc_f
	addb	#BLK_ARAM+1		; reference = page L
	stb	$FFA8
	ifndef	VDIRECT
	inc	<vc_stg
	endc
	bra	vcc_1
vcc_f	ldb	#BLK_SH1		; full: reference = NOT page (vc_pre), in the log block
	stb	$FFA8
vcc_1	ldb	#BLK_ARAM+1
	addb	<vc_page
	stb	$FFA9
	ldb	#BLK_TA
	stb	$FFAC
	ldb	#BLK_TB
	stb	$FFAD
	lda	#BLK_GIME1
	sta	<vc_gbase
	lda	#$FF
	sta	<vc_blk
	tst	<vc_stg
	beq	vcc_2
	lda	#BLK_SH1		; the log at slot 2 (the GIME window is only needed by vc_commit)
	sta	$FFAA
	ldd	#$4000
	std	<vc_lp
	clr	<vc_nxd
	clr	<vc_nxd+1
vcc_2
	clr	<vc_force
	clr	<vc_last
	clr	<vc_last+1
	ldx	#vr_n1
	ldu	#vr_l1
	tst	<vc_page
	beq	vc_p1
	leax	1,x
	leau	VR_MAX*4,u
vc_p1	lda	,x
	pshs	a
	lda	<vc_page		; the list is consumed
	jsr	>vr_clr
	puls	a
	tsta
	beq	vc_ret
	tst	<vc_ref
	bpl	vcc_3
	lbsr	vc_pre			; full: reference = NOT page
vcc_3	cmpa	#$FF
	bne	vc_list
	clr	<vc_y			; whole page
	lda	#191
	sta	<vc_y1
	clr	<vc_p0
	lda	#20
	sta	<vc_np
	clr	<vc_n
	bra	vc_rset
vc_list	jsr	>vr_merge		; no shadow: a pair in two rectangles would be converted twice
	tst	<vc_stg
	beq	vcl_1
	lbsr	vc_area			; a small one (sprites) goes straight to the screen: no log, no wait
	beq	vcl_1
	jsr	>vr_sort		; logging: top to bottom, so the commit runs about in raster order
vcl_1	sta	<vc_n
	stu	<vc_rp
vc_next	ldu	<vc_rp
	ldd	,u			; lines
	sta	<vc_y
	cmpb	#191
	bls	vc_n1
	ldb	#191
vc_n1	stb	<vc_y1
	ldd	2,u			; columns -> pairs p0 .. p1+1 (the next pair's left edge depends on us)
	lsra
	lsrb
	incb
	cmpb	#19
	bls	vc_n2
	ldb	#19
vc_n2	sta	<vc_p0
	subb	<vc_p0
	bmi	vc_skip
	incb
	stb	<vc_np
	leau	4,u
	stu	<vc_rp
	dec	<vc_n
; per rectangle: byte offset and length in the line, rowtab entry of the first line
vc_rset	clr	<vc_cf
	lda	<vc_y1
	suba	<vc_y
	bcs	vc_rdone		; empty
	inca
	sta	<vc_lc
	lda	<vc_p0
	lsla
	sta	<vc_o2
	lda	<vc_np
	lsla
	sta	<vc_l2
	adda	<vc_o2
	sta	<vc_ol
	lda	<vc_y
	ldb	#5
	mul
	addd	#rowtab
	std	<vc_ry
	ldb	<vc_l2
	ldy	#vc_ent
	ldd	b,y
	std	<vc_ja
	bra	vc_rect
vc_skip	leau	4,u
	stu	<vc_rp
	dec	<vc_n
	bne	vc_next
vc_ret	tst	<vc_stg
	bne	vr_cm
	rts
vr_cm	tfr	cc,a			; a big log (a scroll step) is committed when the beam cannot see it change:
	bita	#$10			; in about raster order (vc_sort), every line changes after the beam has shown
	bne	vr_cm1			; it and before it shows it again.  (Not with interrupts off: no ticks.)
	ldd	<vc_lp
	subd	#$4000+VC_SYNC
	blo	vr_cm1
	cmpd	#VC_BIG-VC_SYNC
	bhs	vr_top
	lda	>rt_tick+1		; under a frame to copy: start at the tick (VSYNC, below the picture)
vr_t1	cmpa	>rt_tick+1
	beq	vr_t1
	bra	vr_cm1
vr_top	lda	$FF92			; longer (the beam overtakes it): start at the top of the picture, the end of
	anda	#$20			; the IRQ's VBL window (GIME timer, as rt.asm rt_vbl)
	ora	>vbl_over
	sta	>vbl_over
	beq	vr_w2			; still in the window: wait for its end
	lda	>rt_tick+1		; past it: next tick, then the end of its window
vr_w1	cmpa	>rt_tick+1
	beq	vr_w1
vr_w2	lda	$FF92
	anda	#$20
	ora	>vbl_over
	sta	>vbl_over
	beq	vr_w2
vr_cm1	lbra	vc_commit		; (an empty log: only the slots are put back)

; vc_lc lines from vc_y, pairs vc_p0 .. +vc_np-1; then the next rectangle (vc_n left)
; Per line only the scan: the GIME block and the line end (vc_end) are set up by vc_fnd, on a change.
; vc_force is 0 here (vc_edge clears it when it gives up at the end of a line).
vc_rect	ldy	<vc_ry
	ldx	,y
	ldb	<vc_o2
	abx
	jmp	[VVPAGE*256+vc_ja]
; fast scan: compare words with the shadow until one differs.  Unrolled; entered at vc_u<n> with n words left
; (vc_ent).  vc_scan resumes at X after a run of changed pairs.
vc_scan	ldd	<vc_end
	stx	<vc_t2
	subd	<vc_t2
	beq	vc_rowd
	ldy	#vc_ent
	jmp	[b,y]
vc_u20	ldd	,x++
	cmpd	-$2002,x
	bne	vc_f0
vc_u19	ldd	,x++
	cmpd	-$2002,x
	bne	vc_f0
vc_u18	ldd	,x++
	cmpd	-$2002,x
	bne	vc_f0
vc_u17	ldd	,x++
	cmpd	-$2002,x
	bne	vc_f0
vc_u16	ldd	,x++
	cmpd	-$2002,x
	bne	vc_f0
vc_u15	ldd	,x++
	cmpd	-$2002,x
	bne	vc_f0
vc_u14	ldd	,x++
	cmpd	-$2002,x
	bne	vc_f0
vc_u13	ldd	,x++
	cmpd	-$2002,x
	bne	vc_f0
vc_u12	ldd	,x++
	cmpd	-$2002,x
	bne	vc_f0
vc_u11	ldd	,x++
	cmpd	-$2002,x
	bne	vc_f0
	bra	vc_u10
vc_f0	lbra	vc_fnd
vc_u10	ldd	,x++
	cmpd	-$2002,x
	bne	vc_f0
vc_u9	ldd	,x++
	cmpd	-$2002,x
	bne	vc_f0
vc_u8	ldd	,x++
	cmpd	-$2002,x
	bne	vc_f0
vc_u7	ldd	,x++
	cmpd	-$2002,x
	bne	vc_f0
vc_u6	ldd	,x++
	cmpd	-$2002,x
	bne	vc_f0
vc_u5	ldd	,x++
	cmpd	-$2002,x
	bne	vc_f0
vc_u4	ldd	,x++
	cmpd	-$2002,x
	bne	vc_f0
vc_u3	ldd	,x++
	cmpd	-$2002,x
	bne	vc_f0
vc_u2	ldd	,x++
	cmpd	-$2002,x
	bne	vc_f0
vc_u1	ldd	,x++
	cmpd	-$2002,x
	bne	vc_f0
	lbra	vc_rowd
vc_ent	fdb	0,vc_u1,vc_u2,vc_u3,vc_u4,vc_u5,vc_u6,vc_u7,vc_u8,vc_u9,vc_u10
	fdb	vc_u11,vc_u12,vc_u13,vc_u14,vc_u15,vc_u16,vc_u17,vc_u18,vc_u19,vc_u20
; vc_fnd: first changed pair of a run (from the scan, X = pair + 2).  Sets the GIME block, the line end, U and
; vc_cnt from the pair index, and the bounding box of the shadow changes (line, first pair); redoes the left
; neighbour if bit 0 of our first byte changed (its right edge sees it).  Then the run loop.
vc_fnd	leax	-2,x
	ldy	<vc_ry			; (line start from rowtab, only on a change)
	lda	2,y			; the line's GIME block pair
	cmpa	<vc_blk
	beq	vf_1
	lbsr	vc_map
vf_1	ldd	,y			; line end
	addb	<vc_ol
	adca	#0
	std	<vc_end
	tfr	x,d
	subd	,y
	lsrb
	stb	<vc_t			; pair index
	lda	#7
	mul
	addd	3,y
	tfr	d,u
	lda	#20
	suba	<vc_t
	sta	<vc_cnt
	lda	<vc_y1			; line vc_y1+1-vc_lc
	inca
	suba	<vc_lc
	sta	<vc_cy1
	ldb	<vc_t
	tst	<vc_cf
	bne	vf_b1
	sta	<vc_cy0
	stb	<vc_cp0
	stb	<vc_cp1
	inc	<vc_cf
	bra	vf_b2
vf_b1	cmpb	<vc_cp0
	bhs	vf_b2
	stb	<vc_cp0
vf_b2	tstb
	beq	vf_p0			; pair 0: no left neighbour (and no byte before it)
	lda	,x
	eora	-$2000,x
	lsra
	bcc	vr_lp
	leax	-2,x			; left neighbour, unless it was just converted (vc_cvt reads the bitmap, so it
	cmpx	<vc_last		; already saw us)
	beq	vf_l0
	leau	-7,u
	inc	<vc_cnt
	lbsr	vc_cvt
	dec	<vc_cnt
	leau	7,u
vf_l0	leax	2,x
	bra	vr_lp
vf_p0	ldb	1,x			; pair 0 through vc_cvt (it handles the missing byte before it)
	eorb	-$1FFF,x
	stb	<vc_dif+1
	lbsr	vc_cvt
	bra	vr_adv
; run of changed pairs: X = pair (not the first of the line), U = its destination, vc_cnt.  Convert;
; vc_dif+1 keeps the changed bits of the second byte (bits 6-7: the right neighbour sees them).
vr_lp	ldb	1,x
	eorb	-$1FFF,x
	stb	<vc_dif+1
	tst	<vc_stg
	bne	vr_gen			; logging: through vc_cvt
	lda	<vc_cnt
	deca
	beq	vr_gen			; last pair of the line: no byte after it
	lda	-1,x			; vc_cvt inline (see there)
	ldb	1,x
	lsrb
	rola
	rola
	rola
	anda	#7
	ldb	,x
	lslb
	rola
	lslb
	rola
	adda	#$80
	tfr	d,y
	ldd	,y
	std	,u
	ldd	2,y
	sta	2,u
	stb	<vc_t
	ldb	2,x
	lsrb
	lda	,x
	rola
	rola
	rola
	anda	#7
	ldb	1,x
	lslb
	rola
	lslb
	rola
	adda	#$A0
	tfr	d,y
	ldd	,y
	ora	<vc_t
	std	3,u
	ldd	2,y
	std	5,u
vr_adv	leax	2,x
	leau	7,u
	dec	<vc_cnt
	cmpx	<vc_end
	beq	vr_re
	ldd	,x
	cmpd	-$2000,x
	bne	vr_lp
	bsr	vr_cp1			; the run ends: X is unchanged
	lda	<vc_dif+1
	anda	#$C0
	beq	vr_sc
	lbsr	vc_cvt			; right neighbour: its left edge depends on us
	leax	2,x
	leau	7,u
	dec	<vc_cnt
	cmpx	<vc_end
	beq	vc_rowd
	ldd	,x
	cmpd	-$2000,x
	bne	vr_lp			; (its left neighbour was just converted)
vr_sc	leay	-2,x			; the last pair converted
	sty	<vc_last
	leax	2,x			; X is unchanged: scan on after it
	lbra	vc_scan
vr_gen	lbsr	vc_cvt
	bra	vr_adv
; end of the range.  With a right neighbour still owed (the last pair changed its bits 6-7) extend the range by
; one pair, as long as the row has one.  Without this, that neighbour kept its old left edge.
vr_re	leay	-2,x
	sty	<vc_last
	bsr	vr_cp1
	lda	<vc_dif+1
	anda	#$C0
	beq	vc_rowd
	tst	<vc_cnt
	beq	vc_rowd
	ldd	<vc_end
	addd	#2
	std	<vc_end
	ldd	,x
	cmpd	-$2000,x
	bne	vr_lp
	lbsr	vc_cvt
	bra	vc_rowd
; bounding box: last changed pair = the one before X
vr_cp1	ldb	#19
	subb	<vc_cnt
	cmpb	<vc_cp1
	bls	vr_c1
	stb	<vc_cp1
vr_c1	rts
vc_rowd	ldd	<vc_ry
	addd	#5
	std	<vc_ry
	dec	<vc_lc
	bne	vc_rect
vc_rdone
	tst	<vc_cf			; the shadow changed here: the other page may now differ from it
	beq	vc_rd1
	lda	<vc_cy0
	ldb	<vc_cy1
	std	>vr_y0
	lda	<vc_cp0
	lsla
	ldb	<vc_cp1
	lslb
	incb
	std	>vr_c0
	lda	#$40
	tst	<vc_page
	beq	vc_rd0
	lda	#$20
vc_rd0	jsr	>vm_rect
vc_rd1	tst	<vc_n
	lbne	vc_next
	bra	vc_ret
vc_map	sta	<vc_blk
	tst	<vc_stg
	bne	vc_mark
	adda	<vc_gbase
	sta	$FFAA
	inca
	sta	$FFAB
	rts
vc_mark	ldu	<vc_lp			; log: $00, block pair, (pad)
	clr	,u+
	lda	<vc_blk
	sta	,u+
	leau	1,u
	stu	<vc_lp
	clr	<vc_nxd			; (a new run follows)
	clr	<vc_nxd+1
	rts
; vc_cvt: X = pair source, U = pair destination (7 bytes).  vc_cnt: 20 = first pair, 1 = last.
vc_cvt	stx	<vc_last
	tst	<vc_stg
	beq	vcv_0
	stu	<vc_dst
	cmpu	<vc_nxd
	beq	vcv_ap
	ldu	<vc_lp			; a new run: n, destination
	stu	<vc_hdr
	clr	,u+
	ldd	<vc_dst
	std	,u++
	bra	vcv_a1
vcv_ap	ldu	<vc_lp
vcv_a1	inc	[VVPAGE*256+vc_hdr]
	ldd	<vc_dst
	addd	#7
	std	<vc_nxd
vcv_0
	lda	<vc_cnt
	cmpa	#20
	bne	vc_c2
	clra
	bra	vc_c3
vc_c2	lda	-1,x
vc_c3	ldb	1,x
	lsrb
	rola
	rola
	rola
	anda	#7
	ldb	,x
	lslb
	rola
	lslb
	rola
	adda	#$80
	tfr	d,y
	ldd	,y
	std	,u
	ldd	2,y
	sta	2,u
	stb	<vc_t
	lda	<vc_cnt
	cmpa	#1
	bne	vc_c4
	clrb
	bra	vc_c5
vc_c4	ldb	2,x
vc_c5	lsrb
	lda	,x
	rola
	rola
	rola
	anda	#7
	ldb	1,x
	lslb
	rola
	lslb
	rola
	adda	#$A0
	tfr	d,y
	ldd	,y
	ora	<vc_t
	std	3,u
	ldd	2,y
	std	5,u
	tst	<vc_stg
	beq	vcv_r
	leau	7,u
	stu	<vc_lp
	cmpu	#$4000+VC_LOGEND-16	; room for a mark, a run header, a pair and the end
	blo	vcv_x
	pshs	x			; full: show what is logged, go on with an empty log
	bsr	vc_commit
	bsr	vc_mark
	puls	x
vcv_x	ldu	<vc_dst
vcv_r	rts

; vc_commit: copy the log to the screen (slot 1 = log, slots 2-3 = GIME window), empty it.  Then slot 1 is the
; Apple page and slot 2 the log again.  A run of n pairs is 7n contiguous bytes: PULU 7 / PSHS 7 / LEAS 14,S,
; entered n units from the end.  S and DP are data until the end, so interrupts are off for the whole commit
; (under a frame for a scroll step; a longer one delays the next tick).  Clobbers D, X, Y, U.
vc_commit
	ldu	<vc_lp
	lda	#21			; end
	sta	,u
	lda	#BLK_SH1
	sta	$FFA9
	ldu	#$2000
	tfr	cc,a
	sta	<vc_cc
	sts	<vs_ss
	orcc	#$50
cm_lp	pulu	a,x			; n, destination (or 0, block / 21)
	leas	7,x
	ldy	#cm_tab
	lsla
	jmp	[a,y]
cm_blk	tfr	x,d			; A = block pair
	adda	>VVPAGE*256+vc_gbase	; (extended: DP is data here)
	sta	$FFAA
	inca
	sta	$FFAB
	bra	cm_lp
cm_u20	pulu	a,b,dp,x,y
	pshs	a,b,dp,x,y
	leas	14,s
cm_u19	pulu	a,b,dp,x,y
	pshs	a,b,dp,x,y
	leas	14,s
cm_u18	pulu	a,b,dp,x,y
	pshs	a,b,dp,x,y
	leas	14,s
cm_u17	pulu	a,b,dp,x,y
	pshs	a,b,dp,x,y
	leas	14,s
cm_u16	pulu	a,b,dp,x,y
	pshs	a,b,dp,x,y
	leas	14,s
cm_u15	pulu	a,b,dp,x,y
	pshs	a,b,dp,x,y
	leas	14,s
cm_u14	pulu	a,b,dp,x,y
	pshs	a,b,dp,x,y
	leas	14,s
cm_u13	pulu	a,b,dp,x,y
	pshs	a,b,dp,x,y
	leas	14,s
cm_u12	pulu	a,b,dp,x,y
	pshs	a,b,dp,x,y
	leas	14,s
cm_u11	pulu	a,b,dp,x,y
	pshs	a,b,dp,x,y
	leas	14,s
cm_u10	pulu	a,b,dp,x,y
	pshs	a,b,dp,x,y
	leas	14,s
cm_u9	pulu	a,b,dp,x,y
	pshs	a,b,dp,x,y
	leas	14,s
cm_u8	pulu	a,b,dp,x,y
	pshs	a,b,dp,x,y
	leas	14,s
cm_u7	pulu	a,b,dp,x,y
	pshs	a,b,dp,x,y
	leas	14,s
cm_u6	pulu	a,b,dp,x,y
	pshs	a,b,dp,x,y
	leas	14,s
cm_u5	pulu	a,b,dp,x,y
	pshs	a,b,dp,x,y
	leas	14,s
cm_u4	pulu	a,b,dp,x,y
	pshs	a,b,dp,x,y
	leas	14,s
cm_u3	pulu	a,b,dp,x,y
	pshs	a,b,dp,x,y
	leas	14,s
cm_u2	pulu	a,b,dp,x,y
	pshs	a,b,dp,x,y
	leas	14,s
cm_u1	pulu	a,b,dp,x,y
	pshs	a,b,dp,x,y
	leas	14,s
	bra	cm_lp
cm_tab	fdb	cm_blk,cm_u1,cm_u2,cm_u3,cm_u4,cm_u5,cm_u6,cm_u7,cm_u8,cm_u9,cm_u10,cm_u11,cm_u12,cm_u13,cm_u14,cm_u15,cm_u16,cm_u17,cm_u18,cm_u19,cm_u20,cm_x
cm_x	lds	>VVPAGE*256+vs_ss	; (extended: DP is data here)
	lda	>VVPAGE*256+vc_cc
	tfr	a,cc
	lda	#VVPAGE
	tfr	a,dp
	ldb	#BLK_ARAM+1
	addb	<vc_page
	stb	$FFA9
	lda	#BLK_SH1
	sta	$FFAA
	ldd	#$4000
	std	<vc_lp
	clr	<vc_nxd
	clr	<vc_nxd+1
	rts

; vc_area: A = count (< $FF), U = list -> vc_ar = pairs x lines; below VC_AREA vc_stg = 0 (Z=1).  Preserves A, U.
vc_area	pshs	a,u
	sta	<vc_t
	clr	<vc_ar
	clr	<vc_ar+1
va_l	ldd	2,u			; pairs, as vc_next
	lsra
	lsrb
	incb
	cmpb	#19
	bls	va_1
	ldb	#19
va_1	stb	<vc_t2
	suba	<vc_t2
	nega
	bmi	va_n
	inca
	sta	<vc_t2
	ldd	,u			; lines
	cmpb	#191
	bls	va_2
	ldb	#191
va_2	subb	,u
	bcs	va_n
	incb
	lda	<vc_t2
	mul
	addd	<vc_ar
	std	<vc_ar
va_n	leau	4,u
	dec	<vc_t
	bne	va_l
	ldd	<vc_ar
	cmpd	#VC_AREA
	bhs	va_x
	clr	<vc_stg
va_x	puls	a,u
	tst	<vc_stg
	rts

; vc_pre: full conversion: the reference (slot 0) = NOT page over the list (A = count or $FF, U = entries), whole
; lines (so the neighbours of each rectangle count as changed too).  Preserves A, U.
vc_pre	pshs	a,u
	cmpa	#$FF
	bne	vp_l
	ldx	#$2000			; whole page
vp_a	ldd	,x++
	coma
	comb
	std	-$2002,x
	cmpx	#$4000
	blo	vp_a
	puls	a,u,pc
vp_l	sta	<vc_t
vp_r	lda	,u			; lines ,u .. 1,u (clipped to 191)
	ldb	1,u
	cmpb	#191
	bls	vp_1
	ldb	#191
vp_1	stb	<vc_y1
	sta	<vc_y
	cmpa	<vc_y1
	bhi	vp_n
	ldb	#5
	mul
	addd	#rowtab
	tfr	d,y
vp_ln	ldx	,y
	ldb	#20
vp_w	lda	,x+
	coma
	sta	-$2001,x
	lda	,x+
	coma
	sta	-$2001,x
	decb
	bne	vp_w
	leay	5,y
	inc	<vc_y
	lda	<vc_y
	cmpa	<vc_y1
	bls	vp_ln
vp_n	leau	4,u
	dec	<vc_t
	bne	vp_r
	puls	a,u,pc
	setdp	0
