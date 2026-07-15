# Развёртывание Zabbix Agent на предприятии через GPO

Методичка по автоматической установке Zabbix Agent на все компьютеры домена
через Group Policy Startup Script. Написана по горячим следам реального
внедрения — со всеми граблями, на которые уже наступили, чтобы не наступать
повторно.

## Содержание

- [Архитектура решения](#архитектура-решения)
- [Инфраструктура](#инфраструктура)
- [Быстрый старт (если всё уже настроено)](#быстрый-старт)
- [Настройка с нуля](#настройка-с-нуля)
- [Скрипт развёртывания](#скрипт-развёртывания)
- [Регистрация хостов в Zabbix](#регистрация-хостов-в-zabbix)
- [Мониторинг логов развёртывания](#мониторинг-логов-развёртывания)
- [Известные проблемы и их решения](#известные-проблемы-и-их-решения)
- [Чек-лист диагностики](#чек-лист-диагностики-если-агент-не-встал)
- [Обслуживание](#обслуживание)

---

## Архитектура решения

```
┌─────────────────┐         ┌──────────────────────┐
│  Zabbix Server   │◄────────│  Компьютеры домена    │
│  192.168.1.241   │  10050  │  (Windows 7...11)     │
└─────────────────┘         └──────────────────────┘
                                       ▲
                                       │ Startup Script (GPO)
                                       │
                          ┌────────────┴────────────┐
                          │   SYSVOL контроллера DC   │
                          │   Deploy-ZabbixAgent.ps1  │
                          └────────────┬────────────┘
                                       │ читает MSI, пишет логи
                                       ▼
                          ┌────────────────────────┐
                          │  \\FILESERVER1\distr     │
                          │  ├── zabbix_agent-*.msi  │
                          │  └── Logs\<HOSTNAME>\    │
                          └────────────────────────┘
```

**Почему так:**
- Сам **скрипт** лежит в SYSVOL (реплицируется вместе с GPO на все DC) — не
  зависит от доступности файл-сервера в момент старта загрузки компьютера.
- **MSI-файл и логи** лежат на файловом сервере — они нужны позже (не в первую
  секунду загрузки), поэтому некритично, если сеть на этот момент чуть медленнее
  поднимается.

---

## Инфраструктура

| Параметр | Значение |
|---|---|
| Домен AD | `tfi.ru` |
| Zabbix Server | `192.168.1.241` |
| Файловый сервер (MSI + логи) | `\\FILESERVER1\distr` |
| GPO | `Deploy - Zabbix Agent` |
| Путь к скрипту в SYSVOL | `\\tfi.ru\SysVol\tfi.ru\Policies\{GUID-политики}\Machine\Scripts\Startup\Deploy-ZabbixAgent.ps1` |
| Способ сбора данных Zabbix | Zabbix agent, пассивные проверки (сервер сам стучится в агент на порт 10050) |
| Дистрибутив агента | `zabbix_agent-7.4.10-windows-amd64-openssl.msi` |

---

## Быстрый старт

Если инфраструктура уже настроена и нужно просто добавить новую машину:

1. Убедись, что компьютер лежит в OU, к которой привязан GPO `Deploy - Zabbix Agent`
   (см. [известную проблему про контейнер Computers](#1-компьютер-лежит-в-контейнере-computers-а-не-в-ou))
2. Перезагрузи компьютер (или дождись следующей плановой перезагрузки)
3. Проверь результат:
   ```cmd
   sc query "Zabbix Agent"
   ```
4. Проверь лог на самой машине:
   ```cmd
   type C:\Windows\Temp\zabbix_agent_deploy.log
   ```
   или централизованно на файл-сервере:
   ```
   \\FILESERVER1\distr\Logs\<ИМЯ_КОМПЬЮТЕРА>\zabbix_agent_deploy.log
   ```
5. Если хост не появился в Zabbix сам — добавь вручную (**Data collection → Hosts
   → Create host**) либо настрой [Network Discovery](#регистрация-хостов-в-zabbix)

---

## Настройка с нуля

### 1. Общая папка

Создай `\\FILESERVER1\distr` (или используй существующую) со следующей структурой:
```
distr\
├── zabbix_agent-7.4.10-windows-amd64-openssl.msi
└── Logs\                      ← создаётся автоматически скриптом, но папку
                                  лучше создать заранее, чтобы сразу выставить права
```

**Права доступа (важно — это отдельная точка отказа):**

| Папка | Группа | Права |
|---|---|---|
| `distr` (корень, с MSI) | `Domain Computers` | **Чтение** (Read) — и на Share, и на NTFS |
| `distr\Logs` | `Domain Computers` | **Изменение** (Modify) — и на Share, и на NTFS |

Раздельные права принципиальны: компьютеры должны иметь возможность *читать*
MSI, но *писать* — только в подпапку логов.

### 2. Компьютеры должны быть в OU, а не в контейнере Computers

**Это самая частая причина, почему GPO "не применяется" на конкретной машине.**
См. подробности в разделе [Известные проблемы, пункт 1](#1-компьютер-лежит-в-контейнере-computers-а-не-в-ou).

Проверить и настроить редирект новых компьютеров сразу в нужную OU:
```cmd
redircmp "OU=_COMP,DC=tfi,DC=ru"
```
(выполняется один раз на контроллере домена с правами Domain Admin;
подставь свою целевую OU)

### 3. Создание GPO

1. **Group Policy Management** (`gpmc.msc`) → правый клик на целевую OU →
   **Create a GPO in this domain, and Link it here**
2. Назови, например, `Deploy - Zabbix Agent`
3. **Edit** → **Computer Configuration → Policies → Windows Settings →
   Scripts (Startup/Shutdown) → Startup**
4. Вкладка **PowerShell Scripts** → **Add** → **Обзор** → **Показать файлы...**
   (это сразу открывает нужную SYSVOL-папку) → положи туда
   `Deploy-ZabbixAgent.ps1` → выбери его

⚠️ **Не указывай путь напрямую на файл-сервер** (`\\FILESERVER1\...`) в поле
скрипта — только файл из SYSVOL. Иначе при недоступности сети в момент старта
скрипт вообще не запустится, и это не будет видно ни в каком логе (сам механизм
логирования — часть незапустившегося скрипта).

### 4. Security Filtering (кому применяется GPO)

В GPMC → выбранный GPO → **Scope → Security Filtering** → **Добавить...** →
`Domain Computers`.

Не полагайся на неявное поведение "Authenticated Users" — добавляй `Domain
Computers` явно, это самый предсказуемый вариант.

### 5. Область действия (если компьютеры разбросаны по нескольким OU)

Если целевые машины лежат не в одной OU, а в нескольких (например, и в
основной структуре по отделам, и в отдельном `_COMP`) — GPO нужно **привязать
к каждой** из этих OU отдельно:

Правый клик на OU → **Link an Existing GPO...** → выбрать `Deploy - Zabbix Agent`

### 6. Применение

```cmd
gpupdate /force      # подтягивает саму политику (само назначение скрипта)
shutdown /r /t 0      # ТОЛЬКО перезагрузка реально запускает Startup Script
```
`gpupdate /force` **не запускает** сам Startup Script — он лишь обновляет,
какие скрипты вообще назначены. Выполнение происходит исключительно при
загрузке компьютера, до входа пользователя.

---

## Скрипт развёртывания

Полный текст `Deploy-ZabbixAgent.ps1` — актуальная версия на момент написания
методички. Файл должен быть сохранён в кодировке **UTF-8 с BOM**
(см. [проблему с кодировкой](#4-кириллица-в-логах-превращается-в-кашу)).

```powershell
# ============================================================
# Deploy-ZabbixAgent.ps1
# Тихая установка Zabbix Agent через GPO Startup Script
# Запускается в контексте SYSTEM на каждом компьютере домена
# ============================================================

# --- Настройки: поменяй под себя ---
$ZabbixServer   = "192.168.1.241"     # IP Zabbix-сервера (пассивные проверки)
$SharePath      = "\\FILESERVER1\distr"   # UNC-путь к папке с MSI
$MsiName        = "zabbix_agent-7.4.10-windows-amd64-openssl.msi"
$LogShareRoot   = "$SharePath\Logs"   # Куда складывать логи со всех машин
$hostname       = $env:COMPUTERNAME

# --- Локальные логи (пишем всегда, сеть на старте может быть ещё не готова) ---
$LogFile        = "C:\Windows\Temp\zabbix_agent_deploy.log"
$MsiInstallLog  = "C:\Windows\Temp\zabbix_msi_install.log"
$MsiUninstallLog = "C:\Windows\Temp\zabbix_old_uninstall.log"

function Write-Log {
    param([string]$Message)
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -Path $LogFile -Value "$timestamp  $Message" -Encoding UTF8
}

# Запускает msiexec с жёстким таймаутом - если процесс завис (например, из-за
# невалидных аргументов вроде пустого ProductCode), убиваем его через $TimeoutSec,
# вместо того чтобы навсегда заблокировать выполнение Startup Script на всём компьютере.
function Invoke-MsiExecWithTimeout {
    param(
        [string[]]$Arguments,
        [int]$TimeoutSec = 180
    )

    # Не используем Start-Process -PassThru: на части систем (особенно старых,
    # PowerShell 2.0 / Win7) его .ExitCode ненадёжно заполняется даже после
    # завершения процесса. Работаем напрямую через .NET Process - там это стабильно.
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = "msiexec.exe"
    $psi.Arguments = ($Arguments -join ' ')
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true

    $proc = New-Object System.Diagnostics.Process
    $proc.StartInfo = $psi
    [void]$proc.Start()

    $finished = $proc.WaitForExit($TimeoutSec * 1000)

    if (-not $finished) {
        Write-Log "ПРЕДУПРЕЖДЕНИЕ: msiexec завис дольше $TimeoutSec сек, принудительно завершаю процесс (PID $($proc.Id))."
        try { $proc.Kill() } catch {}
        return 1618   # код "another install already in progress" - используем как маркер сбоя/зависания
    }

    return $proc.ExitCode
}

# --- В конце (в любом случае: успех, ошибка, ранний exit) копируем логи на шару ---
function Copy-LogsToShare {
    try {
        $targetFolder = Join-Path $LogShareRoot $hostname
        if (-not (Test-Path $targetFolder)) {
            New-Item -Path $targetFolder -ItemType Directory -Force | Out-Null
        }

        foreach ($f in @($LogFile, $MsiInstallLog, $MsiUninstallLog)) {
            if (Test-Path $f) {
                Copy-Item -Path $f -Destination $targetFolder -Force -ErrorAction Stop
            }
        }
        Write-Log "Логи скопированы на шару: $targetFolder"
    } catch {
        # Если сети/прав нет - просто оставляем логи локально, не роняем скрипт
        Write-Log "ПРЕДУПРЕЖДЕНИЕ: не удалось скопировать логи на шару: $_"
    }
}

try {
    Write-Log "=== Запуск скрипта развёртывания Zabbix Agent ==="
    Write-Log "ОС: $([System.Environment]::OSVersion.VersionString), PowerShell: $($PSVersionTable.PSVersion)"

    # --- Проверка: агент уже установлен и служба работает нормально? ---
    $existing = Get-Service -Name "Zabbix Agent*" -ErrorAction SilentlyContinue

    if ($existing -and $existing.Status -eq "Running") {
        Write-Log "Zabbix Agent уже установлен и служба работает ($($existing.Name)). Установка пропущена."
        exit 0
    }

    # Служба ЕСТЬ, но не запущена - это ещё не повод сносить установку (может, кто-то
    # поставил агент вручную и служба просто остановлена/не успела стартовать).
    # Сначала пробуем аккуратно запустить, и только если не вышло - идём в полную очистку.
    if ($existing) {
        Write-Log "Служба $($existing.Name) существует, но не запущена (статус: $($existing.Status)). Пробую запустить, не трогая установку..."
        try {
            Set-Service -Name $existing.Name -StartupType Automatic -ErrorAction SilentlyContinue
            Start-Service -Name $existing.Name -ErrorAction Stop
            Start-Sleep -Seconds 3
            $recheck = Get-Service -Name $existing.Name -ErrorAction SilentlyContinue
            if ($recheck -and $recheck.Status -eq "Running") {
                Write-Log "Служба успешно запущена без переустановки. Существующая установка сохранена."
                exit 0
            } else {
                Write-Log "Служба не смогла подняться (статус: $($recheck.Status)). Перехожу к переустановке."
            }
        } catch {
            Write-Log "Не удалось запустить существующую службу: $_. Перехожу к переустановке."
        }
    }

    # ============================================================
    # ЭТАП ОЧИСТКИ: убираем "хвосты" от старых/сломанных установок
    # ============================================================
    Write-Log "Служба Zabbix Agent не найдена в рабочем состоянии. Проверяю следы старых установок..."

    $cleanupNeeded = $false

    $uninstallPaths = @(
        "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*",
        "HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*"
    )

    foreach ($path in $uninstallPaths) {
        $found = Get-ItemProperty -Path $path -ErrorAction SilentlyContinue |
                 Where-Object { $_.DisplayName -like "*Zabbix*" }

        foreach ($item in $found) {
            $productCode = $item.PSChildName

            # Пропускаем битые/пустые записи - без этого msiexec /x "" вешает весь скрипт намертво
            # (не используем [string]::IsNullOrWhiteSpace - это .NET 4.0+, на Win7 может не быть)
            if (-not $productCode -or $productCode.Trim() -eq "" -or -not $item.DisplayName -or $item.DisplayName.Trim() -eq "") {
                Write-Log "Пропускаю некорректную запись в реестре (пустой DisplayName или ProductCode) - похоже на мусорный ключ, не трогаю."
                continue
            }

            $cleanupNeeded = $true
            Write-Log "Найден старый пакет в реестре: $($item.DisplayName) [$productCode]. Удаляю..."

            $uninstallArgs = @("/x", $productCode, "/qn", "/norestart", "HOSTNAME=$hostname", "/l*v", $MsiUninstallLog)
            $uninstallExitCode = Invoke-MsiExecWithTimeout -Arguments $uninstallArgs -TimeoutSec 120
            Write-Log "Удаление старого пакета завершилось с кодом: $uninstallExitCode"

            if ($uninstallExitCode -ne 0) {
                Write-Log "ПРЕДУПРЕЖДЕНИЕ: штатное удаление не сработало (код $uninstallExitCode). Пробую принудительно вычистить остатки из реестра Windows Installer напрямую."
                # Крайняя мера: если MSI категорически отказывается снимать продукт (например,
                # из-за битой LaunchCondition), вычищаем регистрацию продукта из реестра вручную,
                # чтобы новый msiexec /i воспринял это как чистую установку, а не "reconfigure".
                $regPaths = @(
                    "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\$productCode",
                    "HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\$productCode",
                    "HKLM:\SOFTWARE\Classes\Installer\Products\*",
                    "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Installer\UserData\S-1-5-18\Products\*"
                )
                foreach ($rp in $regPaths) {
                    Get-Item -Path $rp -ErrorAction SilentlyContinue | Where-Object {
                        $_.PSChildName -eq $productCode.Trim('{}') -or $_.PSPath -like "*$($productCode.Trim('{}'))*"
                    } | ForEach-Object {
                        Write-Log "Удаляю остаточный ключ реестра: $($_.PSPath)"
                        Remove-Item -Path $_.PSPath -Recurse -Force -ErrorAction SilentlyContinue
                    }
                }
            }
        }
    }

    $staleService = Get-Service -Name "Zabbix Agent*" -ErrorAction SilentlyContinue
    if ($staleService) {
        $cleanupNeeded = $true
        Write-Log "Осталась служба $($staleService.Name) после удаления пакета. Удаляю через sc.exe..."
        Stop-Service -Name $staleService.Name -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 2
        & sc.exe delete $staleService.Name | Out-Null

        # Windows держит службу в состоянии "marked for deletion", пока не закроются все хендлы -
        # ждём, пока она реально исчезнет, иначе новый MSI не сможет создать службу с тем же именем
        $waited = 0
        while ((Get-Service -Name $staleService.Name -ErrorAction SilentlyContinue) -and $waited -lt 30) {
            Start-Sleep -Seconds 2
            $waited += 2
        }
        Write-Log "Ожидание освобождения имени службы: $waited сек."
    }

    # net.exe работает одинаково на всех версиях Windows (в т.ч. Win7),
    # в отличие от Get-LocalGroup/Remove-LocalGroup, которых на Win7 нет вообще
    $groupCheck = & net.exe localgroup Zabbix 2>&1
    if ($LASTEXITCODE -eq 0) {
        $cleanupNeeded = $true
        Write-Log "Найдена оставшаяся локальная группа 'Zabbix'. Удаляю..."
        & net.exe localgroup Zabbix /delete 2>&1 | Out-Null
    }

    $staleFolder = "C:\Program Files\Zabbix Agent"
    if (Test-Path $staleFolder) {
        $cleanupNeeded = $true
        Write-Log "Найдена оставшаяся папка $staleFolder. Удаляю..."
        Remove-Item -Path $staleFolder -Recurse -Force -ErrorAction SilentlyContinue
    }

    if ($cleanupNeeded) {
        Write-Log "Очистка завершена. Продолжаю установку."
    } else {
        Write-Log "Следов старых установок не найдено. Устанавливаю с чистого листа."
    }

    # --- Проверка доступности сетевой папки ---
    $msiPath = Join-Path $SharePath $MsiName
    if (-not (Test-Path $msiPath)) {
        Write-Log "ОШИБКА: MSI не найден по пути $msiPath. Проверь доступность шары и права Domain Computers."
        exit 1
    }

    # --- Копируем MSI локально ---
    $localMsi = "C:\Windows\Temp\$MsiName"
    try {
        Copy-Item -Path $msiPath -Destination $localMsi -Force
        Write-Log "MSI скопирован локально: $localMsi"
    } catch {
        Write-Log "ОШИБКА копирования MSI: $_"
        exit 1
    }

    # --- Тихая установка ---
    $arguments = @(
        "/i", "`"$localMsi`"",
        "/qn", "/norestart",
        "SERVER=$ZabbixServer",
        "SERVERACTIVE=$ZabbixServer",
        "HOSTNAME=$hostname",
        "ENABLEPATH=1",
        "/l*v", $MsiInstallLog
    )

    Write-Log "Запуск установки: msiexec $($arguments -join ' ')"
    $installExitCode = Invoke-MsiExecWithTimeout -Arguments $arguments -TimeoutSec 180
    Write-Log "msiexec завершился с кодом: $installExitCode"

    if ($installExitCode -ne 0) {
        Write-Log "ОШИБКА: установка завершилась с ошибкой, см. $MsiInstallLog"
        exit 1
    }

    # netsh advfirewall работает одинаково на всех версиях Windows (в т.ч. Win7),
    # в отличие от Get-NetFirewallRule/New-NetFirewallRule (появились только с Win8/2012)
    $ruleName = "Zabbix Agent (TCP 10050 from Server)"
    $ruleCheck = & netsh advfirewall firewall show rule name="$ruleName" 2>&1
    $ruleExists = ($LASTEXITCODE -eq 0) -and ($ruleCheck -notmatch "No rules match")

    if (-not $ruleExists) {
        & netsh advfirewall firewall add rule `
            name="$ruleName" `
            dir=in `
            action=allow `
            protocol=TCP `
            localport=10050 `
            remoteip=$ZabbixServer `
            profile=domain,private 2>&1 | Out-Null
        Write-Log "Правило брандмауэра создано: разрешён вход TCP 10050 от $ZabbixServer"
    } else {
        Write-Log "Правило брандмауэра уже существует, пропущено."
    }

    # --- Убеждаемся, что служба запущена и в автозапуске ---
    Start-Sleep -Seconds 3
    $svc = Get-Service -Name "Zabbix Agent*" -ErrorAction SilentlyContinue
    if ($svc) {
        Set-Service -Name $svc.Name -StartupType Automatic
        if ($svc.Status -ne "Running") {
            Start-Service -Name $svc.Name
        }
        Write-Log "Служба $($svc.Name) запущена и в автозапуске. Установка успешно завершена."
    } else {
        Write-Log "ПРЕДУПРЕЖДЕНИЕ: служба Zabbix Agent не найдена после установки."
    }

    # --- Уборка ---
    Remove-Item -Path $localMsi -Force -ErrorAction SilentlyContinue

    Write-Log "=== Скрипт завершён ==="
    exit 0
}
finally {
    # Выполняется всегда: при успехе, при ошибке, при любом exit выше
    Copy-LogsToShare
}
```

**Логика скрипта коротко:**
1. Если служба уже `Running` — ничего не делаем, выходим
2. Если служба есть, но не запущена — пробуем просто её запустить, не трогая установку
3. Если не завелась / отсутствует вообще — чистим все следы старых установок
   (реестр, службу, локальную группу, папку) и ставим агент с чистого листа
4. Открываем нужный порт в файрволе
5. В любом случае (успех/ошибка) копируем локальные логи на сетевую шару

---

## Регистрация хостов в Zabbix

Скрипт **только ставит агент** на компьютер — сам он **не появляется** в
списке хостов Zabbix автоматически. Два варианта:

### Вариант A — вручную (годится для 1-2 машин)
**Data collection → Hosts → Create host** → указать имя, IP, привязать шаблон
`Windows by Zabbix agent`.

### Вариант B — Network Discovery (рекомендуется для массового парка)
1. **Data collection → Discovery → Create discovery rule**
   IP-диапазон сети (например `192.168.1.1-254`), Check type: **Zabbix agent**,
   порт 10050
2. **Data collection → Actions → Discovery actions → Create action**:
   условие "Service type = Zabbix agent" → операция **Add host** + **Link
   template** (`Windows by Zabbix agent`)

Так все машины с работающим агентом сами появляются в Zabbix — не нужно руками
заводить каждую из 10-50+ машин.

**⚠️ Важно:** если ставили агент через GPO, а хост в Zabbix не завёлся сам
(Discovery не настроен) — такие "потеряшки" легко найти, сравнив список папок
в `\\FILESERVER1\distr\Logs\` (там папка появляется на каждую машину, где
скрипт реально отработал) со списком хостов в Zabbix. Расхождение = агент стоит,
но хост не заведён.

---

## Мониторинг логов развёртывания

После каждого прогона на компьютере создаются (локально и копией на шару):

| Файл | Содержимое |
|---|---|
| `zabbix_agent_deploy.log` | Общий ход выполнения скрипта, ключевые решения |
| `zabbix_msi_install.log` | Подробный verbose-лог установки (от msiexec) |
| `zabbix_old_uninstall.log` | Подробный verbose-лог удаления старого пакета (если было) |

Расположение:
- Локально: `C:\Windows\Temp\`
- Централизованно: `\\FILESERVER1\distr\Logs\<ИМЯ_КОМПЬЮТЕРА>\`

**MSI verbose-логи (`/l*v`) в кодировке UTF-16 LE** — при открытии в Notepad++
или через PowerShell (`Get-Content -Encoding Unicode`) читаются нормально;
обычный `type` в cmd тоже показывает верно.

---

## Известные проблемы и их решения

### 1. Компьютер лежит в контейнере `Computers`, а не в OU

**Симптом:** GPO создан и привязан к нужной OU, Security Filtering настроен
правильно, но `gpresult /r /scope computer` на конкретной машине не показывает
GPO в списке применённых политик.

**Причина:** новые компьютеры при вводе в домен по умолчанию попадают в
системный контейнер `CN=Computers` — а GPO **физически нельзя** привязать к
контейнеру, только к OU/домену/сайту.

**Решение:**
```
dsa.msc → Вид → Дополнительные компоненты (Advanced Features) →
найти компьютер в контейнере Computers → Move → переместить в нужную OU
```
Чтобы не повторялось для новых машин:
```cmd
redircmp "OU=_COMP,DC=tfi,DC=ru"
```

### 2. LaunchCondition на Hostname блокирует удаление старого пакета

**Симптом:** `msiexec /x {ProductCode}` падает с кодом `1603`, а в подробном
логе видно:
```
Product: Zabbix Agent (64-bit) -- Allowed symbols for Hostname are
alphanumeric, space and . _ - ,
Action ended: LaunchConditions. Return value 3.
```

**Причина:** при вызове `/x` без явного параметра `HOSTNAME=` инсталлятор
использует **закешированное значение из самой первой (возможно, ручной или
неудачной) установки**. Если то значение содержало недопустимые символы —
launch condition проваливается ещё до старта удаления.

**Решение:** всегда передавать валидный `HOSTNAME=$env:COMPUTERNAME`
даже при `/x` (уже реализовано в скрипте выше).

### 3. "Reconfigure" вместо реальной установки (ExitCode 0 за 1 секунду)

**Симптом:** `msiexec /i` отрабатывает подозрительно быстро (~1 сек),
возвращает `0`, но служба после этого не появляется.

**Причина:** если предыдущее удаление того же `ProductCode` провалилось
(см. пункт 2), Windows Installer всё ещё считает пакет "установленным" —
новый `/i` с тем же кодом продукта воспринимается не как установка, а как
"reconfigure" (переконфигурация уже существующей записи), файлы не
копируются, служба не создаётся.

**Решение:** если штатный `/x` не сработал — скрипт вручную вычищает
регистрацию продукта прямо из реестра Windows Installer (`Uninstall`,
`Installer\Products`, `Installer\UserData\S-1-5-18\Products`), чтобы
следующая установка была воспринята как чистая (уже реализовано).

### 4. Кириллица в логах превращается в кашу

**Симптом:** русские сообщения в `zabbix_agent_deploy.log` выглядят как
`Р—Р°РїСѓСЃРє` вместо `Запуск`.

**Причина:** Windows PowerShell 5.1 (в отличие от pwsh 7+) при отсутствии
**BOM** в начале `.ps1`-файла по умолчанию парсит скрипт в кодировке ANSI
(cp1251 для русской Windows), даже если сам файл физически сохранён в UTF-8.
Строковые литералы с кириллицей внутри скрипта читаются неправильно ещё на
этапе парсинга — и затем корректно (но уже испорченными) сохраняются как UTF-8.

**Решение:** сохранять `.ps1`-файл в кодировке **UTF-8 с BOM** (не просто
UTF-8). При редактировании в Notepad++: Encoding → UTF-8-BOM → Save.
При сохранении из PowerShell/скриптом — добавить байты `EF BB BF` в начало файла.

### 5. `Start-Process -PassThru` не возвращает `.ExitCode` на старых системах

**Симптом:** в логе `msiexec завершился с кодом: ` — код пустой, хотя
подробный verbose-лог показывает `Installation completed successfully`
и реальный код `0`.

**Причина:** известная особенность `Start-Process -PassThru` — на части
систем (особенно старых: Windows 7 + PowerShell 2.0) свойство `.ExitCode`
ненадёжно заполняется даже после завершения процесса.

**Решение:** не использовать `Start-Process` для получения кода возврата,
а работать напрямую с `System.Diagnostics.Process` (см. функцию
`Invoke-MsiExecWithTimeout` в скрипте выше) — это работает одинаково
надёжно на любой версии PowerShell.

### 6. Windows 7 не знает часть современных cmdlet'ов

**Симптом:** ошибки вида "term is not recognized" на машинах с Windows 7
(PowerShell 2.0 из коробки).

**Причина:**
- `Get-LocalGroup` / `Remove-LocalGroup` — появились только в PowerShell 5.1
  на Windows 10/Server 2016+, на Win7 их нет **даже после обновления PowerShell**
- `Get-NetFirewallRule` / `New-NetFirewallRule` — требуют модуль NetSecurity
  (Windows 8/Server 2012+)
- `[string]::IsNullOrWhiteSpace` — требует .NET Framework 4.0+, на Win7
  из коробки только 3.5

**Решение:** заменены на кроссплатформенные аналоги:
- `net.exe localgroup` вместо `Get/Remove-LocalGroup`
- `netsh advfirewall firewall` вместо `Get/New-NetFirewallRule`
- ручная проверка `-not $x -or $x.Trim() -eq ""` вместо `IsNullOrWhiteSpace`

Скрипт логирует версию ОС и PowerShell первой строкой — это сильно ускоряет
диагностику подобных проблем в будущем.

### 7. Пустые/мусорные записи в реестре вешают `msiexec` намертво

**Симптом:** скрипт останавливается на строке "Проверяю следы старых
установок..." и не двигается дальше; никакой ошибки, просто тишина.

**Причина:** если в ветке `Uninstall` реестра находится запись с пустым
`DisplayName`/`ProductCode` (случается при повреждённых предыдущих попытках
установки), скрипт пытался выполнить `msiexec /x ""` — с пустым идентификатором
пакета `msiexec` не завершается с ошибкой, а зависает.

**Решение:** такие записи теперь пропускаются с явным логированием
("Пропускаю некорректную запись...") до попытки что-либо с ними сделать.
Дополнительно — общий таймаут на любой вызов `msiexec` (см. пункт 5), как
защита от ещё не встреченных подобных случаев.

### 8. Два одновременных Startup Script на одной машине

**Симптом:** скрипт в логе запускается дважды подряд с разницей в секундах.

**Причина:** при переносе скрипта с прямой сетевой ссылки
(`\\FILESERVER1\distr\...`) на SYSVOL-версию старую запись забыли удалить —
осталось два назначенных Startup Script, оба выполняющих один и тот же файл.

**Решение:** GPO → Computer Configuration → Scripts → Startup → PowerShell
Scripts — должна быть **ровно одна** запись, указывающая на файл в SYSVOL.

---

## Чек-лист диагностики (если агент не встал)

1. **Компьютер вообще перезагружался?**
   ```cmd
   systeminfo | findstr /B /C:"System Boot Time"
   ```
2. **GPO применился?**
   ```cmd
   gpresult /r /scope computer
   ```
   Ищи `Deploy - Zabbix Agent` в разделе "Примененные объекты групповой политики".
   Если нет — проверь [пункт 1 известных проблем](#1-компьютер-лежит-в-контейнере-computers-а-не-в-ou).
3. **Есть локальный лог?**
   ```cmd
   type C:\Windows\Temp\zabbix_agent_deploy.log
   ```
   Если файла нет вообще — скрипт не запустился (см. пункт 8, либо проверь
   само назначение Startup Script в GPO).
4. **Служба физически есть?**
   ```cmd
   sc query "Zabbix Agent"
   ```
5. **Порт открыт и виден с сервера?**
   С Zabbix-сервера:
   ```bash
   nc -zv <IP-компьютера> 10050
   ```
   Если недоступен — проверь `netsh advfirewall firewall show rule
   name="Zabbix Agent (TCP 10050 from Server)"` на машине агента, и что
   антивирус/сторонний файрвол не блокирует порт отдельно от штатного Windows Firewall.
6. **SYSVOL реплицировался на все контроллеры домена (если их несколько)?**
   ```cmd
   repadmin /replsummary
   ```
   Проверь `Fails`/`Delta` — задержка репликации SYSVOL means часть машин при
   загрузке может видеть устаревшую версию скрипта.

---

## Обслуживание

### Добавление новой машины
Просто убедись, что она в правильной OU — остальное произойдёт автоматически
при следующей перезагрузке.

### Обновление версии Zabbix Agent
1. Положи новый MSI в `\\FILESERVER1\distr\` (можно оставить старый рядом на
   время миграции)
2. Поменяй `$MsiName` в скрипте на новое имя файла
3. Обнови файл в SYSVOL
4. Возможно, потребуется поменять условие проверки версии в скрипте, если
   нужно принудительно обновлять уже установленные агенты (текущая версия
   скрипта только ставит с нуля, если служба не работает — апгрейд рабочей
   установки не триггерится автоматически)

### Массовая принудительная перезагрузка (чтобы не ждать, пока люди сами перезагрузят ПК)
```powershell
$computers = Get-ADComputer -Filter * -SearchBase "OU=_COMP,DC=tfi,DC=ru" |
             Select-Object -ExpandProperty Name

foreach ($pc in $computers) {
    Write-Host "Перезагружаю $pc..."
    Restart-Computer -ComputerName $pc -Force -ErrorAction SilentlyContinue
}
```
⚠️ Предупреди пользователей заранее или делай это в конце рабочего дня —
скрипт выключит компьютеры без подтверждения.

---

*Документ составлен по итогам реального внедрения на предприятии, домен
`tfi.ru`. При переносе на другую инфраструктуру — обнови таблицу параметров
в разделе [Инфраструктура](#инфраструктура).*
