' Resolve — start the local server (if it isn't already up) and open the app.
'
' Double-click this, or make a desktop shortcut to it and give the shortcut
' web\ui\public\icons\icon-local.svg converted to .ico. It behaves like any
' other app: nothing runs until you open it, and closing the window leaves the
' server running for the rest of the session.
'
' Why VBScript rather than a .bat: a .bat flashes a console window every launch
' and leaves one sitting in the taskbar. WScript.Shell with intWindowStyle 0
' runs uvicorn genuinely hidden.
'
' SECURITY — DO NOT CHANGE THE HOST.
' This server has NO authentication and it writes to the real job tracker.
' 127.0.0.1 means only processes on this machine can reach it. Changing it to
' 0.0.0.0 would let anyone on the same network — a cafe, an airport, a shared
' flat — read and rewrite the tracker. There is no scenario in which that is
' worth it.

Option Explicit

Dim PYTHON, ROOT, HOST, PORT
PYTHON = "C:\Users\CHAVAN\AppData\Local\Programs\Python\Python313\pythonw.exe"
ROOT   = "D:\Adzuna"
HOST   = "127.0.0.1"     ' see the security note above
PORT   = "8000"

Dim shell, fso
Set shell = CreateObject("WScript.Shell")
Set fso   = CreateObject("Scripting.FileSystemObject")

If Not fso.FileExists(PYTHON) Then
    MsgBox "Python not found at:" & vbCrLf & PYTHON & vbCrLf & vbCrLf & _
           "Edit tools\Resolve.vbs and correct the PYTHON path.", _
           vbCritical, "Resolve"
    WScript.Quit 1
End If

' Is it already listening? Re-launching would just fail on a bound port.
Dim running, exec, out
running = False
On Error Resume Next
Set exec = shell.Exec("cmd /c netstat -ano -p TCP | findstr LISTENING | findstr " & HOST & ":" & PORT)
If Err.Number = 0 Then
    out = exec.StdOut.ReadAll()
    If InStr(out, HOST & ":" & PORT) > 0 Then running = True
End If
On Error GoTo 0

If Not running Then
    shell.CurrentDirectory = ROOT
    ' intWindowStyle 0 = hidden, bWaitOnReturn = False so we don't block.
    shell.Run """" & PYTHON & """ -m uvicorn web.api.app:app --host " & _
              HOST & " --port " & PORT, 0, False

    ' Poll until it answers rather than sleeping a fixed guess. Cold start is
    ' ~3s: the 3,308-row workbook and the 121k-entry register both load at boot
    ' so the first request doesn't pay for them.
    Dim waited, ok
    waited = 0 : ok = False
    Do While waited < 30 And Not ok
        WScript.Sleep 500
        waited = waited + 0.5
        On Error Resume Next
        Set exec = shell.Exec("cmd /c curl -s -o NUL -w ""%{http_code}"" http://" & _
                              HOST & ":" & PORT & "/api/health")
        If Err.Number = 0 Then
            If Trim(exec.StdOut.ReadAll()) = "200" Then ok = True
        End If
        On Error GoTo 0
    Loop

    If Not ok Then
        MsgBox "The server did not start within 30 seconds." & vbCrLf & vbCrLf & _
               "Run this to see the error:" & vbCrLf & _
               "cd /d " & ROOT & " && python -m uvicorn web.api.app:app --port " & PORT, _
               vbExclamation, "Resolve"
        WScript.Quit 1
    End If
End If

' Open in app mode: a standalone window with no address bar, whether or not the
' PWA has been installed. If it has, Windows will route to the installed app.
Dim edge, chrome, url
url    = "http://" & HOST & ":" & PORT & "/app"   ' the tool, not the landing
edge   = shell.ExpandEnvironmentStrings("%ProgramFiles(x86)%") & _
         "\Microsoft\Edge\Application\msedge.exe"
chrome = shell.ExpandEnvironmentStrings("%ProgramFiles%") & _
         "\Google\Chrome\Application\chrome.exe"

If fso.FileExists(edge) Then
    shell.Run """" & edge & """ --app=" & url, 1, False
ElseIf fso.FileExists(chrome) Then
    shell.Run """" & chrome & """ --app=" & url, 1, False
Else
    shell.Run url, 1, False        ' fall back to the default browser
End If
