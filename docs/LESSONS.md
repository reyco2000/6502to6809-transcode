# Lessons from real ports

Each entry is a mistake that cost time in a real port, and the check that avoids it. Add one per new pitfall.

## Measuring
- **Compare the same scene.** Before comparing speed, check with screenshots that both machines show the same scene. For CPU-bound scenes, compare CPU work per frame (for example sprite draws per frame) rather than flips: the content of a fight or level varies between runs. (In one port, a "81% speed" figure came from the host Apple standing idle while the CoCo was fighting; the real figure was 104%.)
- **Check the original before "fixing".** Odd frames, wipes and jitter may be in the original game. Run the host Apple model, or MAME `apple2e`, at the same moment first.
- **MAME's windowed mode shows false jitter.** Judge smoothness full screen, or on XRoar or real hardware.
- **Polling loops look hot** in a profile. Separate waiting from work before optimizing.

## Correctness
- **Disassemblies are not code maps.** Listings mark executed code as data. Use coverage + recursive descent.
- **Bit-exact tests catch edge cases.** Random tests found a screen byte entering with bit 7 set, a signed `b,x` offset beyond 127, and lines ≥128 written in front of a buffer. Keep the tests after every speed change.
- **`pshs cc,d` / `puls d`** pulls CC into A. Pair pushes and pulls exactly, or interrupts stay masked and the machine hangs.
- **Direct-page misassembly.** After `setdp X`, lwasm assembles a variable in page X as direct. If DP is data at that moment (block copies with DP in `PULU`), force extended addressing with `>`.
- **Mid-instruction entries.** A `JMP` into the middle of an instruction (for example a reboot path) becomes a trap. Make it a native.
- **Loader stacks.** A level can be entered through `TXS`/`RTI` with return frames left on the stack. `snapshot.ret_chain` records them, and the level's `<IMAGE>_START` pushes their native labels.

## Hardware and emulators
- **MAME joystick.** MAME leaves an untouched stick axis at 0 (full up/left), so treat an axis as centred until it has moved once.
- **MAME MMU read-back.** MAME returns MMU registers with floating-bus bits 6-7. A probe that remaps a slot must restore the value `AND $3F`.
- **MAME config is sticky.** A Lua probe that changes a CONFIG field (monitor type) writes `cfg/`; clean it up.
- **Lua write taps on GIME registers** are dropped by MMU remaps. Measure timing with debugger breakpoints (`frame`/`beamy`) instead.
- **Sound and joystick share the DAC.** The joystick read must restore the DAC level, or the speaker clicks go silent or buzz.

## Video
- **One GIME buffer tears.** A conversion that takes several frames while the beam scans shows half-old, half-new pictures. Log the changes and commit them in one burst timed to the beam; a shadow buffer alone doesn't help.
- **Many tiny rectangles overflow the dirty list**, and the whole page then gets converted every frame. Merge vertically adjacent and contained rectangles, and keep a fast path for the most recent entry.
- **Reconvert neighbours only when their table index changes.** The artifact colour of a pair depends on bit 0 of the next byte and bits 6-7 of the previous one.
- **Scrolls:** move GIME rows (`PULU`/`PSHS`, about 4 cycles per byte) instead of reconverting, and skip lines that are already equal.

## Disk and loading
- **DSKCON costs about one revolution per call.** For bulk loads, drive the WD1793 directly: read sectors in physical order (learn the interleave once with READ ADDRESS) and resync per track.
- **Restore only the pages a level load writes** (the snapshot `.mask`). The other pages hold state that carries over between levels.

## Found by the Zaxxon port (2026-11-19); kit fixes in place
- **Draft CODE_RANGES.** The survey dropped a page run that started in the stack page. It now clips ranges (`clip_ranges`).
- **Disassembly from a snapshot.** Traced from its first byte, a 64K dump stays mostly data. `kit.listing --a2dis` seeds the `a2-disassemble` tool with every executed entry from the profile.
- **Mid-game loaders that return** (overlay segments): call stages.
  - Snapshot with `record_writes(m, call=True)`.
  - The image is BASE plus the loaded bytes.
  - The blob is full pages plus byte runs, loaded by `nt_ovl` (512K only; still to be written).
  - The mask used to pick up the game's writes during the `ret_chain` run; it is now frozen for call stages.
- **Boot-only profile images** became roots. They are skipped now (cold snapshot `boot_frame`). SMC (volatile) bytes are ignored when matching images.
- **Reaching late game phases.** Profile scenarios take `pokes` (cheats such as infinite lives), only to reach those phases.
- **New translations:** `(zp,X)`, operand SMC on abs/abs,X/abs,Y (`xsm*` helpers), `SMC_JMP` (patched JMP targets), `SMC_OPS` (opcode variants), and BVC/BVS right after ADC/SBC.
- **Dead paths.** `TRAPS` takes bare or per-image entries, `gen` suggests candidates, and a trap on executed code is refused. The always-taken-branch check looks back across stores.
- **Translated code touching hi-res** (512K engine window `$2000-$5FFF`): `HIRES_ACCESS` routes through `hr_rd`/`hr_wr` with whole-page dirty marks. Slow, so hot routines should still become natives.
- **ROM read as data:** `ROMDATA` tables built from the user's ROM, and `xrd` for pointer reads into ROM.
- **difftest:** random inputs can run the 6502 into data; those cases are counted as `illegal`, not as failures.
- **More Zaxxon findings (2026-11-19):**
  - Random difftest inputs send pointers into ROM or I/O. The 6502 reference now reports those cases as `wild` (a ROM read the port does not model, any write at `$C100+`, and indirect reads of `$C0xx`) and skips them.
  - zp,X / zp,Y instructions the profile saw wrap past `$FF` get a wrap-safe sequence. Zaxxon's `$AE3D` clears all of zero page by wrapping; unwrapped, the translation would have zeroed the 6809 stack page.
  - SMC_OPS sites read their operand from memory, and the image's own opcode is added as a variant. Patch code often changes the operand too, and the original opcode may run before the first patch.
  - The always-taken-branch check now looks back across stores. The reference port's engine lost three dead instructions; its disks differ only by that (verified by reverting the check: byte-identical), and engine and native tests pass 300/300.
  - Translated hi-res access works (Zaxxon plays in MAME) but costs about 3x. The trace shows `hr_wr` + whole-page reconversion dominate, so natives with `vm_rect` remain the way to speed.
- **Hi-res natives (Zaxxon `$0AA5`/`$0A33`/`$0959`/`$181D`, 2026-11-19):**
  - **ROM as a write sink.** Apple games point a buffer into ROM on purpose: Zaxxon's "no save-under" descriptor is `$F000`, and the Apple drops the writes. On the CoCo, `$C000-$FFFF` holds the runtime and code banks, so a native that copies the 6502's `STA (zp),Y` overwrote the runtime and the game crashed at the first wall.
    - **Natives:** guard pointer stores whose pointer can reach `$C000` (a checked loop when the high byte is `$BF` or more).
    - **Runtime:** `hr_wr` drops `$C000+` the same way.
    - **Translator:** sends indirect stores the profile saw touching ROM (`ea` 64) through `hr_wr`.
  - **Native tests must not skip ROM writes.** The native test had counted such cases as `wild` and skipped them, which hid the bug.
    - `reference(..., rom_writes='ignore')` records the dropped writes.
    - The harness dumps its code area, and `compare` fails when the code under test wrote there.
    - Add a generator case that puts the game's real sink pointers in.
  - **Path coverage of native tests.** `difftest.untested(coverage, ranges, pcs, mems)` lists the profiled instructions of the replaced ranges that no case reached (pass `pcs=` to `reference`). Aim for 0: each exit, mode and clip path.
  - **Shared exits.** A replaced routine may end on an RTS outside its range (`$0959` exits through the RTS at `$093B`). The test adds that address to the reference's `inside` set.
  - **Byte loops that index with `B,X` are signed on the 6809.** To match `(zp),Y` for every Y, bias the pointer by `+$80` and count `B' = Y eor $80`. The compare value gets the same `eor`, and BPL/BMI on `CMPB` stay identical.
  - **Engine tests: zero-page wraps from random inputs.** Random zero-page bytes give `zp,X` indexes past `$FF` at sites the profile never saw wrap. The reference now stops those cases as `zpwrap` (`wrapsafe=` the profile's wrap sites) instead of reporting a failure.
  - **Measuring speed.** A 60-frame trace that falls on a death or sound sequence is dominated by the game's calibrated delay loops (88% in Zaxxon's click delay), which are faithful timing. Trace a plain flying window. MAME runs with different probe scripts can diverge in timing, so compare windows taken with the same script.
- **Dirty rectangles and patched natives (Zaxxon `$0E23` line plotter, 2026-11-25):**
  - **Bit-exact is not enough for hi-res natives.** The native tests stubbed `vm_rect` with an RTS, so a rectangle that misses a changed byte passed, and the real converter keeps the stale pixels on screen. `difftest.RECT_LINES` (put it in the harness's `extra_lines`: a logging `vm_rect`/`vm_page` plus `vr_y0`..`vr_c1`) and `difftest.rect_misses(before, got, harness)` list every changed `$2000-$5FFF` byte that no logged rectangle covers. Report misses separately from register/RAM diffs. The first run found a one-column miss in the new native and misses in four older ones (rows wider than column 39 wrap to line + 64, column 0).
  - **One rectangle per call, from the call's inputs.** A hot per-point routine called in a line loop should not call `vm_rect` per point. Compute one rectangle per line from the loop's ends, provided the tables make that safe (here: line = row index − 64, column table monotonic). Check every index adjustment for both directions: a stub that clamps X from `$88` to `$87` moves the low end down by one, not up.
  - **A rectangle that is too big costs as much as the old whole-page mark.** The first version marked all 40 columns whenever X wrapped below 0, which the game's lines always do: video stayed at 50%. Count the rectangle branches in the trace (`kit.mame.profile -n 400`) to catch it.
  - **Opcode SMC inside a native.** When the 6502 patches ORA/AND opcodes and EOR/LDA operands outside the hot loop, the native can read the patched bytes once per call and patch its own `ORA ,Y`/`ANDA ,Y` (`$AA`/`$A4`) and immediates. Opcodes the profile never saw go to `x_trap`. Take the sites out of `SMC_OPS`/`SMC_IMM`, because the range is no longer translated.
  - **Flags of a loop's last call.** When only the last call's N/Z/C reach the caller, rebuild them once at the end from the state the 6502 leaves (the routine's own X/Y save bytes) instead of setting them on every exit of the hot path.
  - **Rows past column 39 (Zaxxon `$0A33`/`$0959`: no right clip).** On the Apple, bytes past column 39 of lines 0-127 land at line + 64, columns 0+; for lines 128-191, columns 40-47 are the screen holes and anything beyond is another line group. The shared exit now reports a second rectangle for the wrapped part and falls back to `vm_page` for anything odder (width 0, start column past 39, last column past 79, or past 47 on lines 128+). Neither fires in real play (trace), so it costs nothing.
  - **False "wrote ROM" failures.** The ROM-write check compared the whole code area with the image, but natives keep their variables and self-patched operands there, so a 6502 ROM write that landed on such an address failed the case. Now `RefBus.romw` records values, and a byte counts only when it holds the value the 6502 wrote and is not one the code writes by name (`Harness.owned`: `sta zv`, `sta lbl+1`, …; a leaked pointer store is always indexed). A mutation test (guard removed) still fails 21 of 600 cases.
  - **Generators must respect the callers' entry state.** Random P with a random A gave Zaxxon's `$181D` an impossible entry (Z = 1 with A ≠ 0; every caller enters right after an LDA), and 42 false rectangle misses. A generator can return `'p'` (and `'a'`, `'x'`, `'y'`) to fix the entry state. Check the listing's callers before trusting a miss.
  - **Hot per-point natives (Zaxxon `zp_line`: 131 → 58 cycles a point, the 6502's 66).**
    - Run stretches with register pointers and skip the per-point scratch stores.
    - Replay the stretch's last point through the exact slow routine. ORA/AND/store twice equals once, so the replay sets zero page and the save bytes exactly at no visual cost. Keep any store the 6502 makes only on some paths (the last on-screen point's high byte), since the replayed point may not take that path.
    - Skip runs of points that only RTS in one step.
  - **Measure a native like for like with the harness, not MAME flips.** Scenes differ from run to run (flips varied 31-63 per 600 frames), but `Harness.run(...)['cycles']` and `reference(...)['cycles']` on the same generated cases give CoCo cycles per Apple cycle (1.75 = Apple speed) for the old native, the new native and the 6502.
- **Video converter work (Zaxxon, 2026-11-25):**
  - **Measure the converter offline.** In-game traces of different builds show different scenes: per-line and per-pair numbers moved the wrong way in MAME, while every scene kind got cheaper offline. `kit.vidtest` runs `vc_conv` in cc3sim on generated scenes, checks it against `invcheck512.convert`, and reports cycles per changed pair and per rectangle line. `--vid OLD.asm` compares versions on identical scenes. A mutation (left-neighbour redo disabled) fails 7 of 20 cases.
  - **Neighbours need half a conversion.** A changed pair's left neighbour depends on it only through its TB half (pixels 7-13), the right neighbour only through its TA half (pixels 0-6). GIME byte 3 is split exactly (TA high nibble, TB low nibble), so `vc_cvtb`/`vc_cvta` redo one table and keep the other nibble.
  - **Small loop changes.** The rectangle loop ends on a rowtab end pointer, and conversions of pairs known not to be first skip the first-pair test.
  - **Result:** −7.7% converter cycles on the same 60 scenes. MAME invariant probe (Zaxxon): 87 samples, 0 inconsistent.
  - **Wide rectangles cost scanning, not conversion.** A diagonal line's bounding box is mostly unchanged bytes (about 10 words scanned per converted pair in Zaxxon). Reporting the line in 32-line bands, with columns rounded out to 4 so that parallel lines repeat the same rectangles (`vm_rect` drops repeats, and the list didn't overflow), cut the in-game scan cost per converted pair by 25%.
- **Narrow blitters are row-overhead bound (Zaxxon `$0AA5`/`$0959`/`$0A33`, 2026-11-25).** Game sprites are 2-6 bytes wide, so a faithful per-row translation (re-deriving every pointer from zero page, three 16-bit zero-page adds, clip tests that never change within a call) cost ~285 cycles a row to copy ~2 bytes. The fix:
  - Do the per-call-constant tests once, on the first row.
  - Keep only the pointers a mode reads in registers or variables (`ABX` for the step).
  - Read the row table through the row index, so it wraps like the 6502's X.
  - Set the zero-page pointers once at the exit, from the number of rows done: initial + n × step. The carry of the last add is "new low byte < step".
  - Store per row only what an exit can expose: on an off-screen row the 6502 reloads Y from its column byte.
  - Result: −17%, −13% and −9% CPU cycles on identical cases (a harness benchmark of the old natives vs the new vs the 6502).
- **Height 0 means 256 rows,** and the row index then wraps past 255. A dirty-rectangle exit that takes lines from the first and last row index must report every line when the end index is ≤ the start index.
- **Per-call setup and masked rows, second round (Zaxxon blitters, 2026-11-25).**
  - **Read 6502 (zp),Y pointer pairs with `LDX B,R` / `STX <lo`.** LDX loads the byte at ptr + Y as the high half, so STX to the zero-page pair writes lo then hi in the 6502's order: one read and one store per pair, against two pointer rebuilds (PSHS, TFR, ABX) a byte. Bias the base by +$80 and index with B = Y eor $80 so a signed B reaches ptr + 0..$FE. Y = $FF would read the second byte from ptr + $100, not ptr + 0, so check that the index table can't hold $FF.
  - **Streams that move together are one pointer.** When the source, mask and save pointers all advance by the same step a row, their differences are constant for the call. An unrolled row body (one block per byte, entered at the width through an address table) reads the source with 5-bit offsets and the mask with a 16-bit offset from the source register, patched once a call by an unrolled `STD`/`SUBD #1` chain entered at the width. Masked cost per byte went from 44 cycles (two LDX reloads, DECB/CMPB/BPL) to 28.
  - **Guard the fast path per call, not per byte.** Check the save-sink condition (every row's save pointer below the sink) for the whole call from the last row's address, and otherwise fall back to the checked per-row loop.
  - Result on identical cases (CoCo cycles per Apple cycle, 1.75 = Apple speed): `$0AA5` 1.72 → 1.35, `$0959` 1.82 → 1.71, `$0A33` 2.00 → 1.93.
- **Count loop use in game before unrolling, and watch the runtime size.** Entry counts of each loop label in the raw MAME trace give rows and bytes per loop. In Zaxxon's third blitter round only one loop of four was worth its bytes (55K of 3.5M cycles; the rest 2-6K each). Unrolling all four overran `RT_LIMIT`; the build failed and the old disk stayed, so a trace then mixed old code with the new listing's labels. Confirm the build succeeded, and that the traced code matches the listing, before reading a profile. Identical unrolled bodies can be shared through a patched return `JMP` (4 cycles a row) to save space.
- **128K: the runtime block fills first (Zaxxon, 2026-11-25).** A port whose natives fit 512K's runtime block can be 1.5K over `RT_LIMIT` on 128K, where the same block also holds the disk streamer. Code block B is mapped at `$C000` all the time and stage loads write only `OVORG` up to `BLOCKB_TOP`, so whole natives can live there: list them in `NATIVE_SRC_B(ram)`. The build sizes `BLOCKB_TOP` from them and streams them in with the boot file. Move routines that are big but run less often per frame (Zaxxon: the line plotter, the bar and the restore blitter, 1.7K). Check the largest stage still fits: the build asserts it.
- **Translated hi-res access on 128K.** `hr_rd`/`hr_wr`/`hr_flush` exist in both runtimes (`HIRES_ACCESS`). A port that stubbed them to get a 128K dry run through must drop the stubs.
- **`kit/asm.py` maps lwasm's line numbers to source lines.** Callers may pass entries that hold several lines (`'\tfcb\t1\n\tfdb\t2'`), so the driver splits them first. Before, a real error (a far branch in a macro, say) could show up as an `IndexError` in the driver.
- **Call stages on 128K (Zaxxon segment loads, 2026-11-25).**
  - The build streams exactly the bytes the loader wrote. The rest of RAM is live game state, so whole pages would be wrong.
  - Leave out the stack page: the loader's frames are dead once it returns, and the game's frames and the native's own frame live there.
  - Split runs at the streamer's two-block destination window: a 15.6K segment run from `$7100` crosses three blocks.
  - `nt_ovl` runs `ds_load` on a runtime stack (the streamer runs in task 1, which maps other RAM at `$0000`), then `v_inval`. The screen keeps the last frame.
- **Translator: a pushed constant return address (`LDA #hi / PHA / LDA #lo / PHA`, then JMP).** Zaxxon's seg1 code pushed two returns into resident code, then jumped to the segment loader, whose RTS went to the first of them. Pushed as raw 6502 bytes, the 6809 RTS read them as an address in the wrong byte order and with no +1, and the machine crashed after the load. `xc65` now emits `LDX #label(hi:lo + 1) / PSHS X` for each such group whose target is code and that has no entry inside. A and N/Z still come from `LDA #lo`, and the stack depth is the same. Code that later reads such bytes with PLA would break; the profile's `rts_pha` class is the place to look when this matters.
- **Exercise every load in MAME, not only the short play script.** Zaxxon's 6000-frame script never reached a segment load, on either target, until the call stages had been "working" for weeks. Use a long input with an infinite-lives poke (the snapshot scenario's), and log `nt_ovl`/`nt_load` with debugger breakpoints (`bpset addr,1,{printf "OVL %d",b;g}`, read from `manager.machine.debugger.consolelog`). On 128K the IRQ tick stops during a load, which shows how long it takes.
- **Forcing a load mid-frame is not a test.** A real loader also overwrites its own buffers, and in Zaxxon those overlap game variables. The game re-initializes them after its real loads, but not after one forced at an arbitrary instruction, so the screen fills with garbage on both targets. Force the game's own decision branch or play to the load.
- **`inv128.lua` with `HIRES_ACCESS`:** pass `HRD` (the address of `hr_d1`). A page written by `hr_wr` is marked dirty only at the next flip or VBL, so without it the probe reports a few stale bytes.
- **Translated hi-res writes: report a box, not the page (Zaxxon 128K, 2026-11-25).** `hr_wr` used to mark the whole page, and `hr_flush` turned that into `vm_page`. One translated copy loop (about 48 bytes a frame) then made every conversion scan all 192 lines: about 95K instructions a flip for about 430 changed pairs. `hr_wr` now keeps a bounding box per page, with the line and column decoded from the address (the 8 unshown bytes of each 128 are skipped), and `hr_flush` reports it with `vm_rect`. 128K went from 215 to 293 flips in 6000 frames (more than 2x in long play), and the 128K invariant probe (which compares the Apple page) found 0 inconsistent. Both runtimes share the code.
- **Find a speed gap per conversion, not per routine.** A routine profile showed only "video 75%" on both targets. Splitting the trace at each `vc_conv` entry (pairs converted, lines scanned, full or diff) showed the 128K converter costs the same per pair as 512K's, and that whole-page scans were the extra work. Count calls (`v_flip`, `vm_page`, `hr_wr` callers) from the raw trace against the listings.
- **`NATIVE_SRC_B` on 512K: the top of every code bank.** Slot 6 always maps the running image's bank, so build512 assembles those natives at `BLOCKB_TOP` and copies them into every bank. The same port split then frees runtime space on both targets (Zaxxon: about 2K). A native there may call the runtime's stage loader: it changes slot 6, and the caller survives because every bank has the same natives at the same addresses.
- **A MAME probe that writes memory through a remapped MMU slot must remap it in both tasks (Zaxxon 128K, 2026-11-27).** A lives poke mapped the target block in task 0's slot 2 (`$FFA2`) only. When the frame callback ran during a conversion (task 1), the write went through task 1's slot 2, which then mapped the last GIME block, and changed one converter instruction. The result looked like a converter crash: an overflowed commit log, runaway code, frozen ticks. It depended on code layout and game timing, so unrelated converter edits seemed to cause it. Set `$FFA2` and `$FFAA` together, as the invariant probes do. `kit/mame/watch.lua` does this for its `POKE` option.
- **Bisect long-run crashes with a stall watchdog and full-state dumps, not with guesses.** A debugger-free watchdog (the tick stops → registers and MMU map) found the stall, a conditional breakpoint saved the log at the bad commit, and a short register-logged trace (`tracelog` with S, CC, U) showed the wrong operand. Runs with a different converter timing take a different game course, so "build A crashes, build B doesn't" doesn't prove A's change caused it.
- **128K converter: change boxes per area, and no redraw of what can't be seen (Zaxxon, 2026-11-27).**
  - The single-buffer converter reports the bounding box of what changed in each dirty box to the other page's list. One box holding changes far apart (a status band at the top, an object lower down) produced one big box, and the next conversion inherited it, so every flip rescanned most of the page. `vid.asm` now starts a new change box after `VC_GAP` (12) unchanged lines.
  - What natives drew on the shown page L used to be converted in full before every flip to P. Outside P's list the screen already shows P, so `vconv` now skips that conversion when no box of L's list overlaps P's list (lines overlap and columns within 3, the neighbour pairs), and L keeps its list.
  - Result on a 60000-frame run with a lives poke: 3466 → 3700 (boxes) → 4063 (skip) flips, +17%. The 128K screen check found 0 inconsistent.
  - Room: the 128K runtime had none left, so Zaxxon assembles out a 1.5% copy-loop unroll there (`ifndef T128`). Engine, the largest stage, block-B natives and runtime fill 128K to within about 100 bytes.
