# Apple II source machine

## Supported now
- **CPU:** NMOS 6502, documented opcodes (`kit/opcodes.py`), minus the rejected set in `METHOD.md`.
- **Host model** (`kit/host/apple2.py`): II+/IIe-class with 64K, language card, a slot-6 Disk II with nibble-level reads, and the ROM from the user's IIe image. It boots `.dsk`/`.do`/`.po`/`.nib`/`.woz`/`.2mg` directly; see `docs/DISKS.md`. It is cycle-counted, which is good enough for loaders, timing and profiles.
- **Video:** hi-res page 1/2 (`$2000`/`$4000`, 280×192 with artifact colour), with exact colour conversion to GIME 320×192×16 (`kit/tables.py`, `rt/t*/vid.asm`). The text page is not displayed; a game that shows text through the text page needs a native or a converter extension.
- **Input:** keyboard `$C000`/`$C010`; buttons `$C061`/`$C062`; paddles through the CoCo right stick.
- **Sound:** speaker `$C030` → DAC.
- **Disk:** the game's loader is replaced by snapshots and the native level loader.

## Softswitch reference (what `kit/survey.py` classifies)

| address | name | handling |
|---|---|---|
| `$C000` | KBD | runtime |
| `$C010` | KBDSTRB | runtime |
| `$C019` | RDVBL (IIe) | `rt_vbl` native |
| `$C030` | SPKR | runtime click; tone loops native |
| `$C050-$C057` | TXTCLR/TXTSET/MIXCLR/MIXSET/PAGE1/PAGE2/LORES/HIRES | `$C054`/`$C055` runtime; the others are ignored. Check mixed or text screens. |
| `$C058-$C05F` | annunciators; `$C05E`/`$C05F` = IIe double hi-res | not supported (DHR) |
| `$C061-$C063` | buttons | runtime |
| `$C064-$C067`, `$C070` | paddle timers / trigger | native (paddle loop) |
| `$C080-$C08F` | language card | native or memory-map design |
| `$C0E0-$C0EF` (`$C08x,X`, X = `$60`) | Disk II | dropped (loader) or native |
| `$C100-$CFFF` | slot ROMs | native |
| `$F800-$FFFF` | monitor: `WAIT` `$FCA8`, `PREAD` `$FB1E`, `COUT` `$FDED`, `HOME` `$FC58`, `RDKEY` `$FD0C`, `BELL` `$FF3A` | natives (`ROM_KNOWN` in `kit/survey.py`) |

## Gaps to close when a game needs them
- **128K IIe (aux memory, `$C002-$C00B` / `$C054`/`$C055` with 80STORE, RAMRD/RAMWRT, ALTZP):**
  - the host model needs aux RAM and the switches;
  - the snapshots need aux contents;
  - the build needs 6 more Apple RAM blocks (512K: easy; 128K: not possible), plus native bank switching.
- **Double hi-res (560×192, 16 colours):** a new converter (the GIME can show 320×192 at 16 colours; DHR is 140×192 at 16 colours effective, which maps well). The bitmap is interleaved across main and aux memory.
- **Lo-res and text screens:** a small converter or natives.
- **Quarter-track and timing-based protection.** `.nib` and `.woz` boot as they are (`kit/disk/a2disk.py`), but the host drive uses whole tracks and a fixed 32 cycles per nibble. Protection that relies on quarter tracks, bit timing or weak bits may fail on the host: use a cracked image, or extend `Disk2`.
- **65C02 games (IIc/enhanced IIe):** add the opcodes to `kit/opcodes.py` and templates to `kit/xc65.py`.
