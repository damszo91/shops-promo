' Uruchamia skan bez wyskakujacego okna (uzywane przez Harmonogram zadan)
Set sh = CreateObject("WScript.Shell")
folder = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
sh.Run """" & folder & "\skan-w-tle.bat""", 0, True
