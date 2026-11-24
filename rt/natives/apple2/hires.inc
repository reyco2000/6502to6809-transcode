; hires.inc - mapping macros for hi-res natives (6502to6809 kit, Apple II source).
; The bitmaps live in Apple RAM blocks BLK_ARAM+1 (page 1, $2000) and BLK_ARAM+2 (page 2, $4000), but the
; transcoded engine code occupies logical $2000-$5FFF.  A native that touches the bitmap maps them in with HRON
; and maps the code back with HROFF.  Both keep every register and CC.  They are empty while XMMU is undefined
; (the differential tests run with an identity map), so the same source is tested and built.
; Rules:
;   * Only code that is NOT in $2000-$5FFF may run between HRON and HROFF: natives sit in the runtime block
;     ($E000) or in code block B ($C000-$DFFF on the 128K target).
;   * Every byte changed in a bitmap must be reported, or the screen will not show it:
;       vm_rect: A = page high byte ($20-$3F or $40-$5F), vr_y0/vr_y1 = lines, vr_c0/vr_c1 = byte columns
;                (inclusive).  One call per native call (the runtime merges rectangles).
;       vm_page: A = page high byte: the whole page.
;   * Report only what really changed if it is cheap to know: every reported pair is reconverted.
HRON	macro
	ifdef	XMMU
	pshs	cc,a
	lda	#BLK_ARAM+1
	sta	$FFA1
	inca
	sta	$FFA2
	puls	cc,a
	endc
	endm
HROFF	macro
	ifdef	XMMU
	pshs	cc,a
	lda	#BLK_EC1
	sta	$FFA1
	lda	#BLK_EC2
	sta	$FFA2
	puls	cc,a
	endc
	endm
