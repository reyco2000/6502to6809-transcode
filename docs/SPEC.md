# Port spec reference (`<port>/spec.py`, `<port>/port.toml`)

`python3 -m kit.newport NAME` writes a commented template of both files.

## port.toml

| key | meaning |
|---|---|
| `name` | Port name: Python package and disk file prefix. |
| `disk`, `rom`, `prom` | The user's Apple disk image, the Apple ROM (last 16K = `$C000-$FFFF`), and the Disk II boot PROM. Never copied anywhere. |
| `build` | Where everything derived goes: `snap/`, `coverage.json`, `t512/`, `t128/`, `.dsk`. Relative paths are relative to `port.toml`. |
| `coverage` | The coverage profile (`./run.sh profile`). |
| `ram` | Default target: 512 or 128. |
| `loader` | Disk file names `<loader>.BIN`/`.DAT`; the user types `LOADM"<loader>":EXEC`. |

## spec.py: images

| name | meaning |
|---|---|
| `BASE` | The boot hand-over image (default `'cold'`). Engine = code identical in every image. |
| `STAGES` | Overlay images in loader-number order. `nt_load` / `nt_ovl` get B = 1..`NSTAGES`. Two kinds: a **restart stage** (the loader never returns: snapshot with `record_writes(m)`; entered from its snapshot state by `nt_load`) and a **call stage** (the game's loader returns to its caller, like mid-game segment loads: snapshot with `record_writes(m, call=True)`; its image is BASE plus the bytes the loader wrote; `nt_ovl` copies them and returns. On 128K the build streams exactly the bytes the loader wrote, except the stack page, plus the stage's code at `OVORG`; the port's 128K `nt_ovl` runs `ds_load` on a runtime stack, then `v_inval`). |
| `IMAGES` | Image name → snapshot file stem (`snap_<name>`). |
| `entry_state`, `entries`, `load_images`, `profile_roots` | From `kit.specbase.helpers(module)`. |

## spec.py: what is translated

| name | meaning |
|---|---|
| `NATIVE` | `{6502 address: native symbol}`. A JSR/JMP to the address calls the native instead. |
| `NATIVE_RANGES` | `[(lo, hi)]`, inclusive bodies of replaced code. Never translated; any other transfer into them is an error. |
| `NATIVE_EXITS` | Translated addresses natives jump to (`jmp E<addr>`). |
| `SMC_BYTES` | Opcode/operand bytes patched at run time inside native code (they may differ between images). |
| `SMC_IMM` | Instruction addresses whose operand is patched (operand SMC): the translation reads it from memory. Immediate operands are loaded from the code byte; absolute, `abs,X` and `abs,Y` operands go through an `xsm*_HHHH` helper that forms the address at run time. The patched address must stay in RAM; if it can point at I/O (for example a `STA $C054`/`$C055` page flip), replace that code with a native. |
| `CODE_RANGES` | Where translated code may be: not zero page, the stack, the hi-res pages or I/O. |
| `SMC_OPS` | `{site: [opcodes]}` opcode SMC: each opcode the profile saw (survey section 4) is translated, and the code dispatches on the byte in memory at run time (unknown byte: `x_trap`). The image's own byte is added when compatible. Variants must have the same length and no JSR/JMP/RTS; their operand is always read from memory. |
| `SMC_JMP` | `{JMP abs site: [targets]}`: a JMP whose target operand is patched (the targets the profile saw). |
| `TRAPS` | Dead paths: `[addr | (image, addr)]`. A transfer to the address becomes `jsr x_trap`. `gen` lists candidates when discovery runs into data on paths the profile never took, and refuses a trap on executed code. |
| `HIRES_ACCESS` | `True`: translated code may read/write the hi-res pages; those accesses (static addresses in `$2000-$5FFF`, and pointer/indexed ones the profile saw there or never ran) go through the runtime's `hr_rd`/`hr_wr` (512K and 128K: hi-res mapped for one byte; whole-page dirty mark). Correct but slow: hot drawing routines still want natives. |
| `ROMDATA` | `[(lo, hi)]` ROM ranges the game reads as data (tables, pitch data): copied from the user's ROM into `ROM_<lo>` tables in the generated source; pointer reads that may reach ROM go through `xrd`. |

## spec.py: generator hooks (optional)

| name | meaning |
|---|---|
| `IO_MEM` | `{$C0xx: latch symbol}`: reads with no side effect become plain loads. The default is the hardware map's latch entries (keyboard, buttons). |
| `HW_OVERRIDES` | `{$C0xx: action}` (`from kit.hw import Ignore, Latch, Runtime`): replaces the hardware map's action for one address (`kit/hw/apple2.py`). Generation fails on any access whose action is a trap, loop-only or unmapped; an override is how a port accepts one, for example `{0xC05E: Ignore()}` when a game sets AN3 but never shows double hi-res. |
| `ROMCONST` | `{ROM address: byte}` for ROM data the game reads. |
| `NATIVE_CONTRACTS` | `{symbol: Native(sym, use, defs, noreturn)}` for natives with no 6502 body (ROM entries) or that never return. |
| `HW_IDIOMS` | `[Idiom(kind, entry, end, exits=(), params={})]` (`from kit.hw import Idiom`): hardware idioms you accepted from the survey's proposals (section 7 of `survey.txt`, commented out in `draft_spec.py`). Each becomes `NATIVE[entry] = hw_<kind>_<entry>` with its range and exits; the routine is rendered from `rt/hw/apple2/<kind>.asm` into `build/src/hw_inst.asm`. Kinds: `vbl_poll`, `rom_wait`, `delay`, `tone`, `paddle_count`, `rom_pread`, `rom_bell`. An overlap with another native or idiom is an error, and so is an exit into overlay code (`image=`; not supported yet). |

## spec.py: build hooks

| name | meaning |
|---|---|
| `START_LABEL` | Native label `rt_start` jumps to after init (`game_start` in the scaffold). |
| `NATIVE_SRC(ram)` | List of the port's `.asm` files for that target. |
| `NATIVE_SRC_B(ram)` | Optional. Native `.asm` files placed in code block B on 128K (assembled at `BLOCKB_TOP`, streamed in at boot) when the runtime block is full. Same assembly as the runtime and `NATIVE_SRC`, so labels and macros are shared both ways; list them after the files that define their macros. On 512K they are assembled at `BLOCKB_TOP` too and copied to the top of every code bank (slot 6 maps the running image's bank). Block B is mapped at `$C000` all the time (also between `HRON`/`HROFF`), and stage loads write only `OVORG` up to `BLOCKB_TOP`. |
| `title(ram)` | Loading screen: `(row, text, attribute[, column])`, 40×24, attribute bits 5-3 = foreground (palette 8-15). |
| `BLOCKB_RESERVE` | 128K only: bytes at the top of code block B for native tables. `BLOCKB_TOP` = `$E000` − reserve − the `NATIVE_SRC_B` code (rounded down to 16); the code starts there and the reserve starts at `natb_end`. |

## Equates the build gives the native sources
- `XMMU`: real MMU mapping. It is undefined in native tests.
- `NSTAGES`.
- The block layout: `BLK_*`.
- `OVORG`/`OVBASE` (128K): where level code sits.
- `BLOCKB_TOP` (128K).
- `T128` or `T512`: the target, for `ifdef` in a shared native source (e.g. leaving out an unrolled variant where the 128K runtime has no room).
- `RT_START_GAME`.
- Every runtime symbol (`rt_*`, `vm_*`, `kb_*`, `joy_*`, `hr_on`, …), since the natives are assembled together with the runtime.
- For level code, the label `<IMAGE>_START` is built from the snapshot: stack frames, registers and flags.
