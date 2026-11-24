-- GIME invariant probe (512K runtime; any game).  Env OUT = output file, KIT, CMD, INPUT (input.lua).  Boots with CMD,
-- waits for the game (rt_tick = 1..3), plays INPUT, and every 17 frames from T=1000 outside a
-- conversion (S below $E000) dumps one page (alternating): 4-byte header (page, shown page, T lo, T hi),
-- its shadow (8K), its GIME buffer (32K).  Check with invcheck.py.  Runtime variables: rt_tick $E007,
-- v_shown $E017.  MMU registers read back with floating-bus bits 6-7: restore them AND $3F.
local f, g, n = 0, nil, 0
local sp = manager.machine.devices[':maincpu'].spaces['program']
local IN = dofile((os.getenv('KIT') or '.') .. '/kit/mame/input.lua')
local out = io.open(os.getenv('OUT'), 'wb')
local last = tonumber(os.getenv('FRAMES') or '4000')
local function rdblk(blk, len)
  local o0, o1 = sp:read_u8(0xFFA2) & 0x3F, sp:read_u8(0xFFAA) & 0x3F
  sp:write_u8(0xFFA2, blk) sp:write_u8(0xFFAA, blk)
  local t = {} for i = 0, len - 1 do t[#t + 1] = string.char(sp:read_u8(0x4000 + i)) end
  sp:write_u8(0xFFA2, o0) sp:write_u8(0xFFAA, o1)
  return table.concat(t)
end
emu.register_frame_done(function() f = f + 1
  IN.frame(f) g = IN.g
  if g and (f - g) >= 1000 and (f - g) % 17 == 0 then
    if manager.machine.devices[':maincpu'].state['S'].value < 0xE000 then
      local pg = n % 2
      out:write(string.char(pg, sp:read_u8(0xE017), (f - g) % 256, math.floor((f - g) / 256)))
      out:write(rdblk(0x08 + pg, 8192)) for b = 0, 3 do out:write(rdblk(pg * 4 + b, 8192)) end
      n = n + 1
    end
  end
  if g and f - g >= last then out:close() print('samples', n, 'game start frame', g) manager.machine:exit() end end)
