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
                Write-Log "ПРЕДУПРЕЖДЕНИЕ: штатное удаление не сработало (код $($uninstallProc.ExitCode)). Пробую принудительно вычистить остатки из реестра Windows Installer напрямую."
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
