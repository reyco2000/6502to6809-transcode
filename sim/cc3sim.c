/*
 * cc3sim.c - tiny CoCo 3 test machine: 6809 + 512K + GIME MMU (two tasks, $FE00 constant page).
 * No video/timing; for unit-testing runtime routines.
 *
 *   cc3sim [-l PHYSHEX:file]... [-m SLOT:BLOCK]... -pc ADDR -stop ADDR [-max N]
 *          [-d PHYSHEX:LEN:file]... [-at ADDR]...
 * Default task-0 map: slots 0-7 = blocks $38-$3F (as after CoCo 3 reset).
 * Prints the cycle count between start and stop.  -at ADDR (up to 16): print "at ADDR cycles=N s6=BB" each
 * time the PC reaches ADDR (at most 200 lines; s6 = task-0 slot-6 block, to tell code banks apart).
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "mc6809.h"

static uint8_t ram[512 * 1024];
static uint8_t io[256];
static uint8_t mmu[16];

static unsigned phys(uint16_t a)
{
    if ((io[0x90] & 0x08) && a >= 0xFE00 && a < 0xFF00)
        return 0x7FE00 + (a & 0xFF);
    int task = io[0x91] & 1;
    return ((unsigned)(mmu[task * 8 + (a >> 13)] & 0x3F) << 13) | (a & 0x1FFF);
}

static uint8_t rd(void *ctx, uint16_t a)
{
    (void)ctx;
    if (a >= 0xFF00) {
        if (a >= 0xFFA0 && a <= 0xFFAF) return mmu[a - 0xFFA0];
        return io[a & 0xFF];
    }
    return ram[phys(a)];
}

static void wr(void *ctx, uint16_t a, uint8_t v)
{
    (void)ctx;
    if (a >= 0xFF00) {
        if (a >= 0xFFA0 && a <= 0xFFAF) mmu[a - 0xFFA0] = v;
        else io[a & 0xFF] = v;
        return;
    }
    ram[phys(a)] = v;
}

int main(int argc, char **argv)
{
    unsigned pc = 0, stop = 0, at[16], nat = 0, natp = 0;
    unsigned long long max = 2000000000ULL;
    for (int i = 0; i < 8; i++) mmu[i] = mmu[8 + i] = 0x38 + i;
    io[0x90] = 0x48;  /* MMU on, constant page off */
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "-l") && i + 1 < argc) {
            unsigned p; char fn[512];
            if (sscanf(argv[++i], "%x:%511s", &p, fn) != 2) return 2;
            FILE *f = fopen(fn, "rb"); if (!f) { perror(fn); return 2; }
            size_t n = fread(ram + p, 1, sizeof ram - p, f); fclose(f);
            fprintf(stderr, "loaded %zu bytes at phys %05X\n", n, p);
        } else if (!strcmp(argv[i], "-m") && i + 1 < argc) {
            unsigned s, b; sscanf(argv[++i], "%x:%x", &s, &b); mmu[s & 15] = b;
        } else if (!strcmp(argv[i], "-pc")) pc = strtoul(argv[++i], 0, 16);
        else if (!strcmp(argv[i], "-stop")) stop = strtoul(argv[++i], 0, 16);
        else if (!strcmp(argv[i], "-max")) max = strtoull(argv[++i], 0, 10);
        else if (!strcmp(argv[i], "-at") && nat < 16) at[nat++] = strtoul(argv[++i], 0, 16);
    }
    mc6809 c; memset(&c, 0, sizeof c);
    c.read = rd; c.write = wr; c.pc = pc; c.s = 0xFDFF; c.cc = 0x50;
    unsigned long long n = 0;
    while (c.pc != stop && n < max) {
        for (unsigned k = 0; k < nat; k++)
            if (c.pc == at[k] && natp < 200) {
                printf("at %04X cycles=%llu s6=%02X\n", c.pc, (unsigned long long)c.cycles, mmu[6]);
                natp++;
            }
        mc6809_step(&c);
        n++;
        if (c.illegal) { fprintf(stderr, "illegal opcode %02X near %04X\n", c.illegal, c.pc); return 3; }
    }
    printf("stop pc=%04X cycles=%llu instr=%llu a=%02X b=%02X x=%04X y=%04X u=%04X s=%04X\n",
           c.pc, (unsigned long long)c.cycles, n, c.a, c.b, c.x, c.y, c.u, c.s);
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "-d") && i + 1 < argc) {
            unsigned p, len; char fn[512];
            if (sscanf(argv[++i], "%x:%x:%511s", &p, &len, fn) != 3) return 2;
            FILE *f = fopen(fn, "wb"); fwrite(ram + p, 1, len, f); fclose(f);
        }
    }
    return c.pc == stop ? 0 : 1;
}
