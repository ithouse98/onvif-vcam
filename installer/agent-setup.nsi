; ONVIF VCam screen agent installer (NSIS 3, Unicode). Built by scripts/build_agent_setup.sh.
; Installs to %ProgramData%\ONVIF-VCam-Agent, registers + starts the ONVIF-VCam-Agent service.
; Silent:  ONVIF-VCam-Agent-Setup.exe /S [/SERVER=192.168.1.10] [/PORT=8600] [/KEY=secret] [/NAME=PC-01]
Target amd64-unicode
ManifestDPIAware true
SetCompressor /SOLID lzma
RequestExecutionLevel admin

!include MUI2.nsh
!include nsDialogs.nsh
!include LogicLib.nsh
!include FileFunc.nsh
!include x64.nsh

!define APP "ONVIF VCam Agent"
!define SVC "ONVIF-VCam-Agent"
!define UNKEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\${SVC}"
!define PUBLISHER "IPS-Co. (Behrouz Kazemi)"

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

Var Server
Var Port
Var Key
Var AgentName
Var hServer
Var hPort
Var hKey
Var hName

!insertmacro MUI_PAGE_WELCOME
Page custom ServerPage ServerPageLeave
!insertmacro MUI_PAGE_INSTFILES
!define MUI_FINISHPAGE_TITLE "$(FinTitle)"
!define MUI_FINISHPAGE_TEXT "$(FinText)"
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "English"
!insertmacro MUI_LANGUAGE "Farsi"

LangString PageTitle ${LANG_ENGLISH} "VCam server"
LangString PageTitle ${LANG_FARSI} "سرور VCam"
LangString PageSub ${LANG_ENGLISH} "Where should this PC stream its screens?"
LangString PageSub ${LANG_FARSI} "تصویر صفحه نمایش این کامپیوتر به کدام سرور ارسال شود؟"
LangString LblServer ${LANG_ENGLISH} "VCam server IP or name (leave empty to find it automatically on the network):"
LangString LblServer ${LANG_FARSI} "IP یا نام سرور VCam (خالی = پیدا کردن خودکار در شبکه)"
LangString LblPort ${LANG_ENGLISH} "Agent port:"
LangString LblPort ${LANG_FARSI} "پورت ایجنت"
LangString LblKey ${LANG_ENGLISH} "Agent key (as set in the VCam panel; empty if none):"
LangString LblKey ${LANG_FARSI} "کلید ایجنت (همان کلید پنل VCam؛ اگر تنظیم نشده خالی بگذارید)"
LangString LblName ${LANG_ENGLISH} "Name shown in the VCam panel:"
LangString LblName ${LANG_FARSI} "نامی که در پنل VCam نمایش داده می‌شود"
LangString BadPort ${LANG_ENGLISH} "The port must be a number between 1 and 65535."
LangString BadPort ${LANG_FARSI} "پورت باید عددی بین 1 تا 65535 باشد."
LangString Need64 ${LANG_ENGLISH} "This agent needs 64-bit Windows 8.1 or newer."
LangString Need64 ${LANG_FARSI} "این ایجنت به ویندوز 64 بیتی (8.1 یا جدیدتر) نیاز دارد."
LangString SvcFail ${LANG_ENGLISH} "The files were installed but the service could not be registered / started (code $0). See $INSTDIR\logs\install.log."
LangString SvcFail ${LANG_FARSI} "فایل‌ها نصب شدند ولی سرویس ثبت یا اجرا نشد (کد $0). فایل $INSTDIR\logs\install.log را ببینید."
LangString FinTitle ${LANG_ENGLISH} "The agent is running"
LangString FinTitle ${LANG_FARSI} "ایجنت در حال اجراست"
LangString FinText ${LANG_ENGLISH} "The service $\"${SVC}$\" is installed, running and starts with Windows.$\r$\n$\r$\nOpen the VCam control panel → Agents: this PC is listed with its monitors; click $\"+ monitor$\" to make a camera.$\r$\n$\r$\nThe screen is captured while a user is logged in to this PC."
LangString FinText ${LANG_FARSI} "سرویس $\"${SVC}$\" نصب شد، در حال اجراست و با روشن شدن ویندوز اجرا می‌شود.$\r$\n$\r$\nدر پنل VCam تب «ایجنت‌ها» را باز کنید: این کامپیوتر با مانیتورهایش آنجاست؛ با «+ مانیتور» دوربین بسازید.$\r$\n$\r$\nتصویر تا وقتی کاربری روی این کامپیوتر وارد شده ضبط می‌شود."
LangString ShLog ${LANG_ENGLISH} "Agent log"
LangString ShLog ${LANG_FARSI} "لاگ ایجنت"
LangString ShUn ${LANG_ENGLISH} "Uninstall ${APP}"
LangString ShUn ${LANG_FARSI} "حذف ${APP}"

Function .onInit
  SetShellVarContext all
  StrCpy $INSTDIR "$APPDATA\${SVC}"
  ${IfNot} ${RunningX64}
    MessageBox MB_ICONSTOP "$(Need64)"
    Abort
  ${EndIf}
  SetRegView 64
  ReadEnvStr $AgentName COMPUTERNAME
  StrCpy $Port "8600"
  ${GetParameters} $R0
  ClearErrors
  ${GetOptions} $R0 "/SERVER=" $R1
  ${IfNot} ${Errors}
    StrCpy $Server $R1
  ${EndIf}
  ClearErrors
  ${GetOptions} $R0 "/PORT=" $R1
  ${IfNot} ${Errors}
    StrCpy $Port $R1
  ${EndIf}
  ClearErrors
  ${GetOptions} $R0 "/KEY=" $R1
  ${IfNot} ${Errors}
    StrCpy $Key $R1
  ${EndIf}
  ClearErrors
  ${GetOptions} $R0 "/NAME=" $R1
  ${IfNot} ${Errors}
    StrCpy $AgentName $R1
  ${EndIf}
  ${IfNot} ${Silent}
    !insertmacro MUI_LANGDLL_DISPLAY
  ${EndIf}
FunctionEnd

Function ServerPage
  !insertmacro MUI_HEADER_TEXT "$(PageTitle)" "$(PageSub)"
  nsDialogs::Create 1018
  Pop $0
  ${NSD_CreateLabel} 0 0 100% 12u "$(LblServer)"
  ${NSD_CreateText} 0 13u 100% 13u "$Server"
  Pop $hServer
  ${NSD_CreateLabel} 0 34u 100% 12u "$(LblPort)"
  ${NSD_CreateNumber} 0 47u 60u 13u "$Port"
  Pop $hPort
  ${NSD_CreateLabel} 0 68u 100% 12u "$(LblKey)"
  ${NSD_CreatePassword} 0 81u 100% 13u "$Key"
  Pop $hKey
  ${NSD_CreateLabel} 0 102u 100% 12u "$(LblName)"
  ${NSD_CreateText} 0 115u 100% 13u "$AgentName"
  Pop $hName
  nsDialogs::Show
FunctionEnd

Function ServerPageLeave
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

Section "Agent" SecMain
  SetShellVarContext all
  SetRegView 64
  ; upgrade: stop the running agent and replace the program files (agent.json / logs are kept)
  ${If} ${FileExists} "$INSTDIR\runtime\python.exe"
    DetailPrint "Stopping the running agent…"
    nsExec::ExecToLog '"$INSTDIR\runtime\python.exe" "$INSTDIR\app\vcam\winservice.py" stop agent'
    Pop $0
    RMDir /r "$INSTDIR\runtime"
    RMDir /r "$INSTDIR\app"
  ${EndIf}
  SetOutPath "$INSTDIR"
  File /r "${SRC}\runtime"
  File /r "${SRC}\app"
  File "vcam.ico"
  ; logs: writable by users (the agent runs as the logged-in user); program files stay admin-only
  CreateDirectory "$INSTDIR\logs"
  nsExec::Exec 'icacls "$INSTDIR\logs" /grant *S-1-5-32-545:(OI)(CI)M'
  Pop $0
  WriteUninstaller "$INSTDIR\uninstall.exe"

  ${If} $Server != ""
  ${AndIf} $Port != "8600"
    StrCpy $R2 "$Server:$Port"
  ${Else}
    StrCpy $R2 "$Server"
  ${EndIf}
  DetailPrint "Registering and starting the service ${SVC}…"
  nsExec::ExecToStack '"$INSTDIR\runtime\python.exe" "$INSTDIR\app\vcam\winservice.py" install agent --server "$R2" --key "$Key" --name "$AgentName"'
  Pop $0
  Pop $1
  FileOpen $2 "$INSTDIR\logs\install.log" w
  FileWrite $2 "exit code $0$\r$\n$1"
  FileClose $2
  DetailPrint "$1"
  ${If} $0 != 0
    MessageBox MB_ICONEXCLAMATION "$(SvcFail)" /SD IDOK
    SetErrorLevel 2
  ${EndIf}

  CreateDirectory "$SMPROGRAMS\${APP}"
  CreateShortcut "$SMPROGRAMS\${APP}\$(ShLog).lnk" "$INSTDIR\logs\vcam-agent.log"
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
  ${GetSize} "$INSTDIR" "/S=0K" $3 $4 $5
  IntFmt $3 "0x%08X" $3
  WriteRegDWORD HKLM "${UNKEY}" "EstimatedSize" "$3"
SectionEnd

Function un.onInit
  SetShellVarContext all
  SetRegView 64
  !insertmacro MUI_UNGETLANGUAGE
FunctionEnd

Section "Uninstall"
  SetShellVarContext all
  SetRegView 64
  DetailPrint "Removing the service ${SVC}…"
  nsExec::ExecToLog '"$INSTDIR\runtime\python.exe" "$INSTDIR\app\vcam\winservice.py" remove agent'
  Pop $0
  Delete "$SMPROGRAMS\${APP}\*.lnk"
  RMDir "$SMPROGRAMS\${APP}"
  DeleteRegKey HKLM "${UNKEY}"
  SetOutPath "$TEMP"
  RMDir /r "$INSTDIR"
SectionEnd
