"""Generic parts of a port spec (see docs/SPEC.md).  A port's spec.py does:

    from kit import portcfg, specbase
    PORT = portcfg.load(os.path.dirname(os.path.abspath(__file__)))
    SNAP, COVERAGE = PORT.snap, PORT.coverage
    entry_state, entries, load_images, profile_roots = specbase.helpers(sys.modules[__name__])
"""
import json
import os


def helpers(spec):
    def entry_state(name):
        return json.load(open(os.path.join(spec.SNAP, spec.IMAGES[name] + '.json')))

    def is_call(name):
        """a call stage: loaded by a native that returns to the game's caller (snapshot taken with
        record_writes(m, call=True)); its image is BASE plus the bytes the loader wrote"""
        return bool(entry_state(name).get('call'))

    def entries(name):
        """entry pc plus the loader's RTS chain (return address + 1) plus the natives' exits.  A call stage is
        entered by returning into code that is already running: only the natives' exits"""
        s = entry_state(name)
        if s.get('call'):
            return list(spec.NATIVE_EXITS)
        return [s['pc']] + [(r + 1) & 0xFFFF for r in s.get('ret_chain', [])] + list(spec.NATIVE_EXITS)

    def load_images():
        out = {}
        base = getattr(spec, 'BASE', 'cold')
        for n, f in spec.IMAGES.items():
            b = bytearray(open(os.path.join(spec.SNAP, f + '.bin'), 'rb').read())
            b += bytearray(0x10000 - len(b))
            if n != base and is_call(n):
                # the rest of RAM is whatever the game has at the load: for the code, the BASE image's bytes
                mask = open(os.path.join(spec.SNAP, f + '.mask'), 'rb').read()
                img = bytearray(open(os.path.join(spec.SNAP, spec.IMAGES[base] + '.bin'), 'rb').read())
                img += bytearray(0x10000 - len(img))
                for a, m in enumerate(mask):
                    if m:
                        img[a] = b[a]
                b = img
            out[n] = b
        return out

    def profile_roots(images):
        """executed instruction starts from the coverage profile (kit/host/profile.py), per image whose bytes
        match"""
        if not spec.COVERAGE or not os.path.exists(spec.COVERAGE):
            return {}
        cov = json.load(open(spec.COVERAGE))
        # code that only ran before the hand-over (boot loader, relocator) is not the game's: no roots from it
        boot = entry_state(getattr(spec, 'BASE', 'cold')).get('boot_frame', 0)
        res = {}
        for n, mem in images.items():
            r = set()
            for im in cov['images']:
                base = int(im['page'], 16) << 8
                if base >= 0xC000:
                    continue
                if boot and all(ld['last_frame'] < boot for ld in im.get('loads', [])):
                    continue
                b = bytes.fromhex(im['bytes'])
                role = im['role']
                # SMC bytes (volatile: patched while the image ran) say nothing about which code is loaded
                vol = {(int(v, 16) if isinstance(v, str) else v) - base for v in im.get('volatile', [])}
                if all(b[o] == mem[base + o] for o in range(256) if role[o] != '.' and o not in vol):
                    instr = im['instr']
                    if isinstance(instr, str):
                        instr = json.loads(instr.replace("'", '"'))
                    r |= {int(a, 16) if isinstance(a, str) else a for a in instr}
            res[n] = {a for a in r if any(lo <= a <= hi for lo, hi in spec.CODE_RANGES)
                      and not any(lo <= a <= hi for lo, hi in spec.NATIVE_RANGES)}
        return res

    def profile_ea(images=None):
        """{addr: EA-class mask} over every profiled code image (bit 8 = the access reached $2000-$5FFF)"""
        if not spec.COVERAGE or not os.path.exists(spec.COVERAGE):
            return {}
        cov = json.load(open(spec.COVERAGE))
        out = {}
        for im in cov['images']:
            for a, msk in im.get('ea', {}).items():
                a = int(a, 16) if isinstance(a, str) else a
                out[a] = out.get(a, 0) | msk
        return out

    def profile_flags(bit):
        """addresses whose profile flags have `bit` (2: zp,X / zp,Y index wrapped past $FF)"""
        if not spec.COVERAGE or not os.path.exists(spec.COVERAGE):
            return set()
        cov = json.load(open(spec.COVERAGE))
        return {int(a, 16) for im in cov['images'] for a, f in im.get('flags', {}).items() if f & bit}

    spec.profile_flags = profile_flags
    spec.is_call = is_call
    spec.profile_ea = profile_ea
    return entry_state, entries, load_images, profile_roots
