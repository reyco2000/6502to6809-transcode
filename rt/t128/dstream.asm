; 6502to6809 disk streamer (game-independent, part of the resident runtime).  Reads the data file (<loader>.DAT) track by
; track straight from the WD1793 and LZSS-decodes it into its final place: no compressed copy is ever kept in RAM.
;
; Disk layout (kit/fastload512.py, kit/fastload128.py): <loader>.DAT is contiguous in ascending tracks, skipping the directory track 17.
; A "file" is a start position (track, sector) and a list of runs; each run is one LZSS stream (format:
; kit/disk/lzss.py, with its own end marker) for one contiguous destination.  The runs follow each other in
; the data with no gap, so back-references never cross a destination gap.
;
;   ds_file:  fcb track,sector  /  per run: fcb block / fdb $8000+offset  ...  /  fcb 0
;
; Track buffer: the converter's log block (BLK_SH1) at task 1 slot 6 ($C000): sector s of the current track at
; $C000+(s-1)*256; the decoder reads up to $D200, then the next track is read.  Destination: task 1 slots 4-5
; (the run's block and the next one), so a run may cross blocks (offset below $2000).
; Interrupts must be off (polled transfers).  The WD's INTRQ raises an NMI (DSKREG bit 5): $FEFD holds an RTI.
; Each track is read in physical sector order, learnt once with back-to-back READ ADDRESS commands, as in
; loader.asm.  Errors are retried forever (resync on the next ID).
;
; The build supplies DS_TOTAL / DS_BARLEN / DS_BARATTR: boot progress bar (ds_bar <> 0: cell pointer in task 1,
; whose slot 1 then maps the title screen).

DS_REG	equ	$FF40
DS_CMD	equ	$FF48			; command / status
DS_SECR	equ	$FF4A
DS_DAT	equ	$FF4B
DS_STEP	equ	$03			; step rate bits (30 ms, as safe as Disk BASIC)
DS_BEND	equ	$D200			; end of the track buffer

ds_dreg	fcb	0			; DSKREG value: drive select + motor + double density (NMI on)
ds_mot	fcb	0			; 1: motor already spinning
ds_ctrk	fcb	0			; track under the head
ds_trk	fcb	0			; next track to read
ds_sec	fcb	1			; its first sector
ds_x	fdb	DS_BEND			; source pointer between runs
ds_bar	fdb	0			; progress bar: next cell (0 = none)
ds_acc	fdb	0
ds_plen	fcb	0			; physical order known (sectors per track) or 0
ds_seek	fcb	0
ds_cnt	fcb	0
ds_pos	fcb	0
ds_id	rmb	6
ds_phys	rmb	18
ds_need	rmb	19

; ds_load: X = file.  Streams all its runs, then the motor goes off.  Interrupts off.  Ends in task 0, DP = 0.
ds_load	bsr	ds_open
	bsr	ds_runs
; ds_stop: motor off
ds_stop	clr	DS_REG
	clr	ds_mot
	rts
; ds_open: X = file -> X = its run list.  The next read starts at the file's track and sector.
ds_open	ldd	,x++
	sta	ds_trk
	stb	ds_sec
	ldu	#DS_BEND
	stu	ds_x
	rts
; ds_runs: X = run list (ends with 0) -> X after it.  Continues from the current position.
ds_runs	lda	ds_dreg
	sta	DS_REG			; motor on
	tst	ds_mot
	bne	dr_1
	inc	ds_mot
	ldy	#$5000			; spin-up: ~0.15 s (reads that fail while it gets up to speed are retried)
dr_sp	leay	-1,y
	nop
	nop
	bne	dr_sp
dr_1	lda	#VVPAGE
	tfr	a,dp
	setdp	VVPAGE
	lda	#BLK_SH1
	sta	$FFAE
	lda	#1
	sta	$FF91
dr_lp	lda	,x+
	beq	dr_end
	ldy	,x++
	pshs	x
	ldx	ds_x
	bsr	ds_run
	stx	ds_x
	puls	x
	bra	dr_lp
dr_end	lda	#BLK_VID		; task 1 slot 6 back to the converter
	sta	$FFAE
	clr	$FF91
	clra
	tfr	a,dp
	setdp	0
	rts

; ---- ds_run: one LZSS stream.  A = destination block, Y = $8000 + offset, X = source.  DP = VVPAGE, task 1.
; (From the zaxxon cocoport unlz, via a2cc3/runtime/unlz.asm.)
	setdp	VVPAGE
lz_dp	equ	$A1
lz_f	equ	$A2
lz_n	equ	$A3
lz_len	equ	$A4
ds_run	sta	<lz_dp
	sta	$FFAC
	inca
	sta	$FFAD
lz_grp	cmpy	#$B000			; once per group (at most 8*18 bytes: stays below $C000); after a move
				; y >= $9000, so a 4K back-reference stays in slot 4
	blo	lz_g0
	leay	-$2000,y
	inc	<lz_dp
	ldb	<lz_dp
	stb	$FFAC
	incb
	stb	$FFAD
lz_g0	cmpx	#DS_BEND
	blo	lz_g1
	bsr	ds_fill
lz_g1	lda	,x+
	sta	<lz_f
	lda	#8
	sta	<lz_n
lz_item	lsr	<lz_f
	bcc	lz_mt
	cmpx	#DS_BEND		; literal
	blo	lz_l1
	bsr	ds_fill
lz_l1	lda	,x+
	sta	,y+
	dec	<lz_n
	bne	lz_item
	bra	lz_grp
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
	dec	<lz_n
	bne	lz_item
	bra	lz_grp
lz_end	rts
lz_get	cmpx	#DS_BEND
	blo	lz_t1
	bsr	ds_fill
lz_t1	lda	,x+
	rts
	setdp	0

; ---- ds_fill: read sectors ds_sec..18 of track ds_trk into the buffer -> X = first of them.  The next read is
; the next track (skipping 17), from sector 1.  Preserves D, Y, U.
ds_fill	pshs	d,y,u
	lda	ds_trk
	cmpa	ds_ctrk
	beq	df_1
	sta	ds_ctrk			; SEEK
	sta	DS_DAT
	lda	#$10+DS_STEP
	lbsr	ds_cmd
	lbsr	ds_wait
	lda	#1
	sta	ds_seek			; the next READ ADDRESS waits for the head to settle
df_1	ldx	#ds_need		; need[s] = 1 for s = ds_sec..18
	ldb	#1
df_2	clra
	cmpb	ds_sec
	blo	df_3
	inca
df_3	sta	b,x
	incb
	cmpb	#19
	blo	df_2
	lda	#19
	suba	ds_sec
	sta	ds_cnt
	tst	ds_plen			; physical order not learnt yet: learn it on this track
	bne	df_4
	lbsr	ds_learn
df_4	lbsr	ds_sync			; A = position (index in ds_phys) of the sector under the head
	sta	ds_pos
df_5	lda	ds_pos			; next physical sector
	inca
	cmpa	ds_plen
	blo	df_6
	clra
df_6	sta	ds_pos
	ldx	#ds_phys
	ldb	a,x			; sector number
	ldx	#ds_need
	tst	b,x
	beq	df_5			; not needed
	pshs	b
	bsr	ds_read
	puls	b
	bcs	df_4			; error: resync and try again
	ldx	#ds_need
	clr	b,x
	bsr	ds_prog
	dec	ds_cnt
	bne	df_5
	lda	ds_sec			; X = first sector read
	adda	#$BF
	clrb
	tfr	d,x
	lda	#1
	sta	ds_sec
	lda	ds_trk
	inca
	cmpa	#17
	bne	df_7
	inca
df_7	sta	ds_trk
	puls	d,y,u,pc

; ---- ds_read: B = sector -> buffer.  C=1 on error.
ds_read	stb	DS_SECR
	tfr	b,a
	adda	#$BF
	clrb
	tfr	d,x
	lda	#$80			; READ SECTOR
	bsr	ds_cmd
	clrb
dd_1	lda	DS_CMD
	bita	#$02
	bne	dd_2
	bita	#$01
	bne	dd_1
	bra	dd_3
dd_2	lda	DS_DAT
	sta	,x+
	decb
	bne	dd_1
dd_3	bsr	ds_wait
	anda	#$1C			; RNF / CRC / lost data
	bne	dd_e
	tstb
	bne	dd_e			; short sector
	andcc	#$FE
	rts
dd_e	orcc	#1
	rts

; ---- ds_prog: one sector more on the boot progress bar (DS_BARLEN cells for DS_TOTAL sectors)
ds_prog	ldx	ds_bar
	beq	dp_x
	ldd	ds_acc
	addd	#DS_BARLEN
dp_1	cmpd	#DS_TOTAL
	blo	dp_2
	subd	#DS_TOTAL
	pshs	d
	ldd	#$20*256+DS_BARATTR
	std	,x++
	puls	d
	bra	dp_1
dp_2	std	ds_acc
	stx	ds_bar
dp_x	rts

; ---- ds_cmd: A = command.  Waits the few microseconds the WD1793 needs before its status is valid.
ds_cmd	sta	DS_CMD
	ldb	#8
dc_1	decb
	bne	dc_1
	rts
; ---- ds_wait: until not busy.  A = status.
ds_wait	lda	DS_CMD
	bita	#$01
	bne	ds_wait
	rts

; ---- ds_raddr: READ ADDRESS (settle first after a seek).  B = sector number, C=1 on error.
ds_raddr
	lda	#$C0
	tst	ds_seek
	beq	da_1
	clr	ds_seek
	lda	#$C4
da_1	bsr	ds_cmd
	ldx	#ds_id
da_2	lda	DS_CMD
	bita	#$02
	bne	da_3
	bita	#$01
	bne	da_2
	bra	da_4
da_3	lda	DS_DAT
	sta	,x+
	cmpx	#ds_id+6
	blo	da_2
da_4	bsr	ds_wait
	anda	#$18			; RNF / CRC
	bne	dd_e
	ldb	ds_id+2
	andcc	#$FE
	rts

; ---- ds_learn: the physical sector order of this track (back-to-back READ ADDRESS until the first sector comes
; round again).  Falls back to 1..18 if anything looks wrong.
ds_learn
	bsr	ds_raddr
	bcs	dl_f
	stb	ds_phys
	lda	#1
	sta	ds_plen
dl_1	bsr	ds_raddr
	bcs	dl_f
	cmpb	ds_phys
	beq	dl_x
	lda	ds_plen
	cmpa	#18
	bhs	dl_f
	ldx	#ds_phys
	stb	a,x
	inc	ds_plen
	bra	dl_1
dl_x	rts
dl_f	ldx	#ds_phys		; fallback: logical order
	lda	#1
dl_f1	sta	,x+
	inca
	cmpa	#19
	blo	dl_f1
	lda	#18
	sta	ds_plen
	rts

; ---- ds_sync: A = index in ds_phys of the sector whose ID just passed
ds_sync	bsr	ds_raddr
	bcs	ds_sync
	ldx	#ds_phys
	clra
dy_1	cmpb	a,x
	beq	dy_2
	inca
	cmpa	ds_plen
	blo	dy_1
	clra				; unknown sector: start anywhere (only slower)
dy_2	rts
