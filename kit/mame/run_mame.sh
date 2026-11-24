#!/bin/sh
# run_mame.sh DSK "COMMAND" OUTDIR SNAPFRAMES... -- boot a CoCo 3 (RAM=512k default, or RAM=128k) in MAME,
# type COMMAND at BASIC, save a PNG at each listed frame (60 per second),
# exit after the last one.  Headless and unthrottled.
#   env: PEEK=hexaddr prints that 16-bit word at each snapshot
#        MONITOR=0 composite, 1 RGB (default)
#        MAME (default ~/proyectos/mame/mame), ROMPATH (default ~/opt/mameroms)
MAME=${MAME:-$HOME/proyectos/mame/mame}
ROMPATH=${ROMPATH:-$HOME/opt/mameroms}
DSK=$(realpath "$1"); CMD=$2; OUT=$(realpath -m "$3"); shift 3
mkdir -p "$OUT"
LUA="$OUT/run.lua"
{
  echo 'local f=0 local snaps={'"$(echo "$@" | tr ' ' ',')"'}'
  echo 'local last=snaps[#snaps]'
  echo 'emu.register_frame_done(function() f=f+1'
  echo "  if f==2 then local p=manager.machine.ioport.ports[':screen_config'] if p then for _,fl in pairs(p.fields) do fl:set_value(${MONITOR:-1}) end end end"
  printf '%s\n' "  if f==120 then manager.machine.natkeyboard:post('$CMD'..string.char(13)) end"
  echo '  for _,s in ipairs(snaps) do if f==s then manager.machine.video:snapshot()'
  [ -n "$PEEK" ] && echo "    print(string.format('frame %d peek %04x', f, manager.machine.devices[':maincpu'].spaces['program']:read_u16(0x$PEEK)))"
  echo "    local st=manager.machine.devices[':maincpu'].state print(string.format('frame %d PC=%04X S=%04X DP=%02X', f, st['PC'].value, st['S'].value, st['DP'].value))"
  echo '  end end'
  echo '  if f>=last then manager.machine:exit() end end)'
} > "$LUA"
rm -rf "$OUT/snap"
exec "$MAME" coco3 -rompath "$ROMPATH" -ram ${RAM:-512k} -flop1 "$DSK" -nothrottle \
  -video none -sound none -snapshot_directory "$OUT/snap" -autoboot_script "$LUA" \
  -skip_gameinfo -nomouse 2>&1 | grep -v "doesn't make much sense"
