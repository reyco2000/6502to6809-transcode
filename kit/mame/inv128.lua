-- GIME invariant probe (128K runtime: one GIME buffer, no shadow; any game).  Env OUT = output file, KIT, CMD,
-- INPUT (input.lua).  Boots with CMD, waits for the game (rt_tick = 1..3), plays INPUT, and every 17 frames from T=1000, while the game runs (S below $4000: the game stack) and the shown page is settled,
-- dumps: 4-byte header (sample parity, shown page, T lo, T hi), the shown Apple page (8K) and the GIME buffer
-- (32K).  Check with invcheck.py (GIME = conversion of the dumped page).  Runtime variables: rt_tick $E007,
-- v_shown $E017.  MMU registers read back with floating-bus bits 6-7: restore them AND $3F.
-- Env: GBLK (first GIME block), APBLK (block of hi-res page 1), VRN (vr_n1), VPON (vp_on, vp_dst follows),
-- VREF (v_ref), HRD (hr_d1, when the runtime has HIRES_ACCESS: pending whole-page marks), hex.
local f, g, n = 0, nil, 0
local sp = manager.machine.devices[':maincpu'].spaces['program']
local IN = dofile((os.getenv('KIT') or '.') .. '/kit/mame/input.lua')
local out = io.open(os.getenv('OUT'), 'wb')
local last = tonumber(os.getenv('FRAMES') or '4000')
local gblk = tonumber(os.getenv('GBLK') or '00', 16)
local apblk = tonumber(os.getenv('APBLK') or '11', 16)
local vrn, vpon = tonumber(os.getenv('VRN') or '0', 16), tonumber(os.getenv('VPON') or '0', 16)
local skipped = 0
local vref = tonumber(os.getenv('VREF') or '0', 16)
local hrd = tonumber(os.getenv('HRD') or '0', 16)   -- hr_d1 (HIRES_ACCESS ports): page written by hr_wr, not yet marked
local function rdblk(blk, len)
  local o0, o1 = sp:read_u8(0xFFA2) & 0x3F, sp:read_u8(0xFFAA) & 0x3F
  sp:write_u8(0xFFA2, blk) sp:write_u8(0xFFAA, blk)
  local t = {} for i = 0, len - 1 do t[#t + 1] = string.char(sp:read_u8(0x4000 + i)) end
  sp:write_u8(0xFFA2, o0) sp:write_u8(0xFFAA, o1)
  return table.concat(t)
end
emu.register_frame_done(function() f = f + 1
  IN.frame(f) g = IN.g
  -- optional jump (needs -debug): env JUMP = target pc, AT = pc of a breakpoint, POKE = debugger expr
  -- (e.g. b@341=2), GO = T.  At the next hit of AT: POKE, then pc = JUMP.  (Ports use it to jump to a level.)
  if g and os.getenv('JUMP') and (f - g) == tonumber(os.getenv('GO')) then
    manager.machine.debugger:command('bpset ' .. os.getenv('AT') .. ',1,{' .. (os.getenv('POKE') or '') .. ';pc=' .. os.getenv('JUMP') .. ';bpclear;g}') end
  if g and (f - g) >= 1000 and (f - g) % 17 == 0 then
    -- no shadow (128K target): when the shown page is settled (converted last = v_ref, its list empty, no pending
    -- scroll of it), the GIME buffer must equal the conversion of that Apple page.  Env VREF (v_ref), hex.
    local shown = sp:read_u8(0xE017)
    if manager.machine.devices[':maincpu'].state['S'].value < 0x4000 and shown < 2 and sp:read_u8(vref) == shown
       and sp:read_u8(vrn + shown) == 0 and (hrd == 0 or sp:read_u8(hrd + shown) == 0) and not (sp:read_u8(vpon) ~= 0 and sp:read_u8(vpon + 1) == shown) then
      out:write(string.char(n % 2, shown, (f - g) % 256, math.floor((f - g) / 256)))
      out:write(rdblk(apblk + shown, 8192)) for b = 0, 3 do out:write(rdblk(gblk + b, 8192)) end
      n = n + 1
    else skipped = skipped + 1 end
  end
  if g and f - g >= last then out:close() print('samples', n, 'game start frame', g, 'not settled', skipped) manager.machine:exit() end end)
