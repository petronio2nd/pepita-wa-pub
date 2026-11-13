' Sobe o `wa servir` sem janela nenhuma.
'
' Um atalho para este arquivo fica na pasta Inicializar do Windows
' (shell:startup), entao o servidor sobe junto com o login. O que apareceria no
' console vai para dados\servidor.log. Para encerrar, rode parar-servidor.cmd
' (e encerre antes de mexer em versao ou dependencia).
'
' Sobe pelo python.exe do .venv (assinado pela PSF), nao pelo wa.exe: o wa.exe
' que o uv gera nao tem assinatura, e o Smart App Control do Windows bloqueia
' (desde 29/09/2026). O Smart App Control nao aceita excecao por pasta.

Option Explicit

Dim fso, shell, wmi, raiz, alvo, processo, arquivo_log, anterior, f

Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

' A pasta do projeto e a mae desta (ferramentas\).
raiz = fso.GetParentFolderName(fso.GetParentFolderName(WScript.ScriptFullName))
alvo = LCase(raiz & "\.venv\scripts\python.exe")

' Ja esta no ar (outro login, ou dois cliques): nao sobe um segundo, que so
' acharia a porta ocupada e morreria. Vale o python.exe deste .venv rodando o
' servir; o de outra copia do projeto tem outro caminho.
Set wmi = GetObject("winmgmts:")
For Each processo In wmi.ExecQuery("select ExecutablePath, CommandLine from Win32_Process where Name = 'python.exe'")
  If Not IsNull(processo.ExecutablePath) And Not IsNull(processo.CommandLine) Then
    If LCase(processo.ExecutablePath) = alvo And InStr(processo.CommandLine, "wacervo.cli servir") > 0 Then WScript.Quit 0
  End If
Next

If Not fso.FolderExists(raiz & "\dados") Then fso.CreateFolder raiz & "\dados"
arquivo_log = raiz & "\dados\servidor.log"

' Passou de 5 MB, o log vira .1 e recomeca: ninguem le o de tres meses atras.
If fso.FileExists(arquivo_log) Then
  If fso.GetFile(arquivo_log).Size > 5 * 1024 * 1024 Then
    anterior = arquivo_log & ".1"
    If fso.FileExists(anterior) Then fso.DeleteFile anterior
    fso.MoveFile arquivo_log, anterior
  End If
End If

Set f = fso.OpenTextFile(arquivo_log, 8, True)
f.WriteLine ""
f.WriteLine "=== wa servir ligado em " & Now & " ==="
f.Close

shell.CurrentDirectory = raiz
' 0 = sem janela nenhuma; False = nao espera o servidor terminar.
' Direto pelo python do .venv, sem `uv run`: o uv.exe ficaria preso o dia todo e
' o `winget upgrade` do uv falharia com "Acesso negado".
shell.Run "cmd /c .venv\Scripts\python.exe -m wacervo.cli servir --sem-aquecer >> """ & arquivo_log & """ 2>&1", 0, False
