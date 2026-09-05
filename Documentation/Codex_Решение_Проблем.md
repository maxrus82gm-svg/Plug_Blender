

Stop-Process -Name Codex -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2

$manifest = Get-WinEvent -FilterHashtable @{
    LogName='Microsoft-Windows-AppXDeploymentServer/Operational'
    StartTime=(Get-Date).AddHours(-6)
} -ErrorAction SilentlyContinue |
ForEach-Object {
    if ($_.Message -match '(C:\\Program Files\\WindowsApps\\OpenAI\.Codex_[0-9.]+_x64__2p2nqsd0c76g0\\AppxManifest\.xml)') {
        $matches[1]
    }
} |
Select-Object -First 1

if ($manifest) {
    Write-Host "Найден пакет:" $manifest
    Add-AppxPackage -Register $manifest -DisableDevelopmentMode

    Write-Host "`nТекущая версия Codex:"
    Get-AppxPackage OpenAI.Codex | Select Name, Version, Status, InstallLocation
}
else {
    Write-Host "Не удалось найти подготовленный пакет Codex в журнале AppX."
}

# Codex Maintenance — проверенные решения технических проблем

Этот документ — накопительная памятка по техническим проблемам Codex Desktop / Codex CLI / Windows / MCP и окружения.

Цель: не расследовать одинаковую проблему заново в каждом проекте. Если проблема уже встречалась и решение было фактически проверено, агент сначала использует этот документ.

Правила:
- сначала диагностировать фактическую причину;
- не переустанавливать Codex и не делать Reset без необходимости;
- не менять proxy/VPN/v2rayN только на основании сетевых ошибок;
- не угадывать версии и пути — определять их на текущем компьютере;
- после исправления обязательно проверять результат;
- добавлять сюда только решения, которые реально были проверены и помогли.

---

## Codex Desktop Windows — зациклилось обновление

### Симптомы

Codex предлагает обновление → начинается установка → Codex закрывается/перезапускается → после запуска снова появляется та же кнопка Update.

Цикл может повторяться сколько угодно раз, а установленная версия не меняется.

### Что оказалось причиной

В проверенном случае:

- установленный AppX-пакет Codex имел `Status = Ok`;
- Codex находил новую версию;
- обновление успешно скачивалось;
- Windows успешно выполняла Stage нового AppX/MSIX-пакета;
- ошибка происходила на этапе Register;
- AppXDeploymentServer выдавал `0x80070005`;
- Windows сообщала, что для установки необходимо закрыть:

`OpenAI.Codex_2p2nqsd0c76g0!App`

То есть Codex пытался обновить сам себя, но Windows всё ещё считала старый Codex запущенным.

Схема проблемы:

Current Codex
→ update found
→ update downloaded
→ new package staged
→ Codex closes
→ Windows still considers Codex running
→ AppX Register fails with 0x80070005
→ old Codex starts
→ Update appears again

В этом случае proxy/v2rayN причиной не был.

---

## Быстрая проверка

Проверить текущий пакет:

```powershell
Get-AppxPackage OpenAI.Codex | Select Name, Version, Status, InstallLocation
```

Нормальное состояние:

`Status = Ok`

Актуальные логи Codex обычно находятся здесь:

`%LOCALAPPDATA%\Codex\Logs\`

В логах искать:

`windows-store-updater`
`buildVersion`
`manifestBuildVersion`
`hasUpdate`
`overallState`
`download_completed`
`result=succeeded`

Если видно примерно следующее:

```text
buildVersion=<OLD_VERSION>
manifestBuildVersion=<NEW_VERSION>
hasUpdate=true
overallState=Completed
action=download_completed
result=succeeded
```

значит обнаружение и скачивание обновления прошли успешно. В этот момент не следует сразу обвинять proxy/VPN.

Проверить Windows AppXDeploymentServer:

```powershell
Get-WinEvent -FilterHashtable @{
    LogName='Microsoft-Windows-AppXDeploymentServer/Operational'
    StartTime=(Get-Date).AddHours(-1)
} -ErrorAction SilentlyContinue |
Where-Object {
    $_.Message -match 'OpenAI\.Codex|2p2nqsd0c76g0'
} |
Select-Object TimeCreated, Id, LevelDisplayName, Message |
Format-List
```

Если журнал содержит:

`0x80070005`

и сообщение о необходимости закрыть:

`OpenAI.Codex_2p2nqsd0c76g0!App`

применить проверенное решение ниже.

---

## Проверенное решение

Полностью закрыть Codex.

Проверить процессы:

```powershell
Get-Process | Where-Object { $_.ProcessName -match "Codex|OpenAI" }
```

Команда должна вернуть пустой результат.

Если процессы остались:

```powershell
taskkill /F /IM Codex.exe /T
```

Если `taskkill` сообщает, что процесс не найден, а `Get-Process` ничего не показывает — Codex уже закрыт.

Затем определить фактически подготовленную новую версию из журнала AppXDeploymentServer.

Там будет путь вида:

`C:\Program Files\WindowsApps\OpenAI.Codex_<NEW_VERSION>_x64__2p2nqsd0c76g0\AppxManifest.xml`

Номер версии нельзя угадывать.

Когда Codex полностью закрыт, вручную зарегистрировать уже подготовленный пакет:

```powershell
Add-AppxPackage -Register "C:\Program Files\WindowsApps\OpenAI.Codex_<NEW_VERSION>_x64__2p2nqsd0c76g0\AppxManifest.xml" -DisableDevelopmentMode
```

Именно эта команда в проверенном случае разорвала бесконечный цикл обновления.

После команды Codex может запуститься автоматически — это нормально.

После этого проверить результат:

```powershell
Get-AppxPackage OpenAI.Codex | Select Name, Version, Status, InstallLocation
```

Нужно убедиться, что:
- версия изменилась;
- `Status = Ok`;
- после запуска Codex больше не предлагает то же обновление.

---

## Важные замечания

Ошибки v2rayN вида:

`connection forcibly closed`
`wsasend`
`wsarecv`

могут быть просто следствием того, что Codex закрывается/перезапускается и сам обрывает локальные соединения.

Наличие таких ошибок само по себе не доказывает проблему с сетью.

Если Codex пишет:

`download_completed`
`result=succeeded`

то скачивание обновления уже прошло успешно.

Также не путать:
- версию Windows-приложения Codex;
- версию Codex CLI;
- версию внутреннего `codex-primary-runtime`.

Это разные компоненты и номера версий у них могут отличаться.

---

## Правило для агента

Если пользователь говорит:

- «Codex опять не обновляется»;
- «обновление зациклилось»;
- «после перезапуска снова Update»;
- «Codex скачивает обновление, но версия не меняется»;

агент сначала читает этот документ и проверяет известный сценарий.

Если подтверждён случай:

`0x80070005 + OpenAI.Codex still running`

не нужно повторять полное расследование с нуля.

Достаточно:

1. Проверить текущую AppX-версию.
2. Подтвердить отсутствие процессов Codex/OpenAI.
3. Определить фактически staged новую версию.
4. Выполнить `Add-AppxPackage -Register` для её `AppxManifest.xml`.
5. Проверить установленную версию после операции.

Если симптомы или ошибка отличаются — перейти к новой диагностике.

---

## Как пополнять документ

Каждую новую реально решённую техническую проблему добавлять отдельным разделом в формате:

### Симптомы
Что видел пользователь.

### Причина
Что фактически оказалось неисправно.

### Проверка
Как быстро подтвердить причину.

### Решение
Какая команда или действие реально помогли.

### Проверка результата
Как убедиться, что проблема действительно устранена.

Не добавлять неподтверждённые догадки как готовые решения.