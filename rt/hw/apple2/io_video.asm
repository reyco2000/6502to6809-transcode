; io_video.asm - Apple II video-mode switches and IIe status reads (6502to6809 kit, hardware map: kit/hw/apple2.py).
; Appended to the runtime by the build only when the generated code uses one of these handlers
; (map entries with Runtime(cond='HW_IOV')), so ports that never touch them keep their exact bytes.
;
; The mode is tracked, not shown: the converter still shows full-screen hi-res (page v_dpage).
; Uses from the runtime: IOV (handler value), v_dpage (0/1), vbl_over (set once this frame's blank has passed).
; Every handler preserves A, B, X, U and CC.

VM_TEXT		equ	$01
VM_MIXED	equ	$02
VM_LORES	equ	$04

v_mode	fcb	0			; Apple display mode bits (0 = full-screen hi-res)

io_rd_C050				; TXTCLR
io_wr_C050
	pshs	cc,a
	lda	#^VM_TEXT
	bra	vm_clr
io_rd_C051				; TXTSET
io_wr_C051
	pshs	cc,a
	lda	#VM_TEXT
	bra	vm_set
io_rd_C052				; MIXCLR
io_wr_C052
	pshs	cc,a
	lda	#^VM_MIXED
	bra	vm_clr
io_rd_C053				; MIXSET
io_wr_C053
	pshs	cc,a
	lda	#VM_MIXED
	bra	vm_set
io_rd_C056				; LORES
io_wr_C056
	pshs	cc,a
	lda	#VM_LORES
	bra	vm_set
io_rd_C057				; HIRES
io_wr_C057
	pshs	cc,a
	lda	#^VM_LORES
vm_clr	anda	v_mode
	bra	vm_st
vm_set	ora	v_mode
vm_st	sta	v_mode
	clr	IOV
	puls	cc,a,pc

io_rd_C01A				; RDTEXT: bit 7 = text mode
	pshs	cc,a
	lda	#VM_TEXT
	bra	vm_rd
io_rd_C01B				; RDMIXED: bit 7 = mixed mode
	pshs	cc,a
	lda	#VM_MIXED
vm_rd	anda	v_mode
	beq	vm_r0
vm_r1	lda	#$80
vm_r0	sta	IOV
	puls	cc,a,pc
io_rd_C01D				; RDHIRES: bit 7 = hi-res (not lo-res)
	pshs	cc,a
	lda	v_mode
	anda	#VM_LORES
	beq	vm_r1
	clra
	bra	vm_r0
io_rd_C01C				; RDPAGE2: bit 7 = page 2 shown
	pshs	cc,a
	lda	v_dpage
	beq	vm_r0
	bra	vm_r1

io_rd_C019				; RDVBL (IIe): bit 7 = 0 inside vertical blank, the same window rt_vbl uses
	pshs	cc,a
	lda	$FF92			; GIME timer flag: the blank window of this frame has passed
	anda	#$20
	ora	vbl_over
	sta	vbl_over
	bne	vm_r1
	bra	vm_r0
