@echo off
rem Encerra o `wa servir` que roda escondido (ferramentas\servir-oculto.vbs).
rem Ele roda como python.exe do .venv com "-m wacervo.cli servir" (o wa.exe e
rem bloqueado pelo Smart App Control). Encerre antes de mexer em versao ou
rem dependencia. Para religar, rode o servir-oculto.vbs.
powershell -NoProfile -Command "$alvo = [IO.Path]::GetFullPath('%~dp0..\.venv\Scripts\python.exe'); $ps = @(Get-CimInstance Win32_Process -Filter \"Name = 'python.exe'\" | Where-Object { $_.ExecutablePath -eq $alvo -and $_.CommandLine -like '*wacervo.cli servir*' }); if ($ps.Count -eq 0) { Write-Host 'o servidor nao estava no ar'; exit 0 }; $ps | ForEach-Object { Stop-Process -Id $_.ProcessId -Force; Write-Host ('servidor encerrado (pid ' + $_.ProcessId + ')') }"
