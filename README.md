# 6502to6809

A kit for porting 6502 games to the TRS-80 Color Computer 3 by **static transcoding**. The game's own code is translated to 6809 ahead of time, and the hardware-bound hot spots are rewritten by hand. It targets 512K and 128K CoCo 3 machines. The source machine today is the Apple II; a C64 layer is designed in `docs/machines/c64.md`.

It is also a Claude Code skill: `~/.claude/skills/6502to6809` is a symlink to this repo, and `SKILL.md` is the entry point.

```
python3 -m kit.disk.a2disk info ~/disks/mygame.woz           # step 1: format, file system, protection, boot
python3 -m kit.newport mygame --disk ~/disks/mygame.woz     # scaffold ~/proyectos/mygame-coco3
cd ~/proyectos/mygame-coco3
./run.sh snapshots && ./run.sh profile && ./run.sh survey   # what to transcode, what to replace
./run.sh gen && ./run.sh test_engine -n 300
./run.sh build512                                           # build/mygame_512k.dsk
```

| path | what |
|---|---|
| `kit/` | Transcoder (`xc65`), discovery/split, generator, build (512K/128K), differential test, survey, scaffolder, snapshots, the host Apple II (`kit/host`), Apple II disk images and CoCo disks (`kit/disk`: `a2disk` = identify, catalog, extract, convert .dsk/.po/.nib/.woz/.2mg), MAME probes (`kit/mame`) |
| `kit/hw` | The hardware transcoder: the Apple II hardware map (`apple2.py`), idiom matchers, the routine library renderer, and test evidence (`survey --verify`) |
| `rt/t512`, `rt/t128` | 6809 runtimes: video converter, input, sound, VBL, fast loader, disk streamer |
| `rt/hw/apple2` | Hardware idiom routines (`vbl_poll`, `delay`, `tone`, `paddle_count`, `rom_wait`, `rom_pread`, `rom_bell`, the `hw_dly` timer) and video-mode handlers |
| `rt/natives/apple2` | Native routine templates |
| `sim/` | `cc3sim`: CoCo 3 unit-test machine (`make -C sim`) |
| `docs/` | Method, decision rules, natives, spec, memory maps, lessons, machines |
| `ports/` | Port packages (spec + natives + wrappers); none ship with the kit, `kit.newport` scaffolds new ones outside the repo |

**Requirements:**
- Python 3.11+;
- lwasm (`~/opt/lwtools/lwasm/lwasm`);
- gcc (to build `sim/cc3sim`);
- MAME with CoCo 3 ROMs, for the probes;
- the user's own disk images and Apple ROMs.

**Rule:** nothing derived from a game disk is committed (`build/`, `*.dsk`, snapshots, generated source).
