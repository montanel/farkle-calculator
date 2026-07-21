' Farkle Optimiser - silent launcher (no console window).
' Double-click this to start the local server; it opens in your browser.
' To stop it later: double-click stop-farkle.bat (or end "pythonw.exe" in Task Manager).
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh  = CreateObject("WScript.Shell")
here = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = here
sh.Run "pythonw """ & here & "\run.py""", 0, False
