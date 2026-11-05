Unicode true
!include "MUI2.nsh"
!include "LogicLib.nsh"
Name "Snowball Companion"
OutFile "${OUTPUT}"
RequestExecutionLevel user
SetCompressor /SOLID lzma
ShowInstDetails show
!define MUI_ABORTWARNING
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_LANGUAGE "SimpChinese"
!insertmacro MUI_LANGUAGE "English"
Section "Snowball Companion"
  InitPluginsDir
  SetOutPath "$PLUGINSDIR\payload"
  File /r "${PAYLOAD}\*"
  DetailPrint "Opening the local feature selection wizard..."
  ExecWait '"$PLUGINSDIR\payload\python\python.exe" -X utf8 "$PLUGINSDIR\payload\installer\bootstrap.py"' $0
  ${If} $0 != 0
    MessageBox MB_OK|MB_ICONSTOP "Snowball setup did not finish. Existing configuration was preserved."
    SetErrorLevel 1
  ${EndIf}
SectionEnd
