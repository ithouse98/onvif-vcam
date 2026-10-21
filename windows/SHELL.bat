@echo off
rem Command prompt with the bundled tools on PATH: python, ffmpeg, ffprobe, ffplay
call "%~dp0_env.cmd"
cmd /k "title ONVIF-VCam shell & echo Tools: python ffmpeg ffprobe ffplay & echo Example: ffplay -rtsp_transport tcp rtsp://admin:admin@127.0.0.1:8554/cam1/Profile_1"
