"""lwasm driver with branch relaxation. Game-independent.

assemble(lines, out_bin, fmt='raw', listing=None, symbols=None) -> lines actually assembled
Short branches that lwasm reports as "Byte overflow" are turned into long branches (LBxx) and the source is
assembled again, until it assembles.  Any other error raises AsmError.
"""
import os
import re
import subprocess
import tempfile

LWASM = os.path.expanduser('~/opt/lwtools/lwasm/lwasm')
SHORT = ('bra', 'brn', 'bhi', 'bls', 'bcc', 'bhs', 'bcs', 'blo', 'bne', 'beq', 'bvc', 'bvs', 'bpl', 'bmi',
         'bge', 'blt', 'bgt', 'ble', 'bsr')
_ERR = re.compile(r'^(.*?)\((\d+)\) : ERROR : (.*)$')


class AsmError(Exception):
    pass


def assemble(lines, out_bin, fmt='raw', listing=None, symbols=None, max_rounds=40):
    lines = [s for ln in lines for s in ln.split('\n')]   # one entry per source line: lwasm's line numbers index it
    with tempfile.TemporaryDirectory() as td:
        src = os.path.join(td, 'x.asm')
        for _ in range(max_rounds):
            open(src, 'w').write('\n'.join(lines) + '\n')
            cmd = [LWASM, '-9', '-f', fmt, '-o', out_bin, src]
            if listing:
                cmd += ['-l' + listing]
            if symbols:
                cmd += ['--symbol-dump=' + symbols]
            r = subprocess.run(cmd, capture_output=True, text=True)
            if r.returncode == 0:
                return lines
            fix, other = [], []
            for ln in (r.stdout + r.stderr).splitlines():
                m = _ERR.match(ln)
                if not m:
                    continue
                n, msg = int(m.group(2)) - 1, m.group(3)
                parts = lines[n].split('\t')
                op = parts[1].lower() if len(parts) > 1 else ''
                if msg.startswith('Byte overflow') and op in SHORT:
                    fix.append(n)
                else:
                    other.append(f'{n + 1}: {msg}: {lines[n].strip()}')
            if other:
                raise AsmError('\n'.join(other[:20]))
            if not fix:
                raise AsmError(r.stdout + r.stderr)
            for n in fix:
                parts = lines[n].split('\t')
                parts[1] = 'l' + parts[1].lower() if parts[1].lower() != 'bsr' else 'lbsr'
                lines[n] = '\t'.join(parts)
        raise AsmError('branch relaxation did not converge')
