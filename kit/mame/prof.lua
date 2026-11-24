-- Profile / timing probe (kit runtime, any game).  Boots the disk with CMD, waits for the game (rt_tick = 1..3) and
-- plays the INPUT script (input.lua; none = attract mode).  Env (plus KIT = kit repo, CMD, INPUT: see input.lua):
--   OUT    directory for trace.log / snapshots
--   FRAMES stop at T = FRAMES
--   TRACE  "start,len": MAME debugger trace of maincpu from T=start for len frames (needs -debug -debugger none)
--   SNAP   every n frames a snapshot (needs video; with -video none snapshots still render)
--   FLIPS  every n frames print T, v_flips (count of $C054/$C055 accesses), rt_tick
-- Runtime variables: rt_tick $E007, v_flips (env VFLIPS, hex, default E01B).
local f, g = 0, nil
local m = manager.machine
local sp = m.devices[':maincpu'].spaces['program']
local IN = dofile((os.getenv('KIT') or '.') .. '/kit/mame/input.lua')
local out = os.getenv('OUT') or '.'
local last = tonumber(os.getenv('FRAMES') or '4000')
local snap = tonumber(os.getenv('SNAP') or '0')
local flips = tonumber(os.getenv('FLIPS') or '0')
local vflips = tonumber(os.getenv('VFLIPS') or 'E01B', 16)   -- v_flips address (main.sym)
local tr0, trn = (os.getenv('TRACE') or ''):match('(%d+),(%d+)')
tr0, trn = tonumber(tr0), tonumber(trn)
emu.register_frame_done(function() f = f + 1
  local t = IN.frame(f)
  if not t then return end
  if flips > 0 and t % flips == 0 then print(string.format('T %d flips %d tick %d', t, sp:read_u16(vflips), sp:read_u16(0xE007))) end
  if snap > 0 and t % snap == 0 then m.video:snapshot() end
  if tr0 and t == tr0 then m.debugger:command('trace ' .. out .. '/trace.log,maincpu,noloop') end
  if tr0 and t == tr0 + trn then m.debugger:command('trace off') end
  if t >= last then m:exit() end end)
