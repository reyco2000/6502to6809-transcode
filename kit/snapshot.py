"""Build-time snapshots on the host Apple II (game-independent helpers).

A snapshot is the 48K of Apple RAM plus the CPU state at the first instruction the port translates, taken by
running the game's own boot/loader code on kit/host.  A port's snapshots.py says WHEN to stop for each image
(its boot hand-over address, how a level/stage load is triggered) and calls these helpers:

    m = boot(rom, prom, dsk, until=lambda pc: pc == 0xB77C)        # cold image: boot the disk to the hand-over
    mask = record_writes(m)                                         # pages the loader writes (stage images)
    mask = record_writes(m, call=True)                              # call stage: the loader returns to its caller
    m = play(m, scenario, until, frames, offset)                    # drive the game with a profile scenario
    run_until(m, pred, frames)                                      # run until pred(pc) is true
    save(outdir, name, m, mask)                                     # snap_<name>.bin / .json / .mask
    main(spec, takers)                                              # CLI: DSK --rom --prom [-o] [--only]

The JSON holds pc, a, x, y, s, p and ret_chain: 6502 return addresses the loader left on the stack that the game
really returns to (the build pushes their native labels).  Outputs are derived from the user's disk: build/ only.
"""
import argparse
import json
import os

from .host.apple2 import Apple2

FRAME = 17030       # Apple II cycles per 60 Hz frame


class Stop(Exception):
    pass


def in_ranges(ranges, a):
    return any(lo <= a <= hi for lo, hi in ranges)


def run_until(m, pred, frames, what='stop condition'):
    """run frame by frame until pred(pc) is true (checked before each instruction)"""
    def tr(pc):
        if pred(pc):
            raise Stop
    try:
        for _ in range(frames):
            m.run_cycles(FRAME, trace=tr)
            m.frames_run = getattr(m, 'frames_run', 0) + 1
    except Stop:
        return m
    raise RuntimeError(f'{what} not reached in {frames} frames')


def run_trace(m, tr, frames, what='stop condition'):
    """run with a custom trace(pc) that raises Stop when done (stateful conditions)"""
    try:
        for _ in range(frames):
            m.run_cycles(FRAME, trace=tr)
    except Stop:
        return m
    raise RuntimeError(f'{what} not reached in {frames} frames')


def boot(rom, prom, dsk, until=None, frames=6000):
    """power on with the disk in slot 6; with until(pc), run to that point"""
    m = Apple2(rom[-0x3000:], prom, dsk, cxrom=rom[-0x4000:-0x3000])
    m.boot()
    m.frames_run = 0
    if until:
        run_until(m, until, frames, 'boot hand-over')
        m.boot_frame = m.frames_run         # profile code images that ended before it are boot code (specbase)
    return m


def play(m, scenario, until, frames, offset=0):
    """run frame by frame applying a profile scenario (kit/host/profile.py format: keys, joy, pokes) until
    until(pc) is true.  offset = the scenario frame at which m was stopped (scenarios count from power-on; a
    machine from boot(..., until=HAND_OVER) is about HAND_OVER's boot frame in).  Pokes (coverage cheats such as
    infinite lives) are applied too: use them only to reach a load, the call-stage image keeps loaded bytes only."""
    from .host.profile import load_scenario
    sc = load_scenario(scenario) if isinstance(scenario, str) else scenario

    def tr(pc):
        if until(pc):
            raise Stop
    try:
        for k in range(frames):
            f = k + offset
            if f in sc['keys']:
                m.kbd = sc['keys'][f] | 0x80
            if f in sc['joy']:
                m.paddle[0], m.paddle[1], m.buttons = sc['joy'][f]
            for pa, pv, pf in sc.get('pokes', []):
                if f >= pf:
                    m.ram[pa] = pv
            m.run_cycles(FRAME, trace=tr)
    except Stop:
        return m
    raise RuntimeError(f'stop condition not reached in {frames} frames of {sc.get("name")}')


def record_writes(m, call=False):
    """from now on, mark every RAM byte below $C000 that is written; returns the mask (bytearray).
    call=True (a call stage: the loader returns to the game): the loader's own stack frames (page $01 at and below
    the current S) are not part of the image and are dropped from the mask when it is saved"""
    mask = bytearray(0xC000)
    m.load_s = m.cpu.s if call else None
    wr = m.write

    def write(a, v):
        if a < 0xC000:
            mask[a] = 1
        return wr(a, v)
    m.write = write
    m.load_mask = mask
    return mask


def state(m):
    c = m.cpu
    return {'pc': c.pc, 'a': c.a, 'x': c.x, 'y': c.y, 's': c.s, 'p': c.p}


def ret_chain(m, frames=600):
    """6502 return addresses the loader left on the stack that are really consumed by an RTS: continue the
    run and record RTS pops of original stack slots that were not overwritten before"""
    c = m.cpu
    s0 = c.s
    if s0 >= 0xFE:
        return []
    clobbered, used = set(), {}
    wr = m.write

    def write(a, v):
        if 0x100 + s0 < a < 0x200:
            clobbered.add(a & 0xFF)
        return wr(a, v)
    m.write = write
    prev = {'pc': None, 's': None}

    def tr(pc):
        p = prev['pc']
        if p is not None and m.ram[p] == 0x60:             # RTS popped prev s+1, s+2
            lo = prev['s'] + 1
            if lo > s0 and lo not in clobbered and lo + 1 not in clobbered:
                used[lo] = (pc - 1) & 0xFFFF
        prev['pc'], prev['s'] = pc, c.s
    for _ in range(frames):
        m.run_cycles(FRAME, trace=tr)
    m.write = wr
    chain, slot = [], s0 + 1
    while slot in used:                                  # contiguous frames from the top of the stack
        chain.append(used[slot])
        slot += 2
    return chain


def save(outdir, name, m):
    s = state(m)
    if getattr(m, 'boot_frame', None) is not None and getattr(m, 'load_mask', None) is None:
        s['boot_frame'] = m.boot_frame
    ram = bytes(m.ram[:0xC000])
    call = getattr(m, 'load_s', None) is not None
    if call:
        # a call stage is entered by returning to the game's caller, not from its snapshot state: no return chain.
        # Freeze the mask now (ret_chain keeps running the game, and the recorder would add the game's own writes)
        m.load_mask = bytearray(m.load_mask)
        s['ret_chain'] = []
    else:
        s['ret_chain'] = ret_chain(m)       # note: restart stages keep recording during this run (whole-page
        #                                     restores of a consistent snapshot state; harmless)
    open(os.path.join(outdir, f'snap_{name}.bin'), 'wb').write(ram)
    if getattr(m, 'load_mask', None) is not None:
        if getattr(m, 'load_s', None) is not None:
            for a in range(0x100, 0x101 + m.load_s):
                m.load_mask[a] = 0
            s['call'] = True
        open(os.path.join(outdir, f'snap_{name}.mask'), 'wb').write(bytes(m.load_mask))
        s['written'] = sum(m.load_mask)
    json.dump(s, open(os.path.join(outdir, f'snap_{name}.json'), 'w'))
    print(name, {k: (f'${v:02X}' if isinstance(v, int) else ['%04X' % x for x in v]) for k, v in s.items()},
          flush=True)
    return s


def main(spec, takers, argv=None):
    """takers: {image name: f(rom, prom, dsk) -> machine stopped at the image's entry}, in build order"""
    P = spec.PORT
    ap = argparse.ArgumentParser(description='host-Apple snapshots of every image of the port')
    ap.add_argument('dsk', nargs='?', default=P.disk)
    ap.add_argument('--rom', default=P.rom)
    ap.add_argument('--prom', default=P.prom)
    ap.add_argument('-o', default=spec.SNAP)
    ap.add_argument('--only', default='')
    a = ap.parse_args(argv)
    rom, prom, dsk = (open(f, 'rb').read() for f in (a.rom, a.prom, a.dsk))
    os.makedirs(a.o, exist_ok=True)
    todo = a.only.split(',') if a.only else list(takers)
    for name in todo:
        save(a.o, name, takers[name](rom, prom, dsk))
