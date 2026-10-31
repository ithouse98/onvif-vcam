#!/usr/bin/env bash
# Builds the offline Windows x64 bundle (dist/ONVIF-VCam-<ver>-Portable-win64.zip) on Linux.
# Python 3.12 + GStreamer (x264, D3D11/GDI screen capture, Media Foundation/KS webcams, PyGObject)
# + GPL ffmpeg (libx265, ffprobe) + VC runtime come from conda-forge (win-64),
# the test-client wheels from PyPI. Needs: curl, tar, bzip2, zip, pip, ffmpeg (for the sample clip).
set -euo pipefail
cd "$(dirname "$0")/.."
VER=$(python3 -c "import vcam;print(vcam.__version__)")
OUT=dist/ONVIF-VCam-$VER
rm -rf "$OUT" && mkdir -p "$OUT"/{app,videos} dist/tools
if [ ! -x dist/tools/bin/micromamba ]; then
  curl -sL https://conda.anaconda.org/conda-forge/linux-64/micromamba-2.9.0-0.tar.bz2 | tar xj -C dist/tools bin/micromamba
fi
MAMBA_ROOT_PREFIX=$PWD/dist/tools/root dist/tools/bin/micromamba create -y --platform win-64 \
  -p "$OUT/runtime" -c conda-forge "python=3.12" "ffmpeg=*=gpl*" pygobject gstreamer \
  gst-plugins-base gst-plugins-good gst-plugins-bad gst-plugins-ugly gst-libav
# trim developer files (~170 MB)
( cd "$OUT/runtime" && rm -rf Library/include Library/share/{doc,man,gtk-doc,info,locale} Lib/test Lib/idlelib conda-meta
  find . -name "*.pdb" -delete; find Library/lib -maxdepth 1 \( -name "*.lib" -o -name "*.a" \) -delete
  find Library/bin -maxdepth 1 -name "*.exe" ! -name ffmpeg.exe ! -name ffprobe.exe ! -name ffplay.exe \
    ! -name "gst-launch-1.0.exe" ! -name "gst-inspect-1.0.exe" ! -name "gst-device-monitor-1.0.exe" -delete
  # GStreamer: only the plugins vcam uses (faster first-run registry scan, fewer failing DLLs)
  keep=" coreelements app playback typefindfunctions videoconvertscale videorate isomp4 matroska avi
    mpegtsdemux flv x264 jpeg y4m videotestsrc videoparsersbad libav rtsp rtp rtpmanager udp tcp soup
    adaptivedemux2 d3d11 d3d winscreencap mediafoundation winks directshow pbtypes autodetect "
  for f in Library/lib/gstreamer-1.0/*; do
    n=$(basename "$f"); n=${n#gst}; n=${n%.*}
    case "$keep" in *" $n "*) [[ $f == *.dll ]] || rm -rf "$f";; *) rm -rf "$f";; esac
  done
  rm -rf Library/libexec/gstreamer-1.0/gst-hotdoc-plugins-scanner.exe Library/libexec/gstreamer-1.0/gst-plugins-doc-cache-generator )
# test client (onvif-zeep-async) as Windows wheels, unpacked into site-packages
pip download -q --only-binary=:all: --platform win_amd64 --python-version 3.12 --implementation cp \
  -d dist/wheels onvif-zeep-async
for w in dist/wheels/*.whl; do python3 -c "import zipfile,sys;zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])" "$w" "$OUT/runtime/Lib/site-packages"; done
cp -r vcam tests "$OUT/app/"; cp windows/* "$OUT/"; cp README.md "$OUT/README-EN.md"; cp config.example.json "$OUT/"
find "$OUT" -name __pycache__ -prune -exec rm -rf {} +
ffmpeg -v error -y -f lavfi -i "testsrc2=size=1920x1080:rate=25" -t 60 \
  -vf "drawtext=text='ONVIF-VCam sample  %{pts\:hms}':fontsize=64:fontcolor=white:x=40:y=40:box=1:boxcolor=black@0.6" \
  -c:v libx264 -preset slow -crf 26 -bf 0 -g 50 -pix_fmt yuv420p -movflags +faststart "$OUT/videos/sample_1080p.mp4"
( cd dist && rm -f "ONVIF-VCam-$VER-Portable-win64.zip" && zip -q -9 -r "ONVIF-VCam-$VER-Portable-win64.zip" "ONVIF-VCam-$VER" )
echo "built dist/ONVIF-VCam-$VER-Portable-win64.zip"
