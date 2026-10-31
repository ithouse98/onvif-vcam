@echo off
rem Command prompt with the bundled tools on PATH: python, ffmpeg, ffprobe, ffplay, gst-launch-1.0, gst-inspect-1.0, gst-device-monitor-1.0
call "%~dp0_env.cmd"
cmd /k "title ONVIF-VCam shell & echo Tools: python ffmpeg ffprobe ffplay gst-launch-1.0 gst-inspect-1.0 gst-device-monitor-1.0 & echo Example: ffplay -rtsp_transport tcp rtsp://admin:admin@127.0.0.1:8554/cam1/Profile_1"
