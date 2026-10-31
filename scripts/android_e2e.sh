#!/usr/bin/env bash
# Emulator end-to-end test of the Android agent against a VCam server running on the CI host
# (the emulator reaches the host as 10.0.2.2). Results -> $RES.
set -x
RES=${RES:-/tmp/results}
APK=$(ls "$GITHUB_WORKSPACE"/apk/*.apk | head -1)
API="curl -s -u admin:admin http://127.0.0.1:8000"
adb install -r "$APK" > "$RES/install.txt" 2>&1
adb shell pm grant com.ipsco.vcamagent android.permission.CAMERA
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
  curl -s -m 30 -o "$RES/snapshot.jpg" -w "snapshot HTTP %{http_code} %{size_download}\n" "http://127.0.0.1:8081/onvif/snapshot/Profile_1.jpg" >> "$RES/summary.txt"
fi
adb exec-out screencap -p > "$RES/app_screen.png"
adb logcat -d -s VCamAgent VCamStreamer AndroidRuntime CameraX > "$RES/logcat.txt" 2>&1
adb logcat -d | grep -iE "vcamagent|FATAL|camera" | tail -200 > "$RES/logcat-all.txt"
cat "$RES/summary.txt"
exit 0
