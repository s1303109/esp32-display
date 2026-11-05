param(
    [ValidateSet('Install', 'Disable', 'Foreground')]
    [string]$Mode = 'Install'
)

# Windows PowerShell 5.1 is included with Windows 11. This script deliberately
# uses a per-user Python installation and never requests administrator rights.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$pythonVersion = '3.14.4'
$pythonUrl = 'https://www.python.org/ftp/python/3.14.4/python-3.14.4-amd64.exe'
# SHA-256 in Python's official Sigstore bundle for this exact installer:
# https://www.python.org/ftp/python/3.14.4/python-3.14.4-amd64.exe.sigstore
$pythonSha256 = 'b571567bd11ea98fd7a2cf85791d2c8557a63b1e04e9d1dae665a275cac87f1b'
$appDir = Join-Path $env:LOCALAPPDATA 'ESP32MusicDisplay'
$defaultPython = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python314\python.exe'
$script:lastPythonProbe = 'No Python candidate was checked.'

function Assert-SupportedWindows {
    if (-not [Environment]::Is64BitOperatingSystem -or
        ($env:PROCESSOR_ARCHITECTURE -ne 'AMD64' -and
         $env:PROCESSOR_ARCHITEW6432 -ne 'AMD64')) {
        throw 'This package requires Windows 11 x64 (Intel/AMD).'
    }
    $version = Get-ItemProperty -Path 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion'
    if ([int]$version.CurrentBuildNumber -lt 22000) {
        throw 'This package requires Windows 11 (build 22000 or newer).'
    }
}

function Test-CompatiblePython([string]$candidate) {
    if (-not $candidate -or -not (Test-Path -LiteralPath $candidate -PathType Leaf)) {
        $script:lastPythonProbe = "Python executable not found: $candidate"
        return $false
    }
    # No quotes in the -c argument: Windows PowerShell 5.1 can strip quotes
    # while passing native command arguments. The OS preflight checks AMD64.
    $probe = 'import sys,struct;print(sys.version_info[0]*100+sys.version_info[1],struct.calcsize(chr(80)))'
    try {
        $output = & $candidate -c $probe 2>&1
        $exitCode = $LASTEXITCODE
        $result = [string]($output | Select-Object -Last 1)
        if ($exitCode -ne 0 -or $result.Trim() -ne '314 8') {
            $script:lastPythonProbe = "Version/bitness check for $candidate returned exit=$exitCode, output=$result"
            Write-Host $script:lastPythonProbe
            return $false
        }
        $pythonw = Join-Path (Split-Path -Parent $candidate) 'pythonw.exe'
        if (-not (Test-Path -LiteralPath $pythonw -PathType Leaf)) {
            $script:lastPythonProbe = "pythonw.exe not found beside $candidate"
            Write-Host $script:lastPythonProbe
            return $false
        }
        $pipOutput = & $candidate -m pip --version 2>&1
        $pipExit = $LASTEXITCODE
        if ($pipExit -ne 0) {
            $script:lastPythonProbe = "pip check for $candidate returned exit=$pipExit, output=$pipOutput"
            Write-Host $script:lastPythonProbe
            return $false
        }
        $script:lastPythonProbe = "Python $candidate is usable."
        return $true
    } catch {
        $script:lastPythonProbe = "Python check for $candidate failed: $($_.Exception.Message)"
        Write-Host $script:lastPythonProbe
        return $false
    }
}

function Find-CompatiblePython {
    $candidates = New-Object 'System.Collections.Generic.List[string]'
    $savedPath = Join-Path $appDir 'python_path.txt'
    if (Test-Path -LiteralPath $savedPath -PathType Leaf) {
        $candidates.Add((Get-Content -LiteralPath $savedPath -Encoding UTF8 -Raw).Trim())
    }
    $candidates.Add($defaultPython)
    $registryPath = 'HKCU:\Software\Python\PythonCore\3.14\InstallPath'
    $registration = Get-ItemProperty -Path $registryPath -ErrorAction SilentlyContinue
    if ($null -ne $registration) {
        $registeredExecutable = $registration.PSObject.Properties['ExecutablePath']
        if ($null -ne $registeredExecutable -and $registeredExecutable.Value) {
            $candidates.Add([string]$registeredExecutable.Value)
        }
    }
    $pathPython = Get-Command python.exe -CommandType Application -ErrorAction SilentlyContinue
    foreach ($command in @($pathPython)) {
        if ($null -ne $command -and $command.Source -notlike '*\WindowsApps\python.exe') {
            $candidates.Add([string]$command.Source)
        }
    }
    foreach ($candidate in ($candidates | Select-Object -Unique)) {
        if (Test-CompatiblePython $candidate) {
            return $candidate
        }
    }
    # A classic Python launcher may know a per-user install outside the usual
    # directory. Ask it for the exact executable, then validate that file.
    $launcher = Get-Command py.exe -CommandType Application -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($null -ne $launcher) {
        try {
            $located = & $launcher.Source -3.14 -c 'import sys;print(sys.executable)' 2>&1
            if ($LASTEXITCODE -eq 0) {
                $candidate = [string]($located | Select-Object -Last 1)
                if (Test-CompatiblePython $candidate.Trim()) {
                    return $candidate.Trim()
                }
            }
        } catch {
            Write-Host "Python launcher lookup failed: $($_.Exception.Message)"
        }
    }
    return $null
}

function Install-OfficialPython {
    Write-Host "Downloading official Python $pythonVersion installer..."
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $installer = Join-Path ([IO.Path]::GetTempPath()) (
        'ESP32MusicDisplay-Python-' + [Guid]::NewGuid().ToString('N') + '.exe'
    )
    try {
        Invoke-WebRequest -Uri $pythonUrl -OutFile $installer -UseBasicParsing
        $actualHash = (Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actualHash -ne $pythonSha256) {
            throw 'Python installer SHA-256 does not match the official release.'
        }
        $signature = Get-AuthenticodeSignature -LiteralPath $installer
        if ($signature.Status -ne 'Valid' -or $null -eq $signature.SignerCertificate) {
            throw "Python installer signature is invalid: $($signature.Status)."
        }
        $signer = $signature.SignerCertificate.GetNameInfo(
            [System.Security.Cryptography.X509Certificates.X509NameType]::SimpleName,
            $false
        )
        if ($signer -ne 'Python Software Foundation') {
            throw "Unexpected Python installer signer: $signer."
        }

        Write-Host 'Installing Python 3.14 for this Windows user...'
        $arguments = '/quiet InstallAllUsers=0 Include_launcher=1 ' +
            'InstallLauncherAllUsers=0 Include_pip=1 Include_test=0 ' +
            'PrependPath=1 TargetDir="' + (Split-Path -Parent $defaultPython) + '"'
        $process = Start-Process -FilePath $installer -ArgumentList $arguments -Wait -PassThru
        if ($process.ExitCode -notin @(0, 3010)) {
            throw "Python installer exited with code $($process.ExitCode)."
        }
        if (-not (Test-CompatiblePython $defaultPython)) {
            throw "Python installer finished, but the interpreter is not usable. $script:lastPythonProbe"
        }
        return $defaultPython
    } finally {
        Remove-Item -LiteralPath $installer -Force -ErrorAction SilentlyContinue
    }
}

function Get-OrInstallPython {
    $python = Find-CompatiblePython
    if ($python) {
        Write-Host "Using Python: $python"
        return $python
    }
    return Install-OfficialPython
}

function Disable-Background {
    $launcher = Join-Path $env:APPDATA (
        'Microsoft\Windows\Start Menu\Programs\Startup\ESP32 Music Display.vbs'
    )
    Remove-Item -LiteralPath $launcher -Force -ErrorAction SilentlyContinue
    if (Test-Path -LiteralPath $appDir -PathType Container) {
        Set-Content -LiteralPath (Join-Path $appDir 'stop.request') -Value 'stop' -Encoding ASCII
    }
    Write-Host 'Background bridge stop requested and automatic startup disabled.'
}

function Test-BackgroundRunning {
    try {
        $mutex = [System.Threading.Mutex]::OpenExisting('Local\ESP32MusicDisplayBridge')
        $mutex.Dispose()
        return $true
    } catch [System.Threading.WaitHandleCannotBeOpenedException] {
        return $false
    }
}

function Stop-BackgroundTemporarily {
    if (-not (Test-Path -LiteralPath $appDir -PathType Container)) {
        return
    }
    $stopPath = Join-Path $appDir 'stop.request'
    if (Test-BackgroundRunning) {
        Write-Host 'Stopping the hidden bridge while diagnostics run...'
        Set-Content -LiteralPath $stopPath -Value 'stop' -Encoding ASCII
        $deadline = [DateTime]::UtcNow.AddSeconds(45)
        while (Test-BackgroundRunning) {
            if ([DateTime]::UtcNow -ge $deadline) {
                Remove-Item -LiteralPath $stopPath -Force -ErrorAction SilentlyContinue
                throw 'The background bridge did not stop within 45 seconds.'
            }
            Start-Sleep -Milliseconds 250
        }
    }
    # An old stop request must not disable the next Windows login.
    Remove-Item -LiteralPath $stopPath -Force -ErrorAction SilentlyContinue
}

try {
    Assert-SupportedWindows
    if ($Mode -eq 'Disable') {
        Disable-Background
        exit 0
    }
    foreach ($name in @('background.pyw', 'bridge.py', 'cover.py', 'glyphs.py', 'timeline.py',
                       'wire.py', 'volume.py', 'requirements.txt',
                       'install_autostart.py', 'fonts\NotoSansCJK-Regular.ttc',
                       'fonts\OFL.txt')) {
        if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot $name) -PathType Leaf)) {
            throw "Missing $name. Extract the complete ZIP before running setup."
        }
    }
    $python = Get-OrInstallPython
    if ($Mode -eq 'Foreground') {
        $launcher = Join-Path $env:APPDATA (
            'Microsoft\Windows\Start Menu\Programs\Startup\ESP32 Music Display.vbs'
        )
        $restoreBackground = Test-Path -LiteralPath $launcher -PathType Leaf
        $stoppedForDiagnostics = $false
        try {
            Stop-BackgroundTemporarily
            $stoppedForDiagnostics = $true
            Write-Host 'Installing dependencies for foreground diagnostics...'
            & $python -m pip install --user -r (Join-Path $PSScriptRoot 'requirements.txt')
            if ($LASTEXITCODE -ne 0) {
                throw "Python package installation failed with code $LASTEXITCODE."
            }
            Write-Host 'Connecting to ESP32. Keep this window open; press Ctrl+C to stop.'
            & $python (Join-Path $PSScriptRoot 'bridge.py')
            if ($LASTEXITCODE -ne 0) {
                throw "Music bridge exited with code $LASTEXITCODE."
            }
        } finally {
            if ($restoreBackground -and $stoppedForDiagnostics) {
                $installedBridge = Join-Path $appDir 'background.pyw'
                $pythonw = Join-Path (Split-Path -Parent $python) 'pythonw.exe'
                if ((Test-Path -LiteralPath $installedBridge -PathType Leaf) -and
                    (Test-Path -LiteralPath $pythonw -PathType Leaf)) {
                    Start-Process -FilePath $pythonw -ArgumentList ('"' + $installedBridge + '"') -WindowStyle Hidden
                }
            }
        }
    } else {
        & $python (Join-Path $PSScriptRoot 'install_autostart.py')
        if ($LASTEXITCODE -ne 0) {
            throw "Background setup exited with code $LASTEXITCODE."
        }
    }
    exit 0
} catch {
    Write-Host "Setup failed: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
