/*
 * mc6809.h - small Motorola 6809 interpreter for the CoCo port test harness.
 * Every documented opcode, all indexed modes, cycle counts per instruction
 * (as in the Motorola data sheet).  Flat 64K memory through callbacks.
 */
#ifndef MC6809_H
#define MC6809_H
#include <stdint.h>

enum { CC_C = 0x01, CC_V = 0x02, CC_Z = 0x04, CC_N = 0x08,
       CC_I = 0x10, CC_H = 0x20, CC_F = 0x40, CC_E = 0x80 };

typedef struct mc6809 {
    uint8_t  a, b, dp, cc;
    uint16_t x, y, u, s, pc;
    uint64_t cycles;
    int      illegal;           /* set to the opcode on an illegal instruction */
    void    *ctx;
    uint8_t (*read)(void *ctx, uint16_t addr);
    void    (*write)(void *ctx, uint16_t addr, uint8_t val);
} mc6809;

void mc6809_reset(mc6809 *c);          /* PC from $FFFE */
int  mc6809_step(mc6809 *c);           /* one instruction, returns cycles */
void mc6809_irq(mc6809 *c);            /* take an IRQ now (if I clear) */

#endif
