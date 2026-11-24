/*
 * mc6809.c - Motorola 6809 interpreter (see mc6809.h).
 */
#include "mc6809.h"

#define RD(a)    c->read(c->ctx, (uint16_t)(a))
#define WR(a, v) c->write(c->ctx, (uint16_t)(a), (uint8_t)(v))
#define D        ((uint16_t)(c->a << 8 | c->b))
#define SETD(v)  do { uint16_t _v = (uint16_t)(v); c->a = _v >> 8; c->b = _v & 0xff; } while (0)

static uint8_t  fetch(mc6809 *c)   { return RD(c->pc++); }
static uint16_t fetch16(mc6809 *c) { uint16_t v = (uint16_t)(RD(c->pc) << 8 | RD(c->pc + 1)); c->pc += 2; return v; }
static uint16_t rd16(mc6809 *c, uint16_t a) { return (uint16_t)(RD(a) << 8 | RD(a + 1)); }
static void     wr16(mc6809 *c, uint16_t a, uint16_t v) { WR(a, v >> 8); WR(a + 1, v & 0xff); }

static void push8s(mc6809 *c, uint8_t v)  { WR(--c->s, v); }
static void push16s(mc6809 *c, uint16_t v){ WR(--c->s, v & 0xff); WR(--c->s, v >> 8); }
static uint8_t pull8s(mc6809 *c)          { return RD(c->s++); }
static uint16_t pull16s(mc6809 *c)        { uint16_t v = (uint16_t)(RD(c->s) << 8 | RD(c->s + 1)); c->s += 2; return v; }

static void nz8(mc6809 *c, uint8_t r)
{
    c->cc &= ~(CC_N | CC_Z | CC_V);
    if (r & 0x80) c->cc |= CC_N;
    if (!r) c->cc |= CC_Z;
}
static void nz16(mc6809 *c, uint16_t r)
{
    c->cc &= ~(CC_N | CC_Z | CC_V);
    if (r & 0x8000) c->cc |= CC_N;
    if (!r) c->cc |= CC_Z;
}

/* ---- arithmetic ----------------------------------------------------------- */
static uint8_t add8(mc6809 *c, uint8_t a, uint8_t b, int carry)
{
    unsigned r = a + b + carry;
    c->cc &= ~(CC_H | CC_N | CC_Z | CC_V | CC_C);
    if (((a & 0xf) + (b & 0xf) + carry) & 0x10) c->cc |= CC_H;
    if (r & 0x80) c->cc |= CC_N;
    if (!(r & 0xff)) c->cc |= CC_Z;
    if (~(a ^ b) & (a ^ r) & 0x80) c->cc |= CC_V;
    if (r & 0x100) c->cc |= CC_C;
    return (uint8_t)r;
}
static uint8_t sub8(mc6809 *c, uint8_t a, uint8_t b, int borrow)
{
    unsigned r = a - b - borrow;
    c->cc &= ~(CC_N | CC_Z | CC_V | CC_C);
    if (r & 0x80) c->cc |= CC_N;
    if (!(r & 0xff)) c->cc |= CC_Z;
    if ((a ^ b) & (a ^ r) & 0x80) c->cc |= CC_V;
    if (r & 0x100) c->cc |= CC_C;
    return (uint8_t)r;
}
static uint16_t add16(mc6809 *c, uint16_t a, uint16_t b)
{
    uint32_t r = (uint32_t)a + b;
    c->cc &= ~(CC_N | CC_Z | CC_V | CC_C);
    if (r & 0x8000) c->cc |= CC_N;
    if (!(r & 0xffff)) c->cc |= CC_Z;
    if (~(a ^ b) & (a ^ r) & 0x8000) c->cc |= CC_V;
    if (r & 0x10000) c->cc |= CC_C;
    return (uint16_t)r;
}
static uint16_t sub16(mc6809 *c, uint16_t a, uint16_t b)
{
    uint32_t r = (uint32_t)a - b;
    c->cc &= ~(CC_N | CC_Z | CC_V | CC_C);
    if (r & 0x8000) c->cc |= CC_N;
    if (!(r & 0xffff)) c->cc |= CC_Z;
    if ((a ^ b) & (a ^ r) & 0x8000) c->cc |= CC_V;
    if (r & 0x10000) c->cc |= CC_C;
    return (uint16_t)r;
}

/* read-modify-write group (NEG COM LSR ROR ASR ASL ROL DEC INC TST CLR), op low nibble */
static int rmw(mc6809 *c, int op, uint8_t *v)
{
    uint8_t m = *v, r;
    switch (op) {
    case 0x0: r = sub8(c, 0, m, 0); break;                                   /* NEG */
    case 0x3: r = (uint8_t)~m; nz8(c, r); c->cc |= CC_C; break;               /* COM */
    case 0x4: r = m >> 1; c->cc &= ~(CC_N | CC_Z | CC_C); if (m & 1) c->cc |= CC_C; if (!r) c->cc |= CC_Z; break; /* LSR */
    case 0x6: r = (uint8_t)(m >> 1 | (c->cc & CC_C) << 7); c->cc &= ~(CC_N | CC_Z | CC_C);
              if (m & 1) c->cc |= CC_C; if (r & 0x80) c->cc |= CC_N; if (!r) c->cc |= CC_Z; break; /* ROR */
    case 0x7: r = (uint8_t)((m >> 1) | (m & 0x80)); c->cc &= ~(CC_N | CC_Z | CC_C);
              if (m & 1) c->cc |= CC_C; if (r & 0x80) c->cc |= CC_N; if (!r) c->cc |= CC_Z; break; /* ASR */
    case 0x8: r = (uint8_t)(m << 1); c->cc &= ~(CC_N | CC_Z | CC_V | CC_C);
              if (m & 0x80) c->cc |= CC_C; if (r & 0x80) c->cc |= CC_N; if (!r) c->cc |= CC_Z;
              if (((m >> 7) ^ (m >> 6)) & 1) c->cc |= CC_V; break;           /* ASL */
    case 0x9: r = (uint8_t)(m << 1 | (c->cc & CC_C)); c->cc &= ~(CC_N | CC_Z | CC_V | CC_C);
              if (m & 0x80) c->cc |= CC_C; if (r & 0x80) c->cc |= CC_N; if (!r) c->cc |= CC_Z;
              if (((m >> 7) ^ (m >> 6)) & 1) c->cc |= CC_V; break;           /* ROL */
    case 0xA: r = (uint8_t)(m - 1); nz8(c, r); if (m == 0x80) c->cc |= CC_V; break; /* DEC */
    case 0xC: r = (uint8_t)(m + 1); nz8(c, r); if (m == 0x7f) c->cc |= CC_V; break; /* INC */
    case 0xD: nz8(c, m); return 0;                                            /* TST */
    case 0xF: r = 0; c->cc &= ~(CC_N | CC_V | CC_C); c->cc |= CC_Z; break;    /* CLR */
    default:  c->illegal = 0x100 | op; return 0;
    }
    *v = r;
    return 1;
}

/* ---- addressing ----------------------------------------------------------- */
static uint16_t *idxreg(mc6809 *c, int r)
{
    switch (r & 3) { case 0: return &c->x; case 1: return &c->y; case 2: return &c->u; default: return &c->s; }
}

/* returns effective address, adds extra cycles to *cyc */
static uint16_t indexed(mc6809 *c, int *cyc)
{
    uint8_t pb = fetch(c);
    uint16_t *r = idxreg(c, pb >> 5);
    uint16_t ea;
    if (!(pb & 0x80)) {                         /* 5-bit offset */
        int off = pb & 0x1f;
        if (off & 0x10) off -= 0x20;
        *cyc += 1;
        return (uint16_t)(*r + off);
    }
    switch (pb & 0x0f) {
    case 0x0: ea = *r; *r += 1; *cyc += 2; break;
    case 0x1: ea = *r; *r += 2; *cyc += 3; break;
    case 0x2: *r -= 1; ea = *r; *cyc += 2; break;
    case 0x3: *r -= 2; ea = *r; *cyc += 3; break;
    case 0x4: ea = *r; break;
    case 0x5: ea = (uint16_t)(*r + (int8_t)c->b); *cyc += 1; break;
    case 0x6: ea = (uint16_t)(*r + (int8_t)c->a); *cyc += 1; break;
    case 0x8: ea = (uint16_t)(*r + (int8_t)fetch(c)); *cyc += 1; break;
    case 0x9: ea = (uint16_t)(*r + fetch16(c)); *cyc += 4; break;
    case 0xB: ea = (uint16_t)(*r + D); *cyc += 4; break;
    case 0xC: { int8_t o = (int8_t)fetch(c); ea = (uint16_t)(c->pc + o); *cyc += 1; break; }
    case 0xD: { uint16_t o = fetch16(c); ea = (uint16_t)(c->pc + o); *cyc += 5; break; }
    case 0xF: ea = fetch16(c); *cyc += 2; break;  /* [n] only valid indirect */
    default:  c->illegal = 0x200 | pb; return 0;
    }
    if (pb & 0x10) {                             /* indirect */
        ea = rd16(c, ea);
        *cyc += 3;
    }
    return ea;
}

/* effective address for modes: 0 = direct, 1 = indexed, 2 = extended */
static uint16_t ea_mode(mc6809 *c, int mode, int *cyc)
{
    switch (mode) {
    case 0: return (uint16_t)(c->dp << 8 | fetch(c));
    case 1: return indexed(c, cyc);
    default: return fetch16(c);
    }
}

static uint16_t *reg16(mc6809 *c, int n, uint16_t *tmp)
{
    switch (n) {
    case 1: return &c->x; case 2: return &c->y; case 3: return &c->u;
    case 4: return &c->s; case 5: return &c->pc;
    default: (void)tmp; return 0;
    }
}

static uint16_t getreg(mc6809 *c, int n)
{
    switch (n) {
    case 0: return D; case 1: return c->x; case 2: return c->y; case 3: return c->u;
    case 4: return c->s; case 5: return c->pc;
    case 8: return c->a | 0xff00; case 9: return c->b | 0xff00;
    case 10: return c->cc | 0xff00; case 11: return c->dp | 0xff00;
    default: return 0xffff;
    }
}
static void setreg(mc6809 *c, int n, uint16_t v)
{
    uint16_t t;
    switch (n) {
    case 0: SETD(v); break;
    case 8: c->a = (uint8_t)v; break; case 9: c->b = (uint8_t)v; break;
    case 10: c->cc = (uint8_t)v; break; case 11: c->dp = (uint8_t)v; break;
    default: { uint16_t *r = reg16(c, n, &t); if (r) *r = v; }
    }
}

static int branch_cond(mc6809 *c, int op)
{
    int cc = c->cc, n = !!(cc & CC_N), v = !!(cc & CC_V), z = !!(cc & CC_Z), cy = !!(cc & CC_C);
    switch (op & 0xf) {
    case 0x0: return 1;            case 0x1: return 0;
    case 0x2: return !(cy | z);    case 0x3: return cy | z;
    case 0x4: return !cy;          case 0x5: return cy;
    case 0x6: return !z;           case 0x7: return z;
    case 0x8: return !v;           case 0x9: return v;
    case 0xA: return !n;           case 0xB: return n;
    case 0xC: return n == v;       case 0xD: return n != v;
    case 0xE: return !z && n == v; default: return z || n != v;
    }
}

static void push_all(mc6809 *c, int entire)
{
    push16s(c, c->pc);
    if (entire) {
        push16s(c, c->u); push16s(c, c->y); push16s(c, c->x);
        push8s(c, c->dp); push8s(c, c->b); push8s(c, c->a);
    }
    push8s(c, c->cc);
}

void mc6809_reset(mc6809 *c)
{
    c->dp = 0;
    c->cc = CC_I | CC_F;
    c->pc = rd16(c, 0xfffe);
    c->illegal = 0;
}

void mc6809_irq(mc6809 *c)
{
    if (c->cc & CC_I) return;
    c->cc |= CC_E;
    push_all(c, 1);
    c->cc |= CC_I;
    c->pc = rd16(c, 0xfff8);
    c->cycles += 19;
}

static int psh(mc6809 *c, uint8_t pb, uint16_t *sp, uint16_t other)
{
    int n = 0;
#define P8(v)  do { WR(--*sp, (v)); n++; } while (0)
#define P16(v) do { WR(--*sp, (v) & 0xff); WR(--*sp, (v) >> 8); n += 2; } while (0)
    if (pb & 0x80) P16(c->pc);
    if (pb & 0x40) P16(other);
    if (pb & 0x20) P16(c->y);
    if (pb & 0x10) P16(c->x);
    if (pb & 0x08) P8(c->dp);
    if (pb & 0x04) P8(c->b);
    if (pb & 0x02) P8(c->a);
    if (pb & 0x01) P8(c->cc);
    return n;
}
static int pul(mc6809 *c, uint8_t pb, uint16_t *sp, uint16_t *other)
{
    int n = 0;
#define L8()  (n++, RD((*sp)++))
    if (pb & 0x01) c->cc = L8();
    if (pb & 0x02) c->a = L8();
    if (pb & 0x04) c->b = L8();
    if (pb & 0x08) c->dp = L8();
    if (pb & 0x10) { uint8_t h = L8(); c->x = (uint16_t)(h << 8 | L8()); }
    if (pb & 0x20) { uint8_t h = L8(); c->y = (uint16_t)(h << 8 | L8()); }
    if (pb & 0x40) { uint8_t h = L8(); *other = (uint16_t)(h << 8 | L8()); }
    if (pb & 0x80) { uint8_t h = L8(); c->pc = (uint16_t)(h << 8 | L8()); }
    return n;
}

/* 8-bit ALU ops 0x80-0xFF column (low nibble), acc = A or B */
static void alu8(mc6809 *c, int lo, uint8_t *acc, uint8_t m)
{
    switch (lo) {
    case 0x0: *acc = sub8(c, *acc, m, 0); break;                        /* SUB */
    case 0x1: sub8(c, *acc, m, 0); break;                               /* CMP */
    case 0x2: *acc = sub8(c, *acc, m, c->cc & CC_C); break;             /* SBC */
    case 0x4: *acc &= m; nz8(c, *acc); break;                           /* AND */
    case 0x5: nz8(c, *acc & m); break;                                  /* BIT */
    case 0x6: *acc = m; nz8(c, m); break;                               /* LD  */
    case 0x8: *acc ^= m; nz8(c, *acc); break;                           /* EOR */
    case 0x9: *acc = add8(c, *acc, m, c->cc & CC_C); break;             /* ADC */
    case 0xA: *acc |= m; nz8(c, *acc); break;                           /* OR  */
    case 0xB: *acc = add8(c, *acc, m, 0); break;                        /* ADD */
    }
}

static int page2(mc6809 *c, uint8_t op);
static int page3(mc6809 *c, uint8_t op);

int mc6809_step(mc6809 *c)
{
    uint8_t op = fetch(c);
    int cyc = 0;
    uint16_t ea, w;
    uint8_t m;

    switch (op >> 4) {
    case 0x0: case 0x6: case 0x7: {                 /* memory RMW, JMP */
        int mode = op >> 4 == 0 ? 0 : op >> 4 == 6 ? 1 : 2;
        cyc = mode == 0 ? 6 : mode == 1 ? 6 : 7;
        ea = ea_mode(c, mode, &cyc);
        if ((op & 0xf) == 0xE) { c->pc = ea; cyc -= 3; break; }   /* JMP 3/3+/4 */
        m = RD(ea);
        if (rmw(c, op & 0xf, &m)) WR(ea, m);
        break;
    }
    case 0x1:
        switch (op) {
        case 0x10: return (int)(c->cycles += (cyc = page2(c, fetch(c))), cyc);
        case 0x11: return (int)(c->cycles += (cyc = page3(c, fetch(c))), cyc);
        case 0x12: cyc = 2; break;                                      /* NOP */
        case 0x13: cyc = 4; break;                                      /* SYNC (no wait in harness) */
        case 0x16: w = fetch16(c); c->pc += w; cyc = 5; break;          /* LBRA */
        case 0x17: w = fetch16(c); push16s(c, c->pc); c->pc += w; cyc = 9; break; /* LBSR */
        case 0x19: {                                                    /* DAA */
            unsigned a = c->a, cf = 0, lsn = a & 0xf, msn = a >> 4;
            if ((c->cc & CC_H) || lsn > 9) cf |= 0x06;
            if ((c->cc & CC_C) || msn > 9 || (msn > 8 && lsn > 9)) cf |= 0x60;
            unsigned r = a + cf;
            c->cc &= ~(CC_N | CC_Z | CC_V);
            if (r & 0x100) c->cc |= CC_C;
            c->a = (uint8_t)r;
            if (c->a & 0x80) c->cc |= CC_N;
            if (!c->a) c->cc |= CC_Z;
            cyc = 2; break;
        }
        case 0x1A: c->cc |= fetch(c); cyc = 3; break;                   /* ORCC */
        case 0x1C: c->cc &= fetch(c); cyc = 3; break;                   /* ANDCC */
        case 0x1D: c->a = (c->b & 0x80) ? 0xff : 0; nz16(c, D); cyc = 2; break; /* SEX */
        case 0x1E: { uint8_t pb = fetch(c); uint16_t r1 = getreg(c, pb >> 4), r2 = getreg(c, pb & 0xf);
                     setreg(c, pb >> 4, r2); setreg(c, pb & 0xf, r1); cyc = 8; break; }   /* EXG */
        case 0x1F: { uint8_t pb = fetch(c); setreg(c, pb & 0xf, getreg(c, pb >> 4)); cyc = 6; break; } /* TFR */
        default: c->illegal = op; cyc = 2;
        }
        break;
    case 0x2: {                                                         /* short branches */
        int8_t o = (int8_t)fetch(c);
        if (branch_cond(c, op)) c->pc = (uint16_t)(c->pc + o);
        cyc = 3; break;
    }
    case 0x3:
        cyc = 4;
        switch (op) {
        case 0x30: c->x = indexed(c, &cyc); c->cc &= ~CC_Z; if (!c->x) c->cc |= CC_Z; break;
        case 0x31: c->y = indexed(c, &cyc); c->cc &= ~CC_Z; if (!c->y) c->cc |= CC_Z; break;
        case 0x32: c->s = indexed(c, &cyc); break;
        case 0x33: c->u = indexed(c, &cyc); break;
        case 0x34: { uint8_t pb = fetch(c); cyc = 5 + psh(c, pb, &c->s, c->u); break; }
        case 0x35: { uint8_t pb = fetch(c); cyc = 5 + pul(c, pb, &c->s, &c->u); break; }
        case 0x36: { uint8_t pb = fetch(c); cyc = 5 + psh(c, pb, &c->u, c->s); break; }
        case 0x37: { uint8_t pb = fetch(c); cyc = 5 + pul(c, pb, &c->u, &c->s); break; }
        case 0x39: c->pc = pull16s(c); cyc = 5; break;                  /* RTS */
        case 0x3A: c->x += c->b; cyc = 3; break;                        /* ABX */
        case 0x3B:                                                      /* RTI */
            c->cc = pull8s(c); cyc = 6;
            if (c->cc & CC_E) {
                c->a = pull8s(c); c->b = pull8s(c); c->dp = pull8s(c);
                c->x = pull16s(c); c->y = pull16s(c); c->u = pull16s(c); cyc = 15;
            }
            c->pc = pull16s(c);
            break;
        case 0x3D: w = (uint16_t)(c->a * c->b); SETD(w); c->cc &= ~(CC_Z | CC_C);
                   if (!w) c->cc |= CC_Z; if (w & 0x80) c->cc |= CC_C; cyc = 11; break; /* MUL */
        case 0x3F: c->cc |= CC_E; push_all(c, 1); c->cc |= CC_I | CC_F; c->pc = rd16(c, 0xfffa); cyc = 19; break;
        default: c->illegal = op;
        }
        break;
    case 0x4: case 0x5: {                                               /* inherent A / B */
        uint8_t *acc = op >> 4 == 4 ? &c->a : &c->b;
        if (!rmw(c, op & 0xf, acc) && (op & 0xf) != 0xD) { /* TST ok, illegal flagged */ }
        cyc = 2; break;
    }
    default: {                                                          /* 0x8-0xF */
        int hi = op >> 4, lo = op & 0xf, mode = (hi & 3);  /* 0 imm, 1 dir, 2 idx, 3 ext */
        uint8_t *acc = hi < 0xC ? &c->a : &c->b;
        int is16 = (lo == 0x3 || lo == 0xC || lo == 0xE);
        if (hi < 0xC && lo == 0xD) {                                    /* BSR / JSR */
            if (mode == 0) { int8_t o = (int8_t)fetch(c); push16s(c, c->pc); c->pc = (uint16_t)(c->pc + o); cyc = 7; break; }
            cyc = mode == 1 ? 7 : mode == 2 ? 7 : 8;
            ea = ea_mode(c, mode - 1, &cyc);
            push16s(c, c->pc); c->pc = ea; break;
        }
        if (lo == 0x7 || (lo == 0xD && hi >= 0xC) || lo == 0xF) {         /* stores */
            if (mode == 0) { c->illegal = op; cyc = 2; break; }
            cyc = (lo == 0x7) ? (mode == 3 ? 5 : 4) : (mode == 3 ? 6 : 5);
            ea = ea_mode(c, mode - 1, &cyc);
            if (lo == 0x7) { WR(ea, *acc); nz8(c, *acc); }
            else {
                uint16_t v = lo == 0xD ? D : hi < 0xC ? c->x : c->u;
                wr16(c, ea, v); nz16(c, v);
            }
            break;
        }
        if (is16) {
            int alu = (lo == 0x3);                                       /* SUBD/ADDD vs CMPX/LD */
            if (mode == 0) { w = fetch16(c); cyc = (lo == 0xC && hi < 0xC) || alu ? 4 : 3; }
            else { cyc = mode == 1 ? 5 : mode == 2 ? 5 : 6; if (alu || (lo == 0xC && hi < 0xC)) cyc++;
                   ea = ea_mode(c, mode - 1, &cyc); w = rd16(c, ea); }
            if (lo == 0x3) { if (hi < 0xC) SETD(sub16(c, D, w)); else SETD(add16(c, D, w)); }
            else if (lo == 0xC) { if (hi < 0xC) sub16(c, c->x, w); else { SETD(w); nz16(c, w); } }
            else { if (hi < 0xC) c->x = w; else c->u = w; nz16(c, w); }
            break;
        }
        if (mode == 0) { m = fetch(c); cyc = 2; }
        else { cyc = mode == 3 ? 5 : 4; ea = ea_mode(c, mode - 1, &cyc); m = RD(ea); }
        if (lo == 0x7 || lo == 0xD || lo == 0xF) { c->illegal = op; break; }
        alu8(c, lo, acc, m);
        break;
    }
    }
    c->cycles += (unsigned)cyc;
    return cyc;
}

/* 16-bit compare/load/store for pages 2 and 3 */
static int page2(mc6809 *c, uint8_t op)
{
    int cyc = 0;
    uint16_t ea, w;
    if (op >= 0x21 && op <= 0x2F) {                                     /* long branches */
        w = fetch16(c);
        if (branch_cond(c, op)) { c->pc += w; return 6; }
        return 5;
    }
    if (op == 0x3F) { c->cc |= CC_E; push_all(c, 1); c->pc = rd16(c, 0xfff4); return 20; }
    int hi = op >> 4, lo = op & 0xf, mode = hi & 3;
    if (lo == 0x3 || lo == 0xC) {                                       /* CMPD / CMPY */
        if (mode == 0) { w = fetch16(c); cyc = 5; }
        else { cyc = mode == 3 ? 8 : 7; ea = ea_mode(c, mode - 1, &cyc); w = rd16(c, ea); }
        sub16(c, lo == 3 ? D : c->y, w);
        return cyc;
    }
    if (lo == 0xE) {                                                    /* LDY / LDS */
        if (mode == 0) { w = fetch16(c); cyc = 4; }
        else { cyc = mode == 3 ? 7 : 6; ea = ea_mode(c, mode - 1, &cyc); w = rd16(c, ea); }
        if (hi < 0xC) c->y = w; else c->s = w;
        nz16(c, w);
        return cyc;
    }
    if (lo == 0xF && mode) {                                            /* STY / STS */
        cyc = mode == 3 ? 7 : 6;
        ea = ea_mode(c, mode - 1, &cyc);
        w = hi < 0xC ? c->y : c->s;
        wr16(c, ea, w); nz16(c, w);
        return cyc;
    }
    c->illegal = 0x1000 | op;
    return 2;
}

static int page3(mc6809 *c, uint8_t op)
{
    int cyc = 0;
    uint16_t ea, w;
    if (op == 0x3F) { c->cc |= CC_E; push_all(c, 1); c->pc = rd16(c, 0xfff2); return 20; }
    int lo = op & 0xf, mode = (op >> 4) & 3;
    if (lo == 0x3 || lo == 0xC) {                                       /* CMPU / CMPS */
        if (mode == 0) { w = fetch16(c); cyc = 5; }
        else { cyc = mode == 3 ? 8 : 7; ea = ea_mode(c, mode - 1, &cyc); w = rd16(c, ea); }
        sub16(c, lo == 3 ? c->u : c->s, w);
        return cyc;
    }
    c->illegal = 0x1100 | op;
    return 2;
}
