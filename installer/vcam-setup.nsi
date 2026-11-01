; ONVIF VCam setup (NSIS 3, Unicode). Built by scripts/build_setup.sh from the portable bundle.
; Components (chosen on the first pages): the VCam server (cameras + control panel), optionally as
; a Windows service with the tray icon (else it starts in the background at logon), and / or the
; screen agent (streams this PC's screens to a VCam server). One folder: %ProgramData%\ONVIF-VCam;
; upgrades keep config.json, videos, vcam-state and agent.json.
; Silent:  ONVIF-VCam-Setup.exe /S [/INSTALL=server,service,agent] [/USER=admin] [/PASSWORD=admin]
;          [/NOFIREWALL] [/SERVER=192.168.1.10] [/PORT=8600] [/KEY=secret] [/NAME=PC-01]
;          (default /INSTALL=server,service; agent options as in the agent setup)
Target amd64-unicode
ManifestDPIAware true
SetCompressor /SOLID lzma
RequestExecutionLevel admin

!include MUI2.nsh
!include nsDialogs.nsh
!include LogicLib.nsh
!include FileFunc.nsh
!include x64.nsh
!include Sections.nsh
!include WordFunc.nsh

!define APP "ONVIF VCam"
!define SVC "ONVIF-VCam"
!define UNKEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\${SVC}"
!define PUBLISHER "IPS-Co. (Behrouz Kazemi)"
!define PANEL "http://127.0.0.1:8000/"
!define AGKEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\ONVIF-VCam-Agent"

Name "${APP} ${VERSION}"
OutFile "${OUTFILE}"
InstallDir "$APPDATA\${SVC}"
BrandingText "ONVIF VCam ${VERSION} · IPS-Co."
VIProductVersion "${VERSION}.0"
VIAddVersionKey /LANG=0 "ProductName" "${APP}"
VIAddVersionKey /LANG=0 "FileVersion" "${VERSION}"
VIAddVersionKey /LANG=0 "CompanyName" "${PUBLISHER}"
VIAddVersionKey /LANG=0 "FileDescription" "${APP} setup"
VIAddVersionKey /LANG=0 "LegalCopyright" "© 1405 IPS-Co."

!define MUI_ICON "vcam.ico"
!define MUI_UNICON "vcam.ico"
!define MUI_ABORTWARNING
!define MUI_LANGDLL_ALLLANGUAGES

Var User
Var Pass
Var Firewall
Var Fresh
Var hUser
Var hPass
Var hFw
Var Server
Var Port
Var Key
Var AgentName
Var hServer
Var hPort
Var hKey
Var hName
Var FinMsg

!insertmacro MUI_PAGE_WELCOME
!define MUI_COMPONENTSPAGE_SMALLDESC
!define MUI_PAGE_CUSTOMFUNCTION_LEAVE ComponentsLeave
!insertmacro MUI_PAGE_COMPONENTS
!insertmacro MUI_PAGE_DIRECTORY
Page custom OptionsPage OptionsPageLeave
Page custom AgentPage AgentPageLeave
!insertmacro MUI_PAGE_INSTFILES
!define MUI_FINISHPAGE_TITLE "$(FinTitle)"
!define MUI_FINISHPAGE_TEXT "$FinMsg"
!define MUI_PAGE_CUSTOMFUNCTION_SHOW FinishShow
!define MUI_FINISHPAGE_RUN
!define MUI_FINISHPAGE_RUN_TEXT "$(FinRun)"
!define MUI_FINISHPAGE_RUN_FUNCTION OpenPanel
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "English"
!insertmacro MUI_LANGUAGE "Farsi"

LangString PageTitle ${LANG_ENGLISH} "Login and network"
LangString PageTitle ${LANG_FARSI} "ورود و شبکه"
LangString PageSub ${LANG_ENGLISH} "Login for the control panel and every virtual camera"
LangString PageSub ${LANG_FARSI} "نام کاربری و رمز پنل مدیریت و همه دوربین‌های مجازی"
LangString LblUser ${LANG_ENGLISH} "User name (the VMS uses it to log in to the cameras):"
LangString LblUser ${LANG_FARSI} "نام کاربری (VMS با همین نام به دوربین‌ها وصل می‌شود)"
LangString LblPass ${LANG_ENGLISH} "Password:"
LangString LblPass ${LANG_FARSI} "رمز عبور"
LangString LblKeep ${LANG_ENGLISH} "An existing installation was found: its cameras, videos, login and settings are kept."
LangString LblKeep ${LANG_FARSI} "نصب قبلی پیدا شد: دوربین‌ها، ویدیوها، نام کاربری و تنظیمات آن حفظ می‌شوند."
LangString LblFw ${LANG_ENGLISH} "Allow VCam through Windows Firewall (control panel, ONVIF, RTSP, discovery, agents)"
LangString LblFw ${LANG_FARSI} "اجازه عبور VCam از فایروال ویندوز (پنل، ONVIF، RTSP، Discovery و ایجنت‌ها)"
LangString BadUser ${LANG_ENGLISH} "Enter a user name."
LangString BadUser ${LANG_FARSI} "نام کاربری را وارد کنید."
LangString Need64 ${LANG_ENGLISH} "ONVIF VCam needs 64-bit Windows 8.1 or newer."
LangString Need64 ${LANG_FARSI} "ONVIF VCam به ویندوز 64 بیتی (8.1 یا جدیدتر) نیاز دارد."
LangString SvcFail ${LANG_ENGLISH} "The files were installed but the service could not be registered / started (code $0). See $INSTDIR\install.log."
LangString SvcFail ${LANG_FARSI} "فایل‌ها نصب شدند ولی سرویس ثبت یا اجرا نشد (کد $0). فایل $INSTDIR\install.log را ببینید."
LangString ItServer ${LANG_ENGLISH} "Server (recommended)"
LangString ItServer ${LANG_FARSI} "سرور (پیشنهادی)"
LangString ItBoth ${LANG_ENGLISH} "Server + agent"
LangString ItBoth ${LANG_FARSI} "سرور + ایجنت"
LangString ItAgent ${LANG_ENGLISH} "Agent only (this PC's screens for another VCam server)"
LangString ItAgent ${LANG_FARSI} "فقط ایجنت (ارسال صفحه این کامپیوتر به سرور VCam دیگر)"
LangString SecServer ${LANG_ENGLISH} "VCam server"
LangString SecServer ${LANG_FARSI} "سرور VCam"
LangString SecCore ${LANG_ENGLISH} "Cameras and control panel"
LangString SecCore ${LANG_FARSI} "دوربین‌ها و پنل مدیریت"
LangString SecService ${LANG_ENGLISH} "Run as a Windows service"
LangString SecService ${LANG_FARSI} "اجرا به‌صورت سرویس ویندوز"
LangString SecAgent ${LANG_ENGLISH} "Screen agent"
LangString SecAgent ${LANG_FARSI} "ایجنت صفحه نمایش"
LangString DescServer ${LANG_ENGLISH} "Virtual ONVIF cameras from video files, screens, webcams, agents and phones, with the web control panel."
LangString DescServer ${LANG_FARSI} "دوربین‌های مجازی ONVIF از فایل ویدیو، صفحه نمایش، وب‌کم، ایجنت‌ها و گوشی، همراه با پنل مدیریت وب."
LangString DescService ${LANG_ENGLISH} "Starts with Windows, even before anyone logs in, with a tray icon. Unchecked: the server starts in the background when a user logs in."
LangString DescService ${LANG_FARSI} "با روشن شدن ویندوز، حتی قبل از ورود کاربر، اجرا می‌شود و آیکون کنار ساعت دارد. بدون این گزینه، سرور با ورود کاربر در پس‌زمینه اجرا می‌شود."
LangString DescAgent ${LANG_ENGLISH} "Streams this PC's monitors to a VCam server on the network (a Windows service)."
LangString DescAgent ${LANG_FARSI} "تصویر مانیتورهای این کامپیوتر را به یک سرور VCam در شبکه می‌فرستد (سرویس ویندوز)."
LangString NoComp ${LANG_ENGLISH} "Choose the server, the agent or both."
LangString NoComp ${LANG_FARSI} "سرور، ایجنت یا هر دو را انتخاب کنید."
LangString AgTitle ${LANG_ENGLISH} "Agent: VCam server"
LangString AgTitle ${LANG_FARSI} "ایجنت: سرور VCam"
LangString AgSub ${LANG_ENGLISH} "Where should this PC stream its screens?"
LangString AgSub ${LANG_FARSI} "تصویر صفحه نمایش این کامپیوتر به کدام سرور ارسال شود؟"
LangString LblServer ${LANG_ENGLISH} "VCam server IP or name (empty = find it automatically on the network):"
LangString LblServer ${LANG_FARSI} "IP یا نام سرور VCam (خالی = پیدا کردن خودکار در شبکه)"
LangString LblPort ${LANG_ENGLISH} "Agent port:"
LangString LblPort ${LANG_FARSI} "پورت ایجنت"
LangString LblKey ${LANG_ENGLISH} "Agent key (as set in the VCam panel; empty if none):"
LangString LblKey ${LANG_FARSI} "کلید ایجنت (همان کلید پنل VCam؛ اگر تنظیم نشده خالی بگذارید)"
LangString LblName ${LANG_ENGLISH} "Name shown in the VCam panel:"
LangString LblName ${LANG_FARSI} "نامی که در پنل VCam نمایش داده می‌شود"
LangString BadPort ${LANG_ENGLISH} "The port must be a number between 1 and 65535."
LangString BadPort ${LANG_FARSI} "پورت باید عددی بین 1 تا 65535 باشد."
LangString AgFail ${LANG_ENGLISH} "The agent service could not be registered / started (code $0). See $INSTDIR\logs\install-agent.log."
LangString AgFail ${LANG_FARSI} "سرویس ایجنت ثبت یا اجرا نشد (کد $0). فایل $INSTDIR\logs\install-agent.log را ببینید."
LangString FinBg ${LANG_ENGLISH} "The server runs in the background and starts when a user logs in to Windows. Stop it from the control panel (⏻) or STOP.bat in $INSTDIR.$\r$\n$\r$\nControl panel: ${PANEL}"
LangString FinBg ${LANG_FARSI} "سرور در پس‌زمینه اجرا می‌شود و با ورود کاربر به ویندوز شروع می‌شود. توقف از پنل مدیریت (⏻) یا STOP.bat در $INSTDIR.$\r$\n$\r$\nپنل مدیریت: ${PANEL}"
LangString FinAgent ${LANG_ENGLISH} "The service $\"ONVIF-VCam-Agent$\" streams this PC's screens: open the VCam control panel → Agents and click $\"+ monitor$\" to make a camera. The screen is captured while a user is logged in."
LangString FinAgent ${LANG_FARSI} "سرویس $\"ONVIF-VCam-Agent$\" تصویر این کامپیوتر را ارسال می‌کند: در پنل VCam تب «ایجنت‌ها» را باز کنید و با «+ مانیتور» دوربین بسازید. تصویر تا وقتی کاربری وارد شده ضبط می‌شود."
LangString FinTitle ${LANG_ENGLISH} "ONVIF VCam is installed"
LangString FinTitle ${LANG_FARSI} "ONVIF VCam نصب شد"
LangString FinText ${LANG_ENGLISH} "The service $\"${SVC}$\" is installed, running and starts with Windows (even before anyone logs in). The tray icon next to the clock starts / stops it.$\r$\n$\r$\nControl panel: ${PANEL}$\r$\nAdd the cameras to your VMS by ONVIF discovery or by the addresses shown in the panel."
LangString FinText ${LANG_FARSI} "سرویس $\"${SVC}$\" نصب شد، در حال اجراست و با روشن شدن ویندوز (حتی قبل از ورود کاربر) اجرا می‌شود. با آیکون کنار ساعت می‌توانید آن را شروع یا متوقف کنید.$\r$\n$\r$\nپنل مدیریت: ${PANEL}$\r$\nدوربین‌ها را با ONVIF Discovery یا آدرس‌های داخل پنل به VMS اضافه کنید."
LangString FinRun ${LANG_ENGLISH} "Open the control panel"
LangString FinRun ${LANG_FARSI} "باز کردن پنل مدیریت"
LangString ShPanel ${LANG_ENGLISH} "ONVIF VCam control panel"
LangString ShPanel ${LANG_FARSI} "پنل مدیریت ONVIF VCam"
LangString ShTray ${LANG_ENGLISH} "ONVIF VCam tray icon"
LangString ShTray ${LANG_FARSI} "آیکون کنار ساعت ONVIF VCam"
LangString ShVideos ${LANG_ENGLISH} "Video library"
LangString ShVideos ${LANG_FARSI} "پوشه ویدیوها"
LangString ShLog ${LANG_ENGLISH} "Log"
LangString ShLog ${LANG_FARSI} "لاگ"
LangString ShStart ${LANG_ENGLISH} "Start ONVIF VCam"
LangString ShStart ${LANG_FARSI} "شروع ONVIF VCam"
LangString ShStop ${LANG_ENGLISH} "Stop ONVIF VCam"
LangString ShStop ${LANG_FARSI} "توقف ONVIF VCam"
LangString ShAgLog ${LANG_ENGLISH} "Agent log"
LangString ShAgLog ${LANG_FARSI} "لاگ ایجنت"
LangString ShUn ${LANG_ENGLISH} "Uninstall ${APP}"
LangString ShUn ${LANG_FARSI} "حذف ${APP}"
LangString UnData ${LANG_ENGLISH} "Also delete the cameras, settings and the video library in $INSTDIR?$\r$\n(No = keep them for a later installation)"
LangString UnData ${LANG_FARSI} "دوربین‌ها، تنظیمات و پوشه ویدیوها در $INSTDIR هم حذف شوند؟$\r$\n(خیر = برای نصب بعدی نگه داشته می‌شوند)"

!macro SelectIf LIST WORD SEC   ; /INSTALL=...: select SEC when WORD is in the list
  ${WordFind} "${LIST}," "${WORD}," "E+1{" $R2
  ${If} ${Errors}
    !insertmacro UnselectSection ${SEC}
  ${Else}
    !insertmacro SelectSection ${SEC}
  ${EndIf}
  ClearErrors
!macroend

!macro AgentOpt OPT VAR
  ClearErrors
  ${GetOptions} $R0 "${OPT}" $R1
  ${IfNot} ${Errors}
    StrCpy ${VAR} $R1
  ${EndIf}
!macroend

!macro UserWrite PATH PERM   ; BUILTIN\Users may modify PATH
  nsExec::Exec 'icacls "${PATH}" /grant *S-1-5-32-545:${PERM}'
  Pop $0
!macroend

; in + out rules for one of the bundled interpreters (the program, any port: panel, ONVIF, RTSP, RTP, discovery, agents)
!macro FwRule EXE
  nsExec::Exec 'netsh advfirewall firewall add rule name="${SVC}" dir=in action=allow program="$INSTDIR\runtime\${EXE}" enable=yes profile=any'
  Pop $0
  nsExec::Exec 'netsh advfirewall firewall add rule name="${SVC}" dir=out action=allow program="$INSTDIR\runtime\${EXE}" enable=yes profile=any'
  Pop $0
!macroend

; every python of this installation: the service, its capture helpers, the tray icon
!macro StopAll
  nsExec::Exec `powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-Process python,pythonw -ErrorAction SilentlyContinue | Where-Object { $$_.Path -like '$INSTDIR\runtime\*' } | Stop-Process -Force"`
  Pop $0
!macroend

InstType "$(ItServer)"
InstType "$(ItBoth)"
InstType "$(ItAgent)"

Section "-Core"   ; program files, shared by every component
  SetShellVarContext all
  SetRegView 64
  SectionIn 1 2 3
  StrCpy $Fresh "1"
  ${If} ${FileExists} "$INSTDIR\config.json"
    StrCpy $Fresh "0"
  ${EndIf}
  ; upgrade: stop the services and our processes, replace the program files only
  ${If} ${FileExists} "$INSTDIR\runtime\python.exe"
    DetailPrint "Stopping the running VCam…"
    ; no restart-on-failure while the files are replaced (install sets it again)
    nsExec::Exec 'sc failure ${SVC} reset= 0 actions= ""'
    Pop $0
    nsExec::Exec 'sc failure ONVIF-VCam-Agent reset= 0 actions= ""'
    Pop $0
    nsExec::ExecToLog '"$INSTDIR\runtime\python.exe" "$INSTDIR\app\vcam\winservice.py" stop vcam'
    Pop $0
    nsExec::ExecToLog '"$INSTDIR\runtime\python.exe" "$INSTDIR\app\vcam\winservice.py" stop agent'
    Pop $0
    !insertmacro StopAll
    RMDir /r "$INSTDIR\runtime"
    RMDir /r "$INSTDIR\app"
  ${EndIf}
  SetOutPath "$INSTDIR"
  File /r "${SRC}\runtime"
  File /r "${SRC}\app"
  File "${SRC}\_env.cmd"
  File "${SRC}\README-EN.md"
  File "${SRC}\README-FA.txt"
  File "vcam.ico"
  WriteUninstaller "$INSTDIR\uninstall.exe"
  CreateDirectory "$SMPROGRAMS\${APP}"
  CreateShortcut "$SMPROGRAMS\${APP}\$(ShUn).lnk" "$INSTDIR\uninstall.exe" "" "$INSTDIR\vcam.ico"
  WriteRegStr HKLM "${UNKEY}" "DisplayName" "${APP}"
  WriteRegStr HKLM "${UNKEY}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKLM "${UNKEY}" "Publisher" "${PUBLISHER}"
  WriteRegStr HKLM "${UNKEY}" "DisplayIcon" "$INSTDIR\vcam.ico"
  WriteRegStr HKLM "${UNKEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKLM "${UNKEY}" "UninstallString" '"$INSTDIR\uninstall.exe"'
  WriteRegStr HKLM "${UNKEY}" "QuietUninstallString" '"$INSTDIR\uninstall.exe" /S'
  WriteRegStr HKLM "${UNKEY}" "URLInfoAbout" "https://github.com/ithouse98/onvif-vcam"
  WriteRegDWORD HKLM "${UNKEY}" "NoModify" 1
  WriteRegDWORD HKLM "${UNKEY}" "NoRepair" 1
SectionEnd

SectionGroup /e "$(SecServer)" GrpServer
Section "$(SecCore)" SecServer
  SetShellVarContext all
  SetRegView 64
  SectionIn 1 2
  SetOutPath "$INSTDIR"
  File "${SRC}\*.bat"
  File "${SRC}\PANEL.url"
  File "${SRC}\config.example.json"
  SetOutPath "$INSTDIR\videos"
  SetOverwrite off           ; the user's library is never touched; the sample only if missing
  File "${SRC}\videos\*.mp4"
  SetOverwrite on
  SetOutPath "$INSTDIR"
  ${If} $Firewall == ${BST_CHECKED}
    DetailPrint "Adding Windows Firewall rules…"
    nsExec::Exec 'netsh advfirewall firewall delete rule name="${SVC}"'
    Pop $0
    !insertmacro FwRule "python.exe"
    !insertmacro FwRule "pythonw.exe"
  ${EndIf}
  CreateShortcut "$SMPROGRAMS\${APP}\$(ShPanel).lnk" "$INSTDIR\PANEL.url" "" "$INSTDIR\vcam.ico"
  CreateShortcut "$SMPROGRAMS\${APP}\$(ShVideos).lnk" "$INSTDIR\videos"
  CreateShortcut "$SMPROGRAMS\${APP}\$(ShLog).lnk" "$INSTDIR\vcam.log"
  CreateShortcut "$DESKTOP\$(ShPanel).lnk" "$INSTDIR\PANEL.url" "" "$INSTDIR\vcam.ico"
  ; switching from background mode to the service (or back) on an upgrade
  Delete "$SMSTARTUP\ONVIF VCam.lnk"
  Delete "$SMSTARTUP\ONVIF VCam tray.lnk"
  ${If} $Fresh == "1"
    StrCpy $R9 '--user "$User" --password "$Pass"'
  ${Else}
    StrCpy $R9 ""
  ${EndIf}
  StrCpy $FinMsg ""
SectionEnd

Section "$(SecService)" SecService
  SetShellVarContext all
  SetRegView 64
  SectionIn 1 2
  DetailPrint "Registering and starting the service ${SVC}…"
  nsExec::ExecToStack '"$INSTDIR\runtime\python.exe" "$INSTDIR\app\vcam\winservice.py" install vcam $R9'
  Pop $0
  Pop $1
  FileOpen $2 "$INSTDIR\install.log" w
  FileWrite $2 "exit code $0$\r$\n$1"
  FileClose $2
  DetailPrint "$1"
  ${If} $0 != 0
    MessageBox MB_ICONEXCLAMATION "$(SvcFail)" /SD IDOK
    SetErrorLevel 2
  ${Else}
    Exec '"$INSTDIR\runtime\pythonw.exe" "$INSTDIR\app\vcam\tray.py"'
  ${EndIf}
  CreateShortcut "$SMPROGRAMS\${APP}\$(ShTray).lnk" "$INSTDIR\runtime\pythonw.exe" '"$INSTDIR\app\vcam\tray.py"' "$INSTDIR\vcam.ico"
  StrCpy $FinMsg "$(FinText)"
SectionEnd
SectionGroupEnd

Section "-Background"   ; the server without the service: started at logon as the user
  SetShellVarContext all
  SetRegView 64
  ${If} ${SectionIsSelected} ${SecServer}
  ${AndIfNot} ${SectionIsSelected} ${SecService}
    nsExec::ExecToLog '"$INSTDIR\runtime\python.exe" "$INSTDIR\app\vcam\winservice.py" remove vcam'
    Pop $0
    ${If} $Fresh == "1"   ; the first config.json, with the chosen login
      nsExec::ExecToLog '"$INSTDIR\runtime\python.exe" "$INSTDIR\app\vcam\winservice.py" config vcam --user "$User" --password "$Pass"'
      Pop $0
    ${EndIf}
    ; the server runs as the logged-in user: users may change its data (not the program files)
    CreateDirectory "$INSTDIR\vcam-state"
    FileOpen $2 "$INSTDIR\vcam.log" a
    FileClose $2
    !insertmacro UserWrite "$INSTDIR\videos" "(OI)(CI)M"
    !insertmacro UserWrite "$INSTDIR\vcam-state" "(OI)(CI)M"
    !insertmacro UserWrite "$INSTDIR\config.json" "M"
    !insertmacro UserWrite "$INSTDIR\vcam.log" "M"
    nsExec::Exec 'icacls "$INSTDIR" /grant *S-1-5-32-545:(WD)'   ; config.json.tmp / log rotation
    Pop $0
    CreateShortcut "$SMSTARTUP\ONVIF VCam.lnk" "$INSTDIR\START.bat" "silent" "$INSTDIR\vcam.ico" 0 SW_SHOWMINIMIZED
    CreateShortcut "$SMPROGRAMS\${APP}\$(ShStart).lnk" "$INSTDIR\START.bat" "" "$INSTDIR\vcam.ico" 0 SW_SHOWMINIMIZED
    CreateShortcut "$SMPROGRAMS\${APP}\$(ShStop).lnk" "$INSTDIR\STOP.bat" "" "$INSTDIR\vcam.ico" 0 SW_SHOWMINIMIZED
    Exec '"$SYSDIR\cmd.exe" /c ""$INSTDIR\START.bat" silent"'
    StrCpy $FinMsg "$(FinBg)"
  ${EndIf}
SectionEnd

Section /o "$(SecAgent)" SecAgent
  SetShellVarContext all
  SetRegView 64
  SectionIn 2 3
  ; a separate agent installation (agent setup) is replaced; its identity is kept
  ReadRegStr $R0 HKLM "${AGKEY}" "InstallLocation"
  ${If} $R0 != ""
  ${AndIf} $R0 != $INSTDIR
    ${IfNot} ${FileExists} "$INSTDIR\agent.json"
      CopyFiles /SILENT "$R0\agent.json" "$INSTDIR"
    ${EndIf}
    DetailPrint "Removing the separate agent installation in $R0…"
    ExecWait '"$R0\uninstall.exe" /S _?=$R0'
    RMDir /r "$R0"
  ${EndIf}
  CreateDirectory "$INSTDIR\logs"
  !insertmacro UserWrite "$INSTDIR\logs" "(OI)(CI)M"
  ${If} $Server != ""
  ${AndIf} $Port != "8600"
    StrCpy $R2 "$Server:$Port"
  ${Else}
    StrCpy $R2 "$Server"
  ${EndIf}
  DetailPrint "Registering and starting the agent service…"
  nsExec::ExecToStack '"$INSTDIR\runtime\python.exe" "$INSTDIR\app\vcam\winservice.py" install agent --server "$R2" --key "$Key" --name "$AgentName"'
  Pop $0
  Pop $1
  FileOpen $2 "$INSTDIR\logs\install-agent.log" w
  FileWrite $2 "exit code $0$\r$\n$1"
  FileClose $2
  DetailPrint "$1"
  ${If} $0 != 0
    MessageBox MB_ICONEXCLAMATION "$(AgFail)" /SD IDOK
    SetErrorLevel 2
  ${EndIf}
  CreateShortcut "$SMPROGRAMS\${APP}\$(ShAgLog).lnk" "$INSTDIR\logs\vcam-agent.log"
  ${If} $FinMsg == ""
    StrCpy $FinMsg "$(FinAgent)"
  ${Else}
    StrCpy $FinMsg "$FinMsg$\r$\n$\r$\n$(FinAgent)"
  ${EndIf}
SectionEnd

Section "-Size"
  SetRegView 64
  ${IfNot} ${SectionIsSelected} ${SecAgent}   ; agent deselected on an upgrade
    nsExec::ExecToLog '"$INSTDIR\runtime\python.exe" "$INSTDIR\app\vcam\winservice.py" remove agent'
    Pop $0
  ${EndIf}
  ${GetSize} "$INSTDIR" "/S=0K" $3 $4 $5
  IntFmt $3 "0x%08X" $3
  WriteRegDWORD HKLM "${UNKEY}" "EstimatedSize" "$3"
SectionEnd

!insertmacro MUI_FUNCTION_DESCRIPTION_BEGIN
  !insertmacro MUI_DESCRIPTION_TEXT ${GrpServer} "$(DescServer)"
  !insertmacro MUI_DESCRIPTION_TEXT ${SecServer} "$(DescServer)"
  !insertmacro MUI_DESCRIPTION_TEXT ${SecService} "$(DescService)"
  !insertmacro MUI_DESCRIPTION_TEXT ${SecAgent} "$(DescAgent)"
!insertmacro MUI_FUNCTION_DESCRIPTION_END

Function .onInit
  SetShellVarContext all
  StrCpy $INSTDIR "$APPDATA\${SVC}"
  ${IfNot} ${RunningX64}
    MessageBox MB_ICONSTOP "$(Need64)"
    Abort
  ${EndIf}
  SetRegView 64
  ReadRegStr $R0 HKLM "${UNKEY}" "InstallLocation"   ; upgrade: same folder as before
  ${If} $R0 != ""
    StrCpy $INSTDIR $R0
  ${EndIf}
  StrCpy $User "admin"
  StrCpy $Pass "admin"
  StrCpy $Firewall ${BST_CHECKED}
  ReadEnvStr $AgentName COMPUTERNAME
  StrCpy $Port "8600"
  ; upgrade: preselect what is installed now
  ${If} ${FileExists} "$INSTDIR\agent.json"
  ${OrIf} ${FileExists} "$SMSTARTUP\ONVIF VCam.lnk"
    ${If} ${FileExists} "$INSTDIR\agent.json"
      !insertmacro SelectSection ${SecAgent}
    ${EndIf}
    ${IfNot} ${FileExists} "$INSTDIR\config.json"
      !insertmacro UnselectSection ${SecServer}
      !insertmacro UnselectSection ${SecService}
    ${EndIf}
    ${If} ${FileExists} "$SMSTARTUP\ONVIF VCam.lnk"
      !insertmacro UnselectSection ${SecService}
    ${EndIf}
  ${EndIf}
  ReadRegStr $R0 HKLM "${AGKEY}" "InstallLocation"   ; separate agent setup installed
  ${If} $R0 != ""
    !insertmacro SelectSection ${SecAgent}
  ${EndIf}
  ${GetParameters} $R0
  ClearErrors
  ${GetOptions} $R0 "/INSTALL=" $R1
  ${IfNot} ${Errors}
    !insertmacro SelectIf $R1 "server" ${SecServer}
    !insertmacro SelectIf $R1 "service" ${SecService}
    !insertmacro SelectIf $R1 "agent" ${SecAgent}
  ${EndIf}
  !insertmacro AgentOpt "/SERVER=" $Server
  !insertmacro AgentOpt "/PORT=" $Port
  !insertmacro AgentOpt "/KEY=" $Key
  !insertmacro AgentOpt "/NAME=" $AgentName
  ${GetParameters} $R0
  ClearErrors
  ${GetOptions} $R0 "/USER=" $R1
  ${IfNot} ${Errors}
    StrCpy $User $R1
  ${EndIf}
  ClearErrors
  ${GetOptions} $R0 "/PASSWORD=" $R1
  ${IfNot} ${Errors}
    StrCpy $Pass $R1
  ${EndIf}
  ClearErrors
  ${GetOptions} $R0 "/NOFIREWALL" $R1
  ${IfNot} ${Errors}
    StrCpy $Firewall ${BST_UNCHECKED}
  ${EndIf}
  ${IfNot} ${Silent}
    !insertmacro MUI_LANGDLL_DISPLAY
  ${EndIf}
FunctionEnd

!macro SkipUnless SEC
  ${IfNot} ${SectionIsSelected} ${SEC}
    Abort
  ${EndIf}
!macroend

Function .onSelChange
  ${IfNot} ${SectionIsSelected} ${SecServer}   ; the service is a way of running the server
    !insertmacro UnselectSection ${SecService}
  ${EndIf}
FunctionEnd

Function ComponentsLeave
  ${IfNot} ${SectionIsSelected} ${SecServer}
  ${AndIfNot} ${SectionIsSelected} ${SecAgent}
    MessageBox MB_ICONEXCLAMATION "$(NoComp)"
    Abort
  ${EndIf}
FunctionEnd

Function OptionsPage
  !insertmacro SkipUnless ${SecServer}
  !insertmacro MUI_HEADER_TEXT "$(PageTitle)" "$(PageSub)"
  nsDialogs::Create 1018
  Pop $0
  ${If} ${FileExists} "$INSTDIR\config.json"
    ${NSD_CreateLabel} 0 0 100% 24u "$(LblKeep)"
    Pop $0
    StrCpy $hUser ""
  ${Else}
    ${NSD_CreateLabel} 0 0 100% 12u "$(LblUser)"
    Pop $0
    ${NSD_CreateText} 0 13u 100% 13u "$User"
    Pop $hUser
    ${NSD_CreateLabel} 0 34u 100% 12u "$(LblPass)"
    Pop $0
    ${NSD_CreatePassword} 0 47u 100% 13u "$Pass"
    Pop $hPass
  ${EndIf}
  ${NSD_CreateCheckbox} 0 76u 100% 22u "$(LblFw)"
  Pop $hFw
  ${NSD_SetState} $hFw $Firewall
  nsDialogs::Show
FunctionEnd

Function OptionsPageLeave
  ${If} $hUser != ""
    ${NSD_GetText} $hUser $User
    ${NSD_GetText} $hPass $Pass
    ${If} $User == ""
      MessageBox MB_ICONEXCLAMATION "$(BadUser)"
      Abort
    ${EndIf}
  ${EndIf}
  ${NSD_GetState} $hFw $Firewall
FunctionEnd

Function AgentPage
  !insertmacro SkipUnless ${SecAgent}
  !insertmacro MUI_HEADER_TEXT "$(AgTitle)" "$(AgSub)"
  nsDialogs::Create 1018
  Pop $0
  ${NSD_CreateLabel} 0 0 100% 12u "$(LblServer)"
  Pop $0
  ${NSD_CreateText} 0 13u 100% 13u "$Server"
  Pop $hServer
  ${NSD_CreateLabel} 0 34u 100% 12u "$(LblPort)"
  Pop $0
  ${NSD_CreateNumber} 0 47u 60u 13u "$Port"
  Pop $hPort
  ${NSD_CreateLabel} 0 68u 100% 12u "$(LblKey)"
  Pop $0
  ${NSD_CreatePassword} 0 81u 100% 13u "$Key"
  Pop $hKey
  ${NSD_CreateLabel} 0 102u 100% 12u "$(LblName)"
  Pop $0
  ${NSD_CreateText} 0 115u 100% 13u "$AgentName"
  Pop $hName
  nsDialogs::Show
FunctionEnd

Function AgentPageLeave
  ${NSD_GetText} $hServer $Server
  ${NSD_GetText} $hPort $Port
  ${NSD_GetText} $hKey $Key
  ${NSD_GetText} $hName $AgentName
  ${If} $Port < 1
  ${OrIf} $Port > 65535
    MessageBox MB_ICONEXCLAMATION "$(BadPort)"
    Abort
  ${EndIf}
FunctionEnd

Function FinishShow   ; the "open the control panel" box only with the server
  ${IfNot} ${SectionIsSelected} ${SecServer}
    ShowWindow $mui.FinishPage.Run ${SW_HIDE}
  ${EndIf}
FunctionEnd

Function OpenPanel
  ExecShell "open" "${PANEL}"
FunctionEnd


Function un.onInit
  SetShellVarContext all
  SetRegView 64
  !insertmacro MUI_UNGETLANGUAGE
FunctionEnd

Section "Uninstall"
  SetShellVarContext all
  SetRegView 64
  DetailPrint "Removing the services…"
  nsExec::ExecToLog '"$INSTDIR\runtime\python.exe" "$INSTDIR\app\vcam\winservice.py" remove vcam'
  Pop $0
  nsExec::ExecToLog '"$INSTDIR\runtime\python.exe" "$INSTDIR\app\vcam\winservice.py" remove agent'
  Pop $0
  !insertmacro StopAll
  Delete "$SMSTARTUP\ONVIF VCam.lnk"
  nsExec::Exec 'netsh advfirewall firewall delete rule name="${SVC}"'
  Pop $0
  Delete "$SMPROGRAMS\${APP}\*.lnk"
  RMDir "$SMPROGRAMS\${APP}"
  Delete "$DESKTOP\$(ShPanel).lnk"
  DeleteRegKey HKLM "${UNKEY}"
  SetOutPath "$TEMP"
  RMDir /r "$INSTDIR\runtime"
  RMDir /r "$INSTDIR\app"
  Delete "$INSTDIR\*.bat"
  Delete "$INSTDIR\_env.cmd"
  Delete "$INSTDIR\PANEL.url"
  Delete "$INSTDIR\README-*.*"
  Delete "$INSTDIR\config.example.json"
  Delete "$INSTDIR\vcam.ico"
  Delete "$INSTDIR\install.log"
  Delete "$INSTDIR\uninstall.exe"
  MessageBox MB_YESNO|MB_ICONQUESTION "$(UnData)" /SD IDNO IDNO keep
    RMDir /r "$INSTDIR"
  keep:
  RMDir "$INSTDIR"
SectionEnd
