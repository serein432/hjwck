' 智能语音播报系统 — 双击启动器（无黑窗口）
' 用 python 启动 app_desktop.py（内嵌后端 + 桌面窗口）
Option Explicit

Dim shell, baseDir, fso, f
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

' 脚本所在目录（= 项目根目录）
baseDir = fso.GetParentFolderName(WScript.ScriptFullName)

' 切到项目根目录并启动 App（窗口样式 0 = 隐藏，不阻塞）
On Error Resume Next
shell.CurrentDirectory = baseDir
shell.Run "python app\desktop\app_desktop.py", 0, False

If Err.Number <> 0 Then
    Set f = fso.CreateTextFile(baseDir & "\startup_error.txt", True)
    f.WriteLine "启动失败: " & Err.Description
    f.Close
    MsgBox "启动失败：" & Err.Description & vbCrLf & "详见 startup_error.txt", vbExclamation
Else
    MsgBox "智能语音播报系统启动中，请稍候（约 2-5 秒）…", vbInformation, "启动"
End If
