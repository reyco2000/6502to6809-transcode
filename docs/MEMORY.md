# CoCo 3 memory maps (512K and 128K targets)

Both targets keep Apple RAM `$0000-$BFFF` at the same logical addresses. Zero page is DP 0, and the 6502 stack page is `$0100` (the 6809 S lives there too). The GIME MMU maps 8K blocks into eight slots, and the build asserts every budget.

## Logical map while the game runs (task 0)

| slot | logical | contents |
|---|---|---|
| 0 | `$0000-$1FFF` | Apple RAM |
| 1-2 | `$2000-$5FFF` | Transcoded **engine code** (two 8K blocks). Between `HRON`/`HROFF` (natives) this window shows the hi-res **bitmaps** instead. |
| 3-5 | `$6000-$BFFF` | Apple RAM |
| 6 | `$C000-$DFFF` | Overlay code. 512K: the current image's own code bank. 128K: code block B, with the level code at `OVORG` after the engine's tail, and the port's block-B natives (`NATIVE_SRC_B`) and tables from `BLOCKB_TOP`. |
| 7 | `$E000-$FDFF` | Resident runtime and the port's natives; the `$FExx` constant page holds vectors and converter variables. |

Task 1 is used by the video converter: Apple page, GIME window, tables, converter code.

## 512K target (`kit/build512.py`, `rt/t512/`)

| blocks | contents |
|---|---|
| `$00-$03`, `$04-$07` | Two GIME buffers (320×192×16): convert into the hidden one, then flip. Smooth. |
| `$08-$09` | Shadows of the two Apple pages (what each GIME buffer shows) |
| `$0A-$0B` | Conversion tables TA/TB |
| `$10-$15` | Apple RAM (`$11`/`$12` = hi-res bitmaps) |
| `$16-$17` | Engine code |
| `$18-…` | One code bank per image (BASE + STAGES) |
| `$1E` | Video converter + row table |
| `$20-$36` | Compressed data: tables, converter, engine, banks and boot RAM, unpacked at start; levels stay packed until loaded |
| `$37` | Resident runtime |
| `$38-$3B` | Staging for level loads |

Load: `<loader>.BIN` (fast loader) reads `<loader>.DAT` into the data blocks. The runtime unpacks the data and starts the game. Level loads unpack from RAM.

## 128K target (`kit/build128.py`, `rt/t128/`)
16 blocks (`$30-$3F`). The same block numbers exist on a 512K machine, so a 128K disk also runs there.

| block(s) | contents |
|---|---|
| `$30-$33` | The single GIME buffer; the converter sits in the tail of `$33` |
| `$34` | Log block: staged commit log, and the disk track buffer while loading |
| `$35`/`$36` | TA/TB |
| `$37`/`$38` | Engine code; the level code at `OVORG`; block-B natives and tables from `BLOCKB_TOP` |
| `$39-$3E` | Apple RAM |
| `$3F` | Runtime + disk streamer |

How the single buffer stays smooth:
- There is no shadow. The reference is the page converted last (`v_ref`).
- Converted pairs go into a log.
- The log is committed in one `PULU`/`PSHS` burst, timed to the beam: from VSYNC for mid-size logs, from the end of the VBL window for big ones.
- Overlapping rectangles are merged, so no pair is converted twice.

How data gets in:
- Nothing is kept compressed in RAM.
- The boot streams the boot image from disk.
- A level load streams that level's pages and code while the screen keeps the last frame.
- A call stage (a mid-game load the game's loader returns from) streams exactly the bytes the loader wrote (not the stack page) and the stage's code at `OVORG`. The caller's state is kept, and the screen keeps the last frame (interrupts off, about 1.7 s for 15K).
- A reboot streams the boot image again.

Costs:
- Level transitions take about 1.5-2.5 s against about 1.4 s on 512K.
- The code budget is tighter: engine + largest level code + native tables must fit in `$37`/`$38` (16K).

## Budgets to watch
- **Engine:** ≤ 16K (`$2000-$5FFF`).
  - 512K: each image's code ≤ 8K.
  - 128K: engine tail + largest level code + block-B natives + `BLOCKB_RESERVE` ≤ 8K in block B.
- **Runtime + natives:** ≤ `$FE00`. Natives grow the runtime block, so on 128K move big tables or whole natives (`NATIVE_SRC_B`) to block B.
- **Converter:** ≤ 2K on 128K (tail of the last GIME block).
- **Disk:** 35 tracks × 18 sectors of RS-DOS; the build reports free granules. A bigger game needs a second disk or a better compressor.
