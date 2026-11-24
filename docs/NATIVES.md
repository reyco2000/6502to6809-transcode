# Runtime services and native routines

## 1. What the runtime already provides (Apple II source; `rt/t512/rt.asm`, `rt/t128/rt.asm`)
Translated code reaches these through the I/O handlers `xc65` emits for `$C0xx` accesses. A port only writes natives for what this table does not cover.

| Apple | runtime | behaviour |
|---|---|---|
| `$C000` KBD | `io_rd_C000`, or the latch `kb_kbd` (`IO_MEM`) | The IRQ scans the CoCo matrix at 60 Hz into an Apple ASCII latch with bit 7 = strobe. A held key auto-repeats like a IIe (`KB_DELAY` 32 ticks, then `KB_RATE` 4). Arrows map to Apple `$88`/`$95`/`$8B`/`$8A`, BREAK to ESC, and CTRL + letter to a control code. The table is `kb_tab`. |
| `$C010` KBDSTRB | `io_rd_C010`, `io_wr_C010` | Clears the strobe; a read returns bit 7 = any key down (IIe). |
| `$C030` SPKR | `io_rd_C030`, `io_wr_C030` | Toggles the 6-bit DAC between its extremes (a click). The sound mux is set to the DAC at start. |
| `$C054`/`$C055` page 1/2 | `io_*_C054`/`C055` → `v_flip` | Selects the page, converts its dirty rectangles and shows it; counts `v_flips`. |
| `$C061`/`$C062` buttons, `$C061,X` | `io_rd_C061/2`, `io_rdx_C061`, latches `btn0`/`btn1` | CoCo right-joystick buttons in bit 7. |
| paddles | `joy_x`, `joy_y` (0..63) | The right stick, read by the IRQ every 2nd tick. An axis reads 32 (centre) until it has moved once (`joy_seen`). The read restores the DAC level, so sound and stick share the DAC. |
| `$C019` RDVBL, frame waits | `rt_vbl` | Converts the shown page if it was drawn on, then returns at once inside vertical blank (GIME timer window) or waits for the next 60 Hz tick. |
| other `$C0xx` | `io_defaults` (build) | A read gives 0, a write does nothing. Check the survey: any of these that matter need a native. |

Other services:
- **Memory.** `hr_on`/`hr_off` (bitmaps at `$2000`/`$4000`), `rt_bank` (code block in slot 6).
- **Video.** `vm_rect`/`vm_page` (dirty marking), `vscroll` (scroll fast path), `v_redraw` and, on 128K, `v_inval`.
- **Data.** `unlz` (LZSS).
- **Errors.** `x_trap`: red border and halt. The address after the `JSR` holds the 6502 address of a transfer that was not translated.
- **Disk.** The fast loader: `<loader>.BIN`, a title screen, a progress bar, and direct WD1793 reads in physical sector order. On 128K, `ds_load` (`dstream.asm`) streams level files from disk.

## 2. Native contract
- **Registers.** A = A, B = X, `RY` = Y (with `RYW` = 0:Y), S = 6502 stack, DP = 0.
- **Exit state.** Produce exactly the registers and N/Z/C flags the callers read: the "must produce" column of `report.txt`, derived by `xc65.routine_contract` from the 6502 code you replace. Changing other registers is allowed (they're dead).
- **Zero page and RAM.** Leave every byte the 6502 routine writes with the 6502's value, scratch included, unless you can show no caller reads it. The differential test compares all of RAM except the stack page.
- **Self-modifying bytes.** If the 6502 routine patches its own code, the native need not write those bytes (list them in `SMC_BYTES`), as long as nothing reads them as data.
- **Bitmap access.** `HRON` … `HROFF` (`rt/natives/apple2/hires.inc`); no code from `$2000-$5FFF` may run in between. Report changes with `vm_rect` (A = page high byte, `vr_y0`/`vr_y1`/`vr_c0`/`vr_c1`) or `vm_page`.
- **Pointer stores into ROM.** The Apple drops writes at `$C100` and above, and games use that as a sink (for example a save buffer at `$F000`). On the CoCo those addresses are the runtime, so a native must drop such stores too: a checked loop when the pointer's high byte can reach `$C0`. The native test catches a missed one when it uses `reference(..., rom_writes='ignore')`.
- **Continuing into translated code.** A native that falls back into translated code ends with `jmp E<addr>` and lists `<addr>` in `NATIVE_EXITS`.
- **Natives that never return** (level loads, reboot) go in `NATIVE_CONTRACTS` with `noreturn=True`.

## 3. Catalogue of native patterns (templates in `rt/natives/apple2/`)

| pattern | typical 6502 | 6809 technique | template / example |
|---|---|---|---|
| sprite blitter | shift a shape by 0-6 dots, OR/AND/store into rows via a row table, clip | Pre-shifted tables (`LSLB`/`LDD B,Y`) or `MUL` by 2^(s+1). The mode (OR/AND/store) patched into one opcode. Inline the row pointer (`NBROW`). Map the bitmaps (`HRON`) only for visible draws. | Karateka `ports/karateka/src/t512/hires.asm` `nb_*` |
| fill / clear | rectangle of a byte pattern | Jump into an unrolled `STD ,U++` run | `fill_template.asm`; Karateka `nh_fill` |
| scroll / page copy | copy lines between pages | Compare before copying (skip identical lines). Tell the converter which lines moved (`vscroll`, `vr_rflag`): the GIME rows are moved with `PULU`/`PSHS` instead of reconverted. | Karateka `nh_scroll` |
| VBL wait | `LDA $C019 / BMI` | `jsr rt_vbl` | `vbl.asm` |
| monitor `WAIT` | `JSR $FCA8` | the same formula in CoCo cycles | `wait.asm` |
| paddles | `PREAD` or a `$C070`/`$C064` count loop | Use `joy_x`/`joy_y`. Better: compute the value the game derives from the count (a class, a position). | `paddle.asm` |
| tones | cycle-counted `$C030` loops, multi-voice players | The same period structure in Apple time; click with `$FF20 EOR #$FC`; poll keys where the 6502 did | `speaker.asm`; Karateka `sound.asm` |
| level loader | RWTS + relocator + `JMP` into the level | `nt_load`: B = level → restore the level's pages, enter its snapshot state (`<IMAGE>_START`) | scaffold `src/t512|t128/start.asm` |
| reboot | `JMP $C600`, game over | `nt_reboot` → `rt_start` (512K), or stream the boot image again (128K) | scaffold `start.asm` |

## 4. Testing natives
A port's `test_native.py` (model: `ports/karateka/test_native.py`) assembles the natives with `XMMU` undefined, so the bitmaps sit identity-mapped at `$2000`/`$4000`. It then runs each native in `cc3sim` and the 6502 original on `kit/host/cpu6502` on the same random inputs, taken from snapshot state, and compares registers, flags and RAM. It prints the cost ratio, where 1.75 is real time.
- Randomize every input the routine reads, including modes, shifts and clip edges.
- Keep the test count high (1,500+) before optimizing, and re-run after every speed change.
