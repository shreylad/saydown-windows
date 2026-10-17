' Starts Saydown with no console window. Its log is %APPDATA%\Saydown\saydown.log.
Set shell = CreateObject("WScript.Shell")
shell.CurrentDirectory = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
shell.Run "uvw run --gui-script saydown.py", 0, False
