# Deciding what to transcode and what to replace

The default is **transcode**. A routine becomes a native only for one of the reasons below. `kit/survey.py` finds candidates for each rule from the coverage profile. Confirm every candidate against a disassembly (`a2-disassemble`, `a2-trace-calls` skills) before putting it in the spec.

## The rules

**H: hardware.**
- A routine whose job is hardware I/O beyond what the runtime handlers already cover (`NATIVES.md`, table 1) becomes a native.
- A single `LDA $C000` or `STA $C030` in game logic is fine: it is translated to a handler call.
- A loop that polls hardware, or depends on an exact sequence of hardware accesses, is not. Examples: the Disk II nibble loop, `$C08x,X` slot I/O, language-card banking, video-mode sequences for mixed text/graphics.

**T: timing.**
- Code whose effect depends on its cycle count must be native, padded to Apple time (1 Apple cycle ≈ 1.75 CoCo cycles). Examples:
  - speaker tone loops (pitch = cycles between `$C030` clicks);
  - paddle reads (count until `$C064` falls);
  - delay loops and `WAIT`;
  - VBL polling (`$C019`), and loops that count polls as a delay.
- The survey lists these as "timing code" per routine.
- Replace the **whole loop**, and exit where it exits (`NATIVE_EXITS`) with the registers it leaves.

**V: video.**
- The survey ranks routines by hi-res memory traffic. Routines that carry most of it (blitters, fills, scrolls, page copies, clears) become natives.
- Any translated store into a hi-res page is invisible: nobody calls `vm_rect` for it. Run a per-image hi-res scan (Karateka `hires_scan.py` is the model): play each image on the host with random input and assert that no translated instruction touches `$2000-$5FFF`.

**P: performance.**
- A routine above about 5% of the estimated cycles that is pixel-, shift- or table-bound: the 6809's `MUL`, 16-bit registers, `PSHS`/`PULU` block moves and indexed modes beat the translation by 2-5×.
- Measure first: `test_engine` prints CoCo cycles per Apple cycle; 1.75 is real time.
- Pure logic usually translates well enough; leave it.

**R: ROM.**
- Every call into monitor or firmware ROM becomes a native (`WAIT`, `PREAD`, `COUT`, `HOME`, `RDKEY`, `BELL`, Disk II `$C65C`).
- Reads of ROM bytes as data (ID bytes, tables) become `ROMCONST` entries.
- `SETKBD`/`SETVID`/`INIT` at boot usually vanish with the boot code.

**S: self-modifying code.**
- An operand patched at run time (an address or immediate that changes) goes in `SMC_IMM`; the translation reads the operand from memory.
- A patched opcode cannot be translated. Put the routine inside a native (the native takes the patched value as a mode) and list the byte in `SMC_BYTES` if it differs between images.

**L: loaders.**
- The boot sector, RWTS, relocators, decompressors and copy protection are **not translated**.
- Snapshots capture their result.
- The native level loader (`nt_load`) restores exactly the pages each load writes (`.mask`) and enters the level from its snapshot state. On 512K they come compressed from RAM; on 128K they are streamed from disk.
- A "reboot" path (game over that re-boots the disk) becomes `nt_reboot`.

## Checklist per candidate
1. Entry address(es) and the full body range go in `NATIVE`/`NATIVE_RANGES`. Every other jump into the range is a generation error, which is useful: it finds missed entries.
2. Exits into translated code go in `NATIVE_EXITS`.
3. Inputs and outputs: registers, flags, zero page, tables in RAM. `report.txt` shows what the callers read.
4. A bit-exact test against the 6502 routine on random inputs from real snapshot state.
5. Dirty marking for every changed bitmap byte: `vm_rect` once per call, with the tightest rectangle that is cheap to know.
6. The cost ratio from the native test (CoCo cycles per Apple cycle).

## What the survey cannot know
- Whether a hi-res routine's traffic is sprites (draw) or scenery (scroll/copy): read the code.
- Whether a "hot" logic routine is hot because it waits: polling loops look hot.
- Code the scenarios never reached. Profile every level and mode with several scenarios (`--scenario` is repeatable, `--merge` combines runs).
