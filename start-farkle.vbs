' Farkle Optimiser - silent launcher (no console window).
' Double-click this to start the local server; it opens in your browser.
' To stop it later: double-click stop-farkle.bat (or end the python process
' in Task Manager). If this ever does nothing, use start-farkle.bat instead --
' it shows a console with any error.
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh  = CreateObject("WScript.Shell")
here = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = here
target = """" & here & "\run.py"""

' Prefer "pyw", the Python launcher's windowless variant: it lives in
' %SystemRoot% and reliably finds the real interpreter, so it is NOT shadowed by
' the Microsoft Store "app execution alias" that can hijack a bare "pythonw" and
' make a silent double-click quietly do nothing. Fall back to pythonw, then to
' the console python, so a machine without the launcher still starts.
On Error Resume Next
sh.Run "pyw " & target, 0, False
If Err.Number <> 0 Then
  Err.Clear
  sh.Run "pythonw " & target, 0, False
End If
If Err.Number <> 0 Then
  Err.Clear
  sh.Run "python " & target, 0, False
End If
If Err.Number <> 0 Then
  MsgBox "Could not start Python. Try start-farkle.bat, which shows the error.", _
         48, "Farkle Optimiser"
End If
