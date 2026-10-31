# Windows runner test of the portable bundle (used by .github/workflows/windows-test.yml).
# Phases: standalone (file + screen + 2-monitor composite cameras, test suites, recordings),
# Windows service (screen capture from Session 0 via the user session), screen agent service
# registering with the VCam service, tray icon, uninstall. Writes everything to C:\results.
param([string]$Root)
$ErrorActionPreference = "Continue"
$R = "C:\results"
$env:PATH = "$Root\runtime;$Root\runtime\Library\bin;$env:PATH"
$env:PYTHONPATH = "$Root\app"
$PY = "$Root\runtime\python.exe"
$sum = "$R\summary.txt"
function Note($t) { $t | Tee-Object -FilePath $sum -Append }
function WaitPort($port, $sec) {
  for ($i = 0; $i -lt $sec; $i++) {
    try { $c = New-Object Net.Sockets.TcpClient; $c.Connect("127.0.0.1", $port); $c.Close(); return $true } catch { Start-Sleep 1 }
  }
  return $false
}
function Api($method, $path, $body) {
  $a = @("-s", "-u", "admin:admin", "-X", $method, "http://127.0.0.1:8000$path")
  if ($body) { $f = "$R\body.json"; $body | Out-File -Encoding ascii $f; $a += @("-H", "Content-Type: application/json", "--data-binary", "@$f") }
  & curl.exe @a
}
function Record($name, $url, $sec) {
  $out = "$R\$name.mkv"
  $p = Start-Process ffmpeg -ArgumentList "-v error -timeout 20000000 -rtsp_transport tcp -i $url -t $sec -c copy -y $out" -NoNewWindow -PassThru -RedirectStandardError "$R\ff_$name.txt"
  if (-not $p.WaitForExit(90000)) { $p.Kill(); Note "$name : ffmpeg TIMEOUT" }
  $info = & ffprobe -v error -count_frames -select_streams v -show_entries stream=width,height,nb_read_frames -of csv=p=0 $out 2>&1
  Note "$name : $info  $(Get-Content "$R\ff_$name.txt" -ErrorAction SilentlyContinue)"
  & ffmpeg -v error -ss ([math]::Floor($sec / 2)) -i $out -frames:v 1 -vf "scale=960:-2" -y "$R\$name.jpg" 2>$null
}
$null = Start-Job { 1..90 | ForEach-Object { Start-Process notepad; Start-Sleep 2 } }

# ---------------------------------------------------------------- phase 1: standalone
Note "== standalone"
Start-Process -FilePath $PY -WorkingDirectory $Root `
  -ArgumentList "-m","vcam","$Root\videos\sample_1080p.mp4","screen://","screen://0+0","screen://all","--mjpeg","-c","$Root\config.json","--log-file","$Root\vcam.log" `
  -RedirectStandardOutput "$R\stdout.txt" -RedirectStandardError "$R\stderr.txt"
Note "panel up: $(WaitPort 8000 120)"
Start-Sleep 3
$t = "$R\tests.txt"
& $PY "$Root\app\tests\test_raw_soap.py" 127.0.0.1 8080 8554 admin admin *>> $t
& $PY "$Root\app\tests\test_with_onvif_client.py" 127.0.0.1 8080 admin admin *>> $t
& $PY "$Root\app\tests\test_panel.py" 127.0.0.1 8000 admin admin "$Root\videos\sample_1080p.mp4" *>> $t
Select-String -Path $t -Pattern "passed|FAIL" | ForEach-Object { Note $_.Line }
Record "screen" "rtsp://admin:admin@127.0.0.1:8554/cam2/Profile_1" 8
Record "composite_0+0" "rtsp://admin:admin@127.0.0.1:8554/cam3/Profile_1" 8
Record "composite_all" "rtsp://admin:admin@127.0.0.1:8554/cam4/Profile_1" 6
& $PY -m vcam -c "$Root\config.json" --stop
Start-Sleep 4
Copy-Item "$Root\vcam.log" "$R\vcam-standalone.log" -ErrorAction SilentlyContinue

# ---------------------------------------------------------------- phase 2: Windows service
Note "== sessions"
Note "this process session: $((Get-Process -Id $PID).SessionId)"
(& qwinsta 2>&1) | ForEach-Object { Note $_ }
Note "== service"
& $PY -m vcam.winservice install vcam *>> "$R\service.txt"
Get-Content "$R\service.txt" | ForEach-Object { Note $_ }
Note "panel up (service): $(WaitPort 8000 120)"
Note "sc: $((& sc.exe query ONVIF-VCam | Select-String STATE).Line)"
Start-Sleep 5
Record "service_file" "rtsp://admin:admin@127.0.0.1:8554/cam1/Profile_2" 5
Record "service_screen" "rtsp://admin:admin@127.0.0.1:8554/cam2/Profile_1" 8
try { Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8081/onvif/snapshot/Profile_1.jpg -TimeoutSec 40 -OutFile "$R\service_snapshot.jpg"; Note "service snapshot ok" } catch { Note "service snapshot failed: $_" }
Api GET "/api/devices" "" | Out-File "$R\service_devices.json"
Note "devices via service: $(Get-Content "$R\service_devices.json")"

# ---------------------------------------------------------------- phase 3: agent service
Note "== agent"
& $PY -m vcam.winservice install agent --server 127.0.0.1 *>> "$R\agent-install.txt"
Get-Content "$R\agent-install.txt" | ForEach-Object { Note $_ }
$agents = $null
for ($i = 0; $i -lt 60; $i++) {
  $agents = Api GET "/api/agents" "" | ConvertFrom-Json
  if ($agents | Where-Object { $_.online }) { break }
  Start-Sleep 2
}
$agents | ConvertTo-Json -Depth 5 | Out-File "$R\agents.json"
$ag = $agents | Where-Object { $_.online } | Select-Object -First 1
if ($ag) {
  Note "agent online: $($ag.name) $($ag.ip) screens=$($ag.screens.Count)"
  $res = Api POST "/api/cameras" ('{"name":"AgentCam","source":"agent://' + $ag.id + '/0"}')
  Note "add agent camera: $res"
  $cid = ($res | ConvertFrom-Json).ids[0]
  Start-Sleep 4
  Record "agent_main" "rtsp://admin:admin@127.0.0.1:8554/$cid/Profile_1" 8
  Record "agent_sub" "rtsp://admin:admin@127.0.0.1:8554/$cid/Profile_2" 5
} else { Note "agent did not register" }

# ---------------------------------------------------------------- phase 4: tray
Note "== tray"
$tray = Start-Process -FilePath "$Root\runtime\pythonw.exe" -ArgumentList "`"$Root\app\vcam\tray.py`"" -PassThru
Start-Sleep 8
Note "tray alive after 8s: $(-not $tray.HasExited)"
Record "tray_screen" "rtsp://admin:admin@127.0.0.1:8554/cam2/Profile_1" 4
if (-not $tray.HasExited) { $tray.Kill() }

# ---------------------------------------------------------------- cleanup
Note "== uninstall"
& $PY -m vcam.winservice remove agent *>> "$R\uninstall.txt"
& $PY -m vcam.winservice remove vcam *>> "$R\uninstall.txt"
Get-Content "$R\uninstall.txt" | ForEach-Object { Note $_ }
Note "panel down after uninstall: $(-not (WaitPort 8000 3))"
Copy-Item "$Root\vcam.log" "$R\vcam-service.log" -ErrorAction SilentlyContinue
Copy-Item "$Root\vcam-agent.log" $R -ErrorAction SilentlyContinue
Copy-Item "$env:TEMP\vcam-gsthelper.log" $R -ErrorAction SilentlyContinue
Copy-Item "$Root\service-*.log" $R -ErrorAction SilentlyContinue
Get-WinEvent -FilterHashtable @{LogName='Application'; StartTime=(Get-Date).AddHours(-1)} -ErrorAction SilentlyContinue |
  Where-Object { $_.ProviderName -match 'ONVIF|Python|pywin' -or $_.Message -match 'ONVIF|vcam' } |
  Select-Object -First 40 TimeCreated, ProviderName, Id, Message | Format-List | Out-File "$R\eventlog-app.txt"
Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='Service Control Manager'; StartTime=(Get-Date).AddHours(-1)} -ErrorAction SilentlyContinue |
  Where-Object { $_.Message -match 'ONVIF' } | Select-Object -First 40 TimeCreated, Id, Message | Format-List | Out-File "$R\eventlog-scm.txt"
Get-ChildItem C:\Windows\Temp\vcam-gsthelper.log -ErrorAction SilentlyContinue | Copy-Item -Destination "$R\vcam-gsthelper-system.log"
Remove-Item "$R\*.mkv", "$R\body.json" -ErrorAction SilentlyContinue
