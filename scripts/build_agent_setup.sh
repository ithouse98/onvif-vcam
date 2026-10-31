#!/usr/bin/env bash
# Builds the screen agent installer dist/ONVIF-VCam-Agent-Setup-<ver>.exe on Linux (NSIS).
# Payload: Python 3.12 + PyGObject + pywin32 + GStreamer capture/encode plugins (conda-forge win-64)
# and the vcam package; no ffmpeg / decoders / videos (the agent only captures and encodes).
# Needs: curl, tar, bzip2, makensis (apt install nsis).
set -euo pipefail
cd "$(dirname "$0")/.."
VER=$(python3 -c "import vcam;print(vcam.__version__)")
SRC=dist/agent-payload
rm -rf "$SRC" && mkdir -p "$SRC/app" dist/tools
if [ ! -x dist/tools/bin/micromamba ]; then
  curl -sL https://conda.anaconda.org/conda-forge/linux-64/micromamba-2.9.0-0.tar.bz2 | tar xj -C dist/tools bin/micromamba
fi
MAMBA_ROOT_PREFIX=$PWD/dist/tools/root dist/tools/bin/micromamba create -y -q --platform win-64 \
  -p "$SRC/runtime" -c conda-forge "python=3.12" pygobject pywin32 gstreamer gst-plugins-base \
  gst-plugins-good gst-plugins-bad gst-plugins-ugly
( cd "$SRC/runtime" && rm -rf Library/include Library/share/{doc,man,gtk-doc,info,locale} Lib/test Lib/idlelib \
    Lib/tkinter Lib/turtledemo Library/lib/tcl* Library/lib/tk* conda-meta Scripts/*.exe
  find . -name "*.pdb" -delete; find Library/lib -maxdepth 1 \( -name "*.lib" -o -name "*.a" \) -delete
  find Library/bin -maxdepth 1 -name "*.exe" -delete
  keep=" coreelements app playback typefindfunctions videoconvertscale videorate compositor x264 jpeg y4m
    videoparsersbad mpegtsmux tcp d3d11 d3d winscreencap pbtypes videotestsrc "
  for f in Library/lib/gstreamer-1.0/*; do
    n=$(basename "$f"); n=${n#gst}; n=${n%.*}
    case " $(echo $keep) " in *" $n "*) [[ $f == *.dll ]] || rm -rf "$f";; *) rm -rf "$f";; esac
  done
  rm -rf Library/libexec/gstreamer-1.0/gst-hotdoc-plugins-scanner.exe Library/libexec/gstreamer-1.0/gst-plugins-doc-cache-generator )
cp -r vcam "$SRC/app/"
rm -rf "$SRC/app/vcam/web/fonts" "$SRC/app/vcam/web/index.html"
find "$SRC" -name __pycache__ -prune -exec rm -rf {} +
cp vcam/web/assets/vcam.ico installer/
makensis -V2 -DVERSION="$VER" -DSRC="$PWD/$SRC" -DOUTFILE="$PWD/dist/ONVIF-VCam-Agent-Setup-$VER.exe" installer/agent-setup.nsi
echo "built dist/ONVIF-VCam-Agent-Setup-$VER.exe"
