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
