#!/usr/bin/env bash
# Fetch / samplehaku – tests and performance.
#   testit/aja.sh                 all tests + performance
#   testit/aja.sh tests           tests only
#   testit/aja.sh performance [--warm]
#   testit/aja.sh accuracy [--examples N]   detection accuracy (read-only, ~40 s), not part of "all"
# (The original Finnish modes testit / suorituskyky / --lämmin also work.)
# Settings files (corrections, genres, window layout) are always backed up and restored.
set -u
D="$(cd "$(dirname "$0")" && pwd)"
R="$(dirname "$D")"
CFG="$HOME/.config/samplehaku"
BAK="$(mktemp -d)"
# korjausloki.jsonl (correction log): tests write to it through the real service -> restored, or removed if it was not there
for f in korjaukset.json genret.json ikkuna.json korjausloki.jsonl; do [ -f "$CFG/$f" ] && cp "$CFG/$f" "$BAK/"; done
restore() {
  for f in korjaukset.json genret.json ikkuna.json; do
    if [ -f "$BAK/$f" ]; then cp "$BAK/$f" "$CFG/$f"; fi
  done
  if [ -f "$BAK/korjausloki.jsonl" ]; then cp "$BAK/korjausloki.jsonl" "$CFG/"; else rm -f "$CFG/korjausloki.jsonl"; fi
  rm -rf "$BAK"
}
trap restore EXIT
mode="${1:-all}"
case "$mode" in
  kaikki) mode=all ;;
  testit) mode=tests ;;
  suorituskyky) mode=performance ;;
  tarkkuus) mode=accuracy ;;
esac
rc=0
if [ "$mode" = all ] || [ "$mode" = tests ]; then
  echo -e "\n\033[1;36m### 1/5 Engine\033[0m";   "$R/.venv/bin/python" -I "$D/test_moottori.py" || rc=1
  echo -e "\n\033[1;36m### 1b/5 Library roots, merge, relocate\033[0m"; "$R/.venv/bin/python" -I "$D/test_siirto.py" || rc=1
  echo -e "\n\033[1;36m### 1c/5 Background indexer (Add folder)\033[0m"; "$R/.venv/bin/python" -I "$D/test_indeksoija.py" || rc=1
  echo -e "\n\033[1;36m### 2/5 Service\033[0m";  /usr/bin/python3 -I "$D/test_palvelu.py" || rc=1
  echo -e "\n\033[1;36m### 3/5 Collections and Files\033[0m"; /usr/bin/python3 -I "$D/test_kokoelmat.py" || rc=1
  echo -e "\n\033[1;36m### 4/5 Window\033[0m";   /usr/bin/python3 -I "$D/test_ikkuna.py" 2> >(grep -v "frame_timings\|^$" >&2) || rc=1
  echo -e "\n\033[1;36m### 5/5 Installation (GNOME, Files, launchers)\033[0m"; /usr/bin/python3 -I "$D/test_asennus.py" || rc=1
fi
if [ "$mode" = all ] || [ "$mode" = performance ]; then
  echo -e "\n\033[1;36m### Performance\033[0m";   /usr/bin/python3 -I "$D/suorituskyky.py" "${@:2}" || rc=1
fi
if [ "$mode" = accuracy ]; then
  echo -e "\n\033[1;36m### Detection accuracy\033[0m"; "$R/.venv/bin/python" -I "$D/tarkkuus.py" "${@:2}" || rc=1
fi
echo
[ $rc = 0 ] && echo -e "\033[1;32mALL OK\033[0m" || echo -e "\033[1;31mFAILURES – see above\033[0m"
exit $rc
