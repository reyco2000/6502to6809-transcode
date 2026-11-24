; 6502to6809 video converter: Apple hi-res page -> GIME 320x192x16.  Game-independent.
; Block BLK_VID, logical $C000 in task 1 (called by rt.asm vconv, which has already switched to task 1).
; Adapted from a2cc3/runtime/video.asm.
; Task 1 map here:
;   slot 0 = shadow of the page; slot 1 = the Apple page (bitmap)
;   slots 2-3 = GIME destination window; slots 4/5 = tables TA/TB
;   slot 6 = this block; slot 7 = runtime
; Only the dirty rectangles of the page are looked at (rt.asm vm_rect).  In them, only byte pairs that differ from
; the shadow are converted, plus their neighbours (their artifact colours depend on the changed pair).
; vc_conv in: A = page (0/1).  Clobbers all but S; DP must be VVPAGE (vc_conv sets it).

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
vc_t2	equ	$25

	org	$C000
	jmp	vc_conv			; $C000: convert page A
	jmp	vs_scroll		; $C003: line X of page A = line X+2 of page B, X = 0..189 (GIME + shadow)

; vs_scroll: the Apple bitmap was scrolled by the caller.  Make the source page's buffer current, then move its
; GIME rows and shadow rows the same way, so the destination needs no conversion.
vs_dst	equ	$10
vs_src	equ	$11
vs_y	equ	$12
vs_n	equ	$13
vs_scroll
	pshs	b
	ldb	#VVPAGE
	tfr	b,dp
	puls	b
	sta	<vs_dst
	stb	<vs_src
	tfr	b,a
	lbsr	vc_conv			; source page up to date
	lda	#BLK_SH1		; shadows: destination at $0000, source at $2000
	adda	<vs_dst
	sta	$FFA8
	lda	#BLK_SH1
	adda	<vs_src
	sta	$FFA9
	clr	<vs_y
vs_row	lda	<vs_y
	ldb	#5
	mul
	ldy	#rowtab
	leay	d,y			; Y = destination line entry, Y+10 = source line (two lines down)
	ldx	10,y			; shadow: 40 bytes
	ldu	,y
	leau	-$2000,u
; The GIME row is a function of its shadow row alone: if the destination already holds the source's row, the
; row is done (the story scroll: ~85% of the rows are blank or unchanged).  With vr_rfon the caller has
; already compared the lines (vr_rflag).
	tst	>vr_rfon
	beq	vs_cmpq
	pshs	x
	ldx	#vr_rflag
	ldb	<vs_y
	abx				; (unsigned: lines go up to 189)
	tst	,x
	puls	x
	lbeq	vs_nx
	lbra	vs_cp
vs_cmpq
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
	lbra	vs_nx
vs_cp	ldd	0,x
	std	0,u
	ldd	2,x
	std	2,u
	ldd	4,x
	std	4,u
	ldd	6,x
	std	6,u
	ldd	8,x
	std	8,u
	ldd	10,x
	std	10,u
	ldd	12,x
	std	12,u
	ldd	14,x
	std	14,u
	ldd	16,x
	std	16,u
	ldd	18,x
	std	18,u
	ldd	20,x
	std	20,u
	ldd	22,x
	std	22,u
	ldd	24,x
	std	24,u
	ldd	26,x
	std	26,u
	ldd	28,x
	std	28,u
	ldd	30,x
	std	30,u
	ldd	32,x
	std	32,u
	ldd	34,x
	std	34,u
	ldd	36,x
	std	36,u
	ldd	38,x
	std	38,u
	lda	2,y			; GIME: destination window $4000, source window $8000
	ldb	<vs_dst
	lslb
	lslb
	addb	#BLK_GIME1
	pshs	b
	adda	,s+
	sta	$FFAA
	inca
	sta	$FFAB
	lda	12,y
	ldb	<vs_src
	lslb
	lslb
	addb	#BLK_GIME1
	pshs	b
	adda	,s+
	sta	$FFAC
	inca
	sta	$FFAD
	ldu	3,y
	ldx	13,y
	leax	$4000,x
	lda	#10
	sta	<vs_n
vs_gm
	ldd	0,x
	std	0,u
	ldd	2,x
	std	2,u
	ldd	4,x
	std	4,u
	ldd	6,x
	std	6,u
	ldd	8,x
	std	8,u
	ldd	10,x
	std	10,u
	ldd	12,x
	std	12,u
	leax	14,x
	leau	14,u
	dec	<vs_n
	bne	vs_gm
vs_nx	inc	<vs_y
	lda	<vs_y
	cmpa	#190
	lblo	vs_row
	clr	>vr_rfon
	rts

vc_conv	ldb	#VVPAGE
	tfr	b,dp
	setdp	VVPAGE
	sta	<vc_page
	ldb	#BLK_SH1
	addb	<vc_page
	stb	$FFA8
	ldb	#BLK_ARAM+1
	addb	<vc_page
	stb	$FFA9
	ldb	#BLK_TA
	stb	$FFAC
	ldb	#BLK_TB
	stb	$FFAD
	lsla
	lsla
	adda	#BLK_GIME1
	sta	<vc_gbase
	lda	#$FF
	sta	<vc_blk
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
	clr	,x			; the list is consumed (IRQs are off)
	tsta
	beq	vc_ret
	cmpa	#$FF
	bne	vc_list
	clr	<vc_y			; whole page
	lda	#191
	sta	<vc_y1
	clr	<vc_p0
	lda	#20
	sta	<vc_np
	clr	<vc_n
	bra	vc_rset
vc_list	sta	<vc_n
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
vc_rset	lda	<vc_p0
	lsla
	sta	<vc_o2
	lda	<vc_np
	lsla
	sta	<vc_l2
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
vc_ret	rts

; lines vc_y..vc_y1, pairs vc_p0 .. +vc_np-1; then the next rectangle (vc_n left)
; Per line: the GIME window is remapped only when the line's block changes; the line end is source + l2.
; vc_force is 0 here (vc_edge clears it when it gives up at the end of a line).
vc_rect	lda	<vc_y
	cmpa	<vc_y1
	bhi	vc_rdone
	ldy	<vc_ry
	lda	2,y
	cmpa	<vc_blk
	bne	vc_map
vc_r1	ldx	,y
	ldb	<vc_o2
	abx
	ldb	<vc_l2
	leau	b,x
	stu	<vc_end
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
vc_f0	bra	vc_fnd
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
vc_fnd	leax	-2,x			; changed pair: destination and pair counter from its index
	ldy	<vc_ry		; (line start from rowtab, only on a change)
	tfr	x,d
	subd	,y
	lsrb
	stb	<vc_t
	lda	#7
	mul
	addd	3,y
	tfr	d,u
	lda	#20
	suba	<vc_t
	sta	<vc_cnt
	ldd	,x
	bra	vc_chg
vc_pair	ldd	,x			; only while a right neighbour must be redone
	cmpd	-$2000,x
	bne	vc_chg
	bsr	vc_cvt
	clr	<vc_force
vc_nxt	leax	2,x
	leau	7,u
	dec	<vc_cnt
	cmpx	<vc_end
	beq	vc_edge
	tst	<vc_force
	bne	vc_pair
	ldd	,x			; a run of changed pairs stays here (U and vc_cnt are already right)
	cmpd	-$2000,x
	bne	vc_chg
	leax	2,x
	bra	vc_scan
; End of the range with a right neighbour still owed (the last pair changed its bits 6-7): extend the range by
; one pair, as long as the row has one.  Without this, that neighbour kept its old left edge.
vc_edge	tst	<vc_force
	beq	vc_rowd
	tst	<vc_cnt
	beq	vc_edg0
	ldd	<vc_end
	addd	#2
	std	<vc_end
	bra	vc_pair
vc_edg0	clr	<vc_force
vc_rowd	ldd	<vc_ry
	addd	#5
	std	<vc_ry
	inc	<vc_y
	bne	vc_rect
vc_rdone
	tst	<vc_n
	lbne	vc_next
	rts
vc_map	sta	<vc_blk
	adda	<vc_gbase
	sta	$FFAA
	inca
	sta	$FFAB
	bra	vc_r1
; A neighbour sees only a few of our bits (vc_cvt): the left one bit 0 of our first byte (its TB index), the right
; one bits 6-7 of our second byte (its TA index).  It is redone only when those bits changed.
vc_chg	eora	-$2000,x
	eorb	-$1FFF,x
	std	<vc_dif
	ldd	,x
	std	-$2000,x
	tst	<vc_force
	bne	vc_c1
	lda	<vc_cnt
	cmpa	#20
	beq	vc_c1
	lda	<vc_dif
	lsra
	bcc	vc_c1
	leax	-2,x			; left neighbour: its right edge depends on us
	cmpx	<vc_last		; (unless it was just converted: vc_cvt reads the bitmap, so it already saw us)
	beq	vc_c0
	leau	-7,u
	inc	<vc_cnt
	bsr	vc_cvt
	dec	<vc_cnt
	leax	2,x
	leau	7,u
	bra	vc_c1
vc_c0	leax	2,x
vc_c1	bsr	vc_cvt
	clr	<vc_force
	lda	<vc_dif+1
	anda	#$C0
	beq	vc_nxt
	inc	<vc_force
	bra	vc_nxt

; vc_cvt: X = pair source, U = pair destination (7 bytes).  vc_cnt: 20 = first pair, 1 = last.
vc_cvt	stx	<vc_last
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
	rts
	setdp	0
