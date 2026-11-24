-- Shared input driver for the MAME probes (game-independent).  Loaded with dofile() by prof.lua / inv*.lua.
-- Env:
--   CMD    command typed at BASIC at frame 120 (default LOADM"GAME":EXEC; the port's loader name)
--   INPUT  input script file (none = no input: attract mode).  One event per line, T = frames after game start:
--            T key TEXT          type TEXT (the word SPACE = ' ', ENTER = CR, ESC = break key)
--            T joyx V            right joystick X, 0..1023 (512 = centre)
--            T joyy V            right joystick Y
--            T btn 1|0           right joystick button (Apple button 0) pressed / released
--            T1-T2/N keys CHARS  from T1 to T2, every N frames, type the next char of CHARS (cyclic)
--            T1-T2/N joyx A,B,C  from T1 to T2, every N frames, the next X value of the list (cyclic)
--          '#' starts a comment.
-- Game start = the first frame after 300 where the runtime's rt_tick ($E007) is 1..3 and $E000 holds JMP.
local M = {}
local m = manager.machine
local sp = m.devices[':maincpu'].spaces['program']
local jp = m.ioport.ports[':joystick_rx']
local fx, fy = jp.fields['Right Joystick X'], m.ioport.ports[':joystick_ry'].fields['Right Joystick Y']
local ev, rep = {}, {}
local cmd = os.getenv('CMD') or 'LOADM"GAME":EXEC'

local function txt(s)
  return (s:gsub('SPACE', ' '):gsub('ENTER', string.char(13)):gsub('ESC', string.char(27)))
end
local path = os.getenv('INPUT')
if path and path ~= '' then
  for line in io.lines(path) do
    line = line:gsub('#.*', '')
    local a, b, n, kind, arg = line:match('^%s*(%d+)%-(%d+)/(%d+)%s+(%a+)%s+(.-)%s*$')
    if a then
      local list = {}
      if kind == 'keys' then for c in arg:gmatch('.') do list[#list + 1] = c end
      else for v in arg:gmatch('[^,]+') do list[#list + 1] = tonumber(v) end end
      rep[#rep + 1] = {t0 = tonumber(a), t1 = tonumber(b), n = tonumber(n), kind = kind, list = list}
    else
      local t, k, v = line:match('^%s*(%d+)%s+(%a+)%s+(.-)%s*$')
      if t then ev[#ev + 1] = {t = tonumber(t), kind = k, v = v} end
    end
  end
end

local fb = nil                       -- the right joystick's button field (port names differ between MAME versions)
for _, p in pairs(m.ioport.ports) do
  for name, fl in pairs(p.fields) do
    if not fb and name:match('Right') and name:match('Button') then fb = fl end
  end
end

local function apply(kind, v)
  if kind == 'key' or kind == 'keys' then m.natkeyboard:post(txt(v))
  elseif kind == 'joyx' then fx:set_value(tonumber(v))
  elseif kind == 'joyy' then fy:set_value(tonumber(v))
  elseif kind == 'btn' and fb then fb:set_value(tonumber(v)) end
end

M.g = nil
-- call once per frame with the frame number; returns T (frames since game start) or nil before it
function M.frame(f)
  if f == 120 then m.natkeyboard:post(cmd .. string.char(13)) end
  if M.g == nil and f > 300 then
    local t = sp:read_u16(0xE007)
    if t >= 1 and t <= 3 and sp:read_u8(0xE000) == 0x7E then M.g = f end
  end
  if not M.g then return nil end
  local t = f - M.g
  for _, e in ipairs(ev) do if e.t == t then apply(e.kind, e.v) end end
  for _, r in ipairs(rep) do
    if t >= r.t0 and t <= r.t1 and (t - r.t0) % r.n == 0 then
      local i = ((t - r.t0) // r.n) % #r.list + 1
      apply(r.kind, r.list[i])
    end
  end
  return t
end
return M
