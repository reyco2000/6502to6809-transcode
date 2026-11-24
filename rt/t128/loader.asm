; 6502to6809 fast loader (game-independent).  <loader>.BIN is this small LOADM program at $0E00 (graphics pages: free
; under Disk BASIC).  It:
;   1. shows a title on the CoCo 3 40-column text screen (physical block LD_TBLK, mapped at $2000 while loading),
;   2. reads the data file by driving the WD1793 directly (no DSKCON: its per-call overhead costs about one disk
;      revolution per sector).  Each track is read in one pass, in physical sector order: the order is learnt
;      once with back-to-back READ ADDRESS commands (so any interleave works: MAME, XRoar, a DSKINI floppy);
;      each track starts with one READ ADDRESS to know where the head is.  Every sector goes straight to its
;      8K block through MMU slot 2 ($4000).  Polled transfers (no HALT); interrupts off.
;   3. boots the runtime (block LD_RTBLK at $E000, JMP $E000 = rt_boot) with the motor on: A = DSKREG value,
;      B = track under the head, X/Y = progress bar cell/accumulator.  The runtime streams the rest of the data
;      file itself (dstream.asm) and continues the bar (LD_TOTAL counts its sectors too).
; The build supplies (appended after this file):
;   ld_pal    16 palette values
;   ld_title  rows: fcb row,col,attr / fcc "text" / fcb 0 ... fcb $FF
;   ld_trks   per track, ascending: fcb track,count / count x (fcb sector,block,page) ... fcb $FF
; and the equates LD_TBLK, LD_RTBLK, LD_TOTAL, LD_BAR_ROW, LD_BAR_COL, LD_BAR_LEN, LD_BARATTR, LD_ERRATTR.

DSKREG	equ	$FF40
FDCCMD	equ	$FF48			; command / status
FDCTRK	equ	$FF49
FDCSEC	equ	$FF4A
FDCDAT	equ	$FF4B
DEFDRV	equ	$095A			; drive of the last disk command (LOADM)
STEP	equ	$03			; step rate bits (30 ms, as safe as Disk BASIC)

	org	$0E00
ld_start
	orcc	#$50
	; ---- title screen: 40x24, 2 bytes per cell (character, attribute)
	lda	#LD_TBLK
	sta	$FFA1			; screen at $2000 while loading
	ldx	#$2000
	ldd	#$2000			; space, attribute 0 (palette 8 on palette 0)
ld_cls	std	,x++
	cmpx	#$2000+40*24*2
	blo	ld_cls
	ldu	#ld_title
ld_line	lda	,u+			; row ($FF = end)
	cmpa	#$FF
	beq	ld_txtd
	ldb	#80
	mul
	addd	#$2000
	tfr	d,x
	ldb	,u+			; column
	lslb
	abx
	lda	,u+			; attribute
ld_chr	ldb	,u+
	beq	ld_line
	stb	,x+
	sta	,x+
	bra	ld_chr
ld_txtd
	ldx	#ld_pal			; palette: 0-7 backgrounds, 8-15 foregrounds
	ldu	#$FFB0
ld_pl	lda	,x+
	sta	,u+
	cmpu	#$FFC0
	blo	ld_pl
	lda	#$4C			; GIME video modes on (CoCo 3 mode, MMU on, constant page)
	sta	$FF90
	lda	#$03			; text, 8 lines per row
	sta	$FF98
	lda	#$05			; 40 columns, attributes
	sta	$FF99
	clr	$FF9A
	clr	$FF9C
	lda	#LD_TBLK*4		; video start = block * 8K / 8
	sta	$FF9D
	clr	$FF9E
	clr	$FF9F
	ldd	#$2000+LD_BAR_ROW*80+LD_BAR_COL*2
	std	ld_bar
	clr	ld_acc
	clr	ld_acc+1

	; ---- drive: NMI (raised by INTRQ with DSKREG bit 5) only returns; motor on, double density
	ldx	#$FEFD
	ldu	#ld_onmi
	ldd	,x
	std	,u
	lda	2,x
	sta	2,u
	lda	#$7E			; JMP ld_nmi
	sta	,x
	ldd	#ld_nmi
	std	1,x
	ldb	DEFDRV
	ldx	#ld_dsel
	lda	b,x
	ora	#$28			; double density + NMI enable, motor on
	sta	ld_dreg
	sta	DSKREG
	ldx	#0			; motor spin-up: ~0.4 s
ld_spin	leax	-1,x
	nop
	nop
	bne	ld_spin
	lda	#STEP			; RESTORE: head to track 0
	lbsr	ld_cmd
	lbsr	ld_wait
	clr	ld_ctrk
	clr	ld_seek

	; ---- tracks
	ldy	#ld_trks
ld_trk	lda	,y+
	cmpa	#$FF
	lbeq	ld_done
	cmpa	ld_ctrk
	beq	ld_t1
	sta	ld_ctrk			; SEEK
	sta	FDCDAT
	lda	#$10+STEP
	lbsr	ld_cmd
	lbsr	ld_wait
	lda	#1
	sta	ld_seek			; the next READ ADDRESS waits for the head to settle
ld_t1	lda	,y+
	sta	ld_cnt
	ldx	#ld_need		; need[sector] = entry pointer (0 = not on this track's list)
	ldb	#19*2
ld_clr	clr	,x+
	decb
	bne	ld_clr
	ldb	ld_cnt
ld_t2	lda	,y			; sector
	lsla
	ldx	#ld_need
	sty	a,x
	leay	3,y
	decb
	bne	ld_t2
	tst	ld_plen			; physical order not learnt yet: learn it on this track
	bne	ld_t3
	lbsr	ld_learn
ld_t3	lbsr	ld_sync			; A = position (index in ld_phys) of the sector under the head
	sta	ld_pos
ld_t4	lda	ld_pos			; next physical sector
	inca
	cmpa	ld_plen
	blo	ld_t5
	clra
ld_t5	sta	ld_pos
	ldx	#ld_phys
	lda	a,x			; sector number
	lsla
	ldx	#ld_need
	ldu	a,x
	beq	ld_t4			; not needed
	pshs	a
	lbsr	ld_read			; U = entry: sector, block, page
	puls	a
	bcs	ld_t3			; error: resync and try again
	ldx	#ld_need
	leax	a,x
	clr	,x
	clr	1,x
	lbsr	ld_prog
	dec	ld_cnt
	bne	ld_t4
	lbra	ld_trk

ld_done	lda	#LD_RTBLK		; runtime: it streams the rest (dstream.asm) with the motor still on
	sta	$FFA7
	lda	ld_dreg
	ldb	ld_ctrk
	ldx	ld_bar
	ldy	ld_acc
	jmp	$E000			; rt_boot

ld_nmi	rti

; ---- ld_cmd: A = command.  Waits the few microseconds the WD1793 needs before its status is valid.
ld_cmd	sta	FDCCMD
	ldb	#8
ld_c1	decb
	bne	ld_c1
	rts
; ---- ld_wait: until not busy.  A = status.
ld_wait	lda	FDCCMD
	bita	#$01
	bne	ld_wait
	rts

; ---- ld_raddr: READ ADDRESS (settle first after a seek).  B = sector number, C=1 on error.
ld_raddr
	lda	#$C0
	tst	ld_seek
	beq	ld_ra1
	clr	ld_seek
	lda	#$C4
ld_ra1	bsr	ld_cmd
	ldx	#ld_id
ld_ra2	lda	FDCCMD
	bita	#$02
	bne	ld_ra3
	bita	#$01
	bne	ld_ra2
	bra	ld_ra4
ld_ra3	lda	FDCDAT
	sta	,x+
	cmpx	#ld_id+6
	blo	ld_ra2
ld_ra4	bsr	ld_wait
	anda	#$18			; RNF / CRC
	bne	ld_rae
	ldb	ld_id+2
	andcc	#$FE
	rts
ld_rae	orcc	#1
	rts

; ---- ld_learn: the physical sector order of this track (back-to-back READ ADDRESS until the first sector
; comes round again).  Falls back to 1..18 if anything looks wrong.
ld_learn
	bsr	ld_raddr
	bcs	ld_lf
	stb	ld_phys
	lda	#1
	sta	ld_plen
ld_l1	bsr	ld_raddr
	bcs	ld_lf
	cmpb	ld_phys
	beq	ld_lok
	lda	ld_plen
	cmpa	#18
	bhs	ld_lf
	ldx	#ld_phys
	stb	a,x
	inc	ld_plen
	bra	ld_l1
ld_lok	rts
ld_lf	ldx	#ld_phys		; fallback: logical order
	lda	#1
ld_lf1	sta	,x+
	inca
	cmpa	#19
	blo	ld_lf1
	lda	#18
	sta	ld_plen
	rts

; ---- ld_sync: A = index in ld_phys of the sector whose ID just passed
ld_sync	bsr	ld_raddr
	bcs	ld_sync
	ldx	#ld_phys
	clra
ld_s1	cmpb	a,x
	beq	ld_s2
	inca
	cmpa	ld_plen
	blo	ld_s1
	clra				; unknown sector: start anywhere (only slower)
ld_s2	rts

; ---- ld_read: U = entry (sector, block, page).  Reads the sector into block:page.  C=1 on error.
ld_read	lda	1,u
	sta	$FFA2
	lda	2,u
	clrb
	tfr	d,x
	lda	,u
	sta	FDCSEC
	lda	#$80			; READ SECTOR
	lbsr	ld_cmd
	clrb
ld_r1	lda	FDCCMD
	bita	#$02
	bne	ld_r2
	bita	#$01
	bne	ld_r1
	bra	ld_r3
ld_r2	lda	FDCDAT
	sta	,x+
	decb
	bne	ld_r1
ld_r3	bsr	ld_wait
	anda	#$1C			; RNF / CRC / lost data
	bne	ld_rerr
	tstb
	bne	ld_rerr			; short sector
	andcc	#$FE
	rts
ld_rerr	inc	ld_errs
	lda	ld_errs
	cmpa	#20
	bhs	ld_err
	orcc	#1
	rts

; ---- ld_prog: one sector more on the progress bar (LD_BAR_LEN cells for LD_TOTAL sectors)
ld_prog	ldd	ld_acc
	addd	#LD_BAR_LEN
ld_p1	cmpd	#LD_TOTAL
	blo	ld_p2
	subd	#LD_TOTAL
	pshs	d
	ldx	ld_bar
	ldd	#$20*256+LD_BARATTR
	std	,x++
	stx	ld_bar
	puls	d
	bra	ld_p1
ld_p2	std	ld_acc
	rts

ld_err	clr	DSKREG
	ldx	#$2000+LD_BAR_ROW*80+LD_BAR_COL*2
	ldu	#ld_emsg
ld_e1	lda	,u+
	beq	ld_e2
	sta	,x+
	lda	#LD_ERRATTR
	sta	,x+
	bra	ld_e1
ld_e2	bra	ld_e2
ld_emsg	fcc	"DISK ERROR - CHECK THE DISK"
	fcb	0

ld_dsel	fcb	$01,$02,$04,$40		; drive 0-3 select bits
ld_dreg	fcb	0
ld_onmi	rmb	3
ld_ctrk	fcb	0
ld_seek	fcb	0
ld_cnt	fcb	0
ld_pos	fcb	0
ld_plen	fcb	0
ld_errs	fcb	0
ld_acc	fdb	0
ld_bar	fdb	0
ld_id	rmb	6
ld_phys	rmb	18
ld_need	rmb	19*2
