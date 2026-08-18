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
' TWO BUGS FIXED 2026-08-18, both of which made this worse than the .bat it
' replaced:
'
' 1. It never actually started the server. pythonw.exe has NO CONSOLE, so
'    sys.stdout and sys.stderr are Nothing, and uvicorn dies on its first log
'    write with exit code 1 before binding the port. Output is now redirected
'    to logs\server.log, which both keeps uvicorn alive and leaves something
'    to read when a launch fails.
'
' 2. It spawned a cmd window every 500ms while polling. shell.Exec ALWAYS
'    shows a window - there is no hidden variant - so the health-check loop
'    flashed up to 60 consoles per launch, and the "is it already running"
'    check flashed one more. Both now use MSXML2.XMLHTTP, which makes the
'    request in-process and shows nothing.
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

' One health request, in-process, no window. Also a better question than the
' netstat check it replaces: a port can be bound by a process that is wedged or
' by an unrelated program, and neither of those should count as "already up".
Function Responding(url)
    Dim http
    Responding = False
    On Error Resume Next
    Set http = CreateObject("MSXML2.XMLHTTP")
    http.Open "GET", url, False
    http.Send
    If Err.Number = 0 Then
        If http.Status = 200 Then Responding = True
    End If
    Err.Clear
    On Error GoTo 0
End Function

Dim healthUrl, running
healthUrl = "http://" & HOST & ":" & PORT & "/api/health"
running = Responding(healthUrl)

Dim LOGFILE
LOGFILE = ROOT & "\logs\server.log"

If Not running Then
    shell.CurrentDirectory = ROOT
    If Not fso.FolderExists(ROOT & "\logs") Then fso.CreateFolder ROOT & "\logs"

    ' The redirection is load-bearing, not tidiness. pythonw.exe has no console,
    ' so without somewhere real to write, uvicorn's first log line raises and the
    ' process exits 1 without ever binding the port - which is exactly what this
    ' script did silently for as long as it existed. Routed through cmd because
    ' shell.Run has no redirection of its own; style 0 keeps that cmd hidden.
    shell.Run "cmd /c """"" & PYTHON & """ -m uvicorn web.api.app:app --host " & _
              HOST & " --port " & PORT & " > """ & LOGFILE & """ 2>&1""", 0, False

    ' Poll until it answers rather than sleeping a fixed guess. Cold start is
    ' ~3s: the 3,308-row workbook and the 121k-entry register both load at boot
    ' so the first request doesn't pay for them.
    Dim waited, ok
    waited = 0 : ok = False
    Do While waited < 30 And Not ok
        WScript.Sleep 500
        waited = waited + 0.5
        ok = Responding(healthUrl)
    Loop

    If Not ok Then
        Dim hint
        hint = ""
        If fso.FileExists(LOGFILE) Then
            On Error Resume Next
            hint = vbCrLf & vbCrLf & "Last thing the server said:" & vbCrLf & _
                   Right(fso.OpenTextFile(LOGFILE, 1).ReadAll(), 500)
            On Error GoTo 0
        End If
        MsgBox "The server did not start within 30 seconds." & vbCrLf & vbCrLf & _
               "Full log: " & LOGFILE & hint, vbExclamation, "Resolve"
        WScript.Quit 1
    End If
End If

' Open in app mode: a standalone window with no address bar.
'
' Chrome first, and a named profile, because this machine has eight of them
' across four Google accounts. Without --profile-directory Chrome picks
' whichever was last active, so the app would open somewhere different
' depending on what you happened to be doing, which is the opposite of an app
' icon behaving predictably.
'
' "Profile 8" is the "Ai Tools" profile (anuragchavan21102@gmail.com). To
' change it: chrome://version in the profile you want, read the Profile Path,
' and put the last folder name below.
Dim chrome, edge, url, profile
url     = "http://" & HOST & ":" & PORT & "/app"   ' the tool, not the landing
profile = "Profile 8"
chrome  = shell.ExpandEnvironmentStrings("%ProgramFiles%") & _
          "\Google\Chrome\Application\chrome.exe"
edge    = shell.ExpandEnvironmentStrings("%ProgramFiles(x86)%") & _
          "\Microsoft\Edge\Application\msedge.exe"

If fso.FileExists(chrome) Then
    shell.Run """" & chrome & """ --profile-directory=""" & profile & _
              """ --app=" & url, 1, False
ElseIf fso.FileExists(edge) Then
    shell.Run """" & edge & """ --app=" & url, 1, False
Else
    shell.Run url, 1, False        ' fall back to the default browser
End If
