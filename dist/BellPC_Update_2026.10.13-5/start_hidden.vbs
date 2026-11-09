Rem Starts the bell system with no visible window (used by the Desktop / Startup shortcuts).
Set fso = CreateObject("Scripting.FileSystemObject")
appDir = fso.GetParentFolderName(WScript.ScriptFullName)
Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = appDir
sh.Run """" & appDir & "\start_all.bat""", 0, False
