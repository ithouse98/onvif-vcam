#!/usr/bin/env bash
# Builds the full installer dist/ONVIF-VCam-Setup-<ver>.exe (server + Windows service + tray +
# firewall + shortcuts) on Linux with NSIS, from the portable bundle folder made by
# scripts/build_portable.sh (run that first). Needs: makensis (apt install nsis).
set -euo pipefail
cd "$(dirname "$0")/.."
VER=$(python3 -c "import vcam;print(vcam.__version__)")
SRC=${1:-dist/ONVIF-VCam-$VER}
[ -x "$SRC/runtime/python.exe" ] || { echo "no portable bundle in $SRC: run scripts/build_portable.sh"; exit 1; }
cp vcam/web/assets/vcam.ico installer/
makensis -V2 -DVERSION="$VER" -DSRC="$PWD/$SRC" -DOUTFILE="$PWD/dist/ONVIF-VCam-Setup-$VER.exe" installer/vcam-setup.nsi
echo "built dist/ONVIF-VCam-Setup-$VER.exe"
