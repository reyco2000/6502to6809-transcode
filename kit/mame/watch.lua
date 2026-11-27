-- Long-run watchdog (kit runtime, any game).  Boots with CMD, plays INPUT (input.lua), and prints every 1000 frames
-- "T <frame> tick <rt_tick> flips <v_flips>".  When the IRQ tick stops for STALL frames (default 600: a level or
-- segment load with interrupts off takes ~100) it prints "STALL ..." with the CPU registers and the task-0 MMU map,
-- samples them every 10 frames for 300 frames, then exits.  Env:
--   KIT, CMD, INPUT   as input.lua;  FRAMES  stop at T = FRAMES
--   VFLIPS            v_flips address (hex, main.sym; default E01B)
--   POKE / POKEN      optional, e.g. POKE=3E:1F2B:02 POKEN=50: every POKEN frames from T = POKET (default 0) write
--                     value 02 at offset 1F2B of 8K block 3E (a cheat such as infinite lives, for long runs that
--                     must reach late game events).  Several: comma-separated.  The block is mapped in slot 2 of
--                     BOTH tasks for the write: the frame callback may run while the runtime is in task 1 (video
--                     converter), and a write through task 1's slot 2 would land in whatever block it maps there.
-- Runtime variables: rt_tick $E007.  MMU registers read back with floating-bus bits 6-7: AND $3F.
local f = 0
local m = manager.machine
local cpu = m.devices[':maincpu']
local sp = cpu.spaces['program']
local IN = dofile((os.getenv('KIT') or '.') .. '/kit/mame/input.lua')
local last = tonumber(os.getenv('FRAMES') or '6000')
local stall_n = tonumber(os.getenv('STALL') or '600')
local vflips = tonumber(os.getenv('VFLIPS') or 'E01B', 16)
local pokes = {}
for blk, off, val in (os.getenv('POKE') or ''):gmatch('(%x+):(%x+):(%x+)') do
  pokes[#pokes + 1] = {tonumber(blk, 16), tonumber(off, 16), tonumber(val, 16)}
end
local poken, poket = tonumber(os.getenv('POKEN') or '50'), tonumber(os.getenv('POKET') or '0')
local lt, same, stop = -1, 0, nil
local function state()
  local s = cpu.state
  local mmu = {}
  for i = 0, 7 do mmu[#mmu + 1] = string.format('%02X', sp:read_u8(0xFFA0 + i) & 0x3F) end
  return string.format('pc=%04X s=%04X cc=%02X dp=%02X a=%02X b=%02X x=%04X y=%04X u=%04X mmu0=%s',
    s['PC'].value, s['S'].value, s['CC'].value, s['DP'].value, s['A'].value, s['B'].value, s['X'].value,
    s['Y'].value, s['U'].value, table.concat(mmu))
end
emu.register_frame_done(function() f = f + 1
  local t = IN.frame(f)
  if not t then return end
  if #pokes > 0 and t >= poket and t % poken == 0 and not stop then
    local o0, o1 = sp:read_u8(0xFFA2) & 0x3F, sp:read_u8(0xFFAA) & 0x3F
    for _, p in ipairs(pokes) do
      sp:write_u8(0xFFA2, p[1]) sp:write_u8(0xFFAA, p[1]) sp:write_u8(0x4000 + p[2], p[3])
    end
    sp:write_u8(0xFFA2, o0) sp:write_u8(0xFFAA, o1)
  end
  local tk = sp:read_u16(0xE007)
  if tk == lt then same = same + 1 else same = 0 end
  lt = tk
  if t % 1000 == 0 then print(string.format('T %d tick %d flips %d', t, tk, sp:read_u16(vflips))) io.stdout:flush() end
  if same == stall_n and not stop then print(string.format('STALL T %d tick %d %s', t, tk, state())) stop = t + 300 end
  if stop and t % 10 == 0 then print(string.format('  T %d tick %d %s', t, tk, state())) io.stdout:flush() end
  if (stop and t >= stop) or t >= last then m:exit() end end)
