#!/usr/bin/env bash
# Emulator end-to-end test of the Android agent against a VCam server running on the CI host
# (the emulator reaches the host as 10.0.2.2). Results -> $RES.
set -x
RES=${RES:-/tmp/results}
APK=$(ls "$GITHUB_WORKSPACE"/apk/*.apk | head -1)
API="curl -s -u admin:admin http://127.0.0.1:8000"
adb install -r "$APK" > "$RES/install.txt" 2>&1
adb shell pm grant com.ipsco.vcamagent android.permission.CAMERA
PKG=com.ipsco.vcamagent
shot() { adb exec-out screencap -p > "$RES/ui_$1.png"; }
adb shell am start -n $PKG/.SplashActivity
sleep 1.2; shot 1_splash
sleep 4
adb shell am start -n com.ipsco.vcamagent/.MainActivity --es server 10.0.2.2 --es height 720 --es fps 25 --es start true
ID=""
for i in $(seq 1 60); do
  ID=$($API/api/agents | python3 -c "import json,sys; a=[x for x in json.load(sys.stdin) if x['online']]; print(a[0]['id'] if a else '')")
  [ -n "$ID" ] && break
  sleep 2
done
$API/api/agents > "$RES/agents.json"
echo "agent id: $ID" >> "$RES/summary.txt"
if [ -n "$ID" ]; then
  $API/api/cameras -X POST -H "Content-Type: application/json" -d "{\"name\":\"Phone\",\"source\":\"agent://$ID/0\"}" > "$RES/add.json"
  CID=$(python3 -c "import json; print(json.load(open('$RES/add.json'))['ids'][0])")
  echo "camera: $CID $(cat "$RES/add.json")" >> "$RES/summary.txt"
  sleep 3
  timeout 90 ffmpeg -v error -timeout 30000000 -rtsp_transport tcp -i "rtsp://admin:admin@127.0.0.1:8554/$CID/Profile_1" -t 12 -c copy -y /tmp/phone.mkv 2> "$RES/ffmpeg.txt"
  echo "main: $(ffprobe -v error -count_frames -select_streams v -show_entries stream=codec_name,width,height,nb_read_frames -of csv=p=0 /tmp/phone.mkv 2>&1)" >> "$RES/summary.txt"
  ffmpeg -v error -ss 6 -i /tmp/phone.mkv -frames:v 1 -y "$RES/phone_frame.jpg"
  timeout 60 ffmpeg -v error -timeout 30000000 -rtsp_transport tcp -i "rtsp://admin:admin@127.0.0.1:8554/$CID/Profile_2" -t 6 -c copy -y /tmp/phone_sub.mkv 2>> "$RES/ffmpeg.txt"
  echo "sub: $(ffprobe -v error -count_frames -select_streams v -show_entries stream=codec_name,width,height,nb_read_frames -of csv=p=0 /tmp/phone_sub.mkv 2>&1)" >> "$RES/summary.txt"
  HP=$($API/api/state | python3 -c "import json,sys; print([c['config']['http_port'] for c in json.load(sys.stdin)['cameras'] if c['config']['id']=='$CID'][0])")
  curl -s -m 30 -o "$RES/snapshot.jpg" -w "snapshot HTTP %{http_code} %{size_download}\n" "http://127.0.0.1:$HP/onvif/snapshot/Profile_1.jpg" >> "$RES/summary.txt"
fi
shot 2_main_camera
# phone screen as the source: PROJECT_MEDIA=allow skips the consent dialog on the emulator
adb shell appops set $PKG PROJECT_MEDIA allow
adb shell am start -n $PKG/.MainActivity --es source screen
sleep 10
shot 3_main_screen
if [ -n "$ID" ]; then
  timeout 90 ffmpeg -v error -timeout 30000000 -rtsp_transport tcp -i "rtsp://admin:admin@127.0.0.1:8554/$CID/Profile_1" -t 8 -c copy -y /tmp/screen.mkv 2>> "$RES/ffmpeg.txt"
  echo "screen: $(ffprobe -v error -count_frames -select_streams v -show_entries stream=codec_name,width,height,nb_read_frames -of csv=p=0 /tmp/screen.mkv 2>&1)" >> "$RES/summary.txt"
  ffmpeg -v error -ss 4 -i /tmp/screen.mkv -frames:v 1 -y "$RES/screen_frame.jpg"
  $API/api/agents > "$RES/agents_screen.json"
fi
# UI screenshots: settings, themes, Persian
adb shell am start -n $PKG/.SettingsActivity; sleep 2; shot 4_settings
adb shell input keyevent 4; sleep 1
for t in matrix datacenter amber purple light; do
  adb shell am start -n $PKG/.MainActivity --es theme $t; sleep 2; shot "5_theme_$t"
done
adb shell am start -n $PKG/.MainActivity --es theme cyber --es lang fa; sleep 1
adb shell am start -n $PKG/.SettingsActivity; sleep 2; shot 6_settings_fa
adb shell input keyevent 4; sleep 2; shot 7_main_fa
adb logcat -d -s VCamAgent VCamStreamer AndroidRuntime CameraX > "$RES/logcat.txt" 2>&1
adb logcat -d | grep -iE "vcamagent|FATAL|camera" | tail -200 > "$RES/logcat-all.txt"
cat "$RES/summary.txt"
exit 0
