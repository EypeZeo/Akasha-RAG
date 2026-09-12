[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$ProjectRoot
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# --------------------------------------------------------------------------
# SECURITY INVARIANT — do not "improve" this into something configurable.
#
# AKASHA_RUNTIME_MIRROR may redirect where the *bytes* of a runtime archive
# come from.  It must NEVER redirect where the *checksum* comes from: expected
# hashes are always fetched from the official upstream host.  A mirror that
# supplies both the archive and its hash can certify its own tampered payload,
# which makes the verification worthless.  There is deliberately no environment
# variable that overrides a checksum source.
# --------------------------------------------------------------------------

if ($PSVersionTable.PSVersion.Major -lt 5) {
    throw "PowerShell 5.1 or newer is required. Found $($PSVersionTable.PSVersion)."
}

# Windows PowerShell 5.1 on older builds can still negotiate TLS 1.0/1.1 by
# default, which github.com and nodejs.org refuse.  Opt in before any download.
try {
    [Net.ServicePointManager]::SecurityProtocol = `
        [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
} catch {
    # PowerShell 7+ manages this itself and may not expose the enum value.
}

[string]$ResolvedRoot = (Resolve-Path -LiteralPath $ProjectRoot -ErrorAction Stop).Path
[string]$RuntimeDirectory = Join-Path -Path $ResolvedRoot -ChildPath '.runtime'
[string]$BackendDirectory = Join-Path -Path $ResolvedRoot -ChildPath 'backend'
[string]$FrontendDirectory = Join-Path -Path $ResolvedRoot -ChildPath 'frontend'
[string]$BackendPython = Join-Path -Path $BackendDirectory -ChildPath '.venv\Scripts\python.exe'
[string]$NodeVersion = 'v22.22.0'
[string]$NodeArchive = "node-$NodeVersion-win-x64.zip"
[string]$UvVersion = '0.12.10'
[string]$UvArchive = 'uv-x86_64-pc-windows-msvc.zip'
# gyan.dev is the Windows build ffmpeg.org's own download page recommends. Unlike
# uv/Node this is a rolling "current stable" link, not a pinned version -- accepted
# deliberately: ffmpeg isn't locked for reproducibility by uv.lock/package-lock.json
# the way Python/Node are, it only needs to work and match its own just-published
# checksum, so there is nothing gained by also pinning an exact version number here.
[string]$FfmpegArchive = 'ffmpeg-release-essentials.zip'

# --------------------------------------------------------------------------
# Minimal EN/ZH strings.  This stage runs before backend/.venv is guaranteed to
# exist, so it cannot call into launcher_i18n (that is exactly why the former
# "preflight_*" keys were dropped there in v0.7.5).  Keep this table small and
# free of shell metacharacters.
# --------------------------------------------------------------------------
[string]$script:Lang = 'en'
if ($env:AKASHA_LANG -and $env:AKASHA_LANG.Trim().ToLowerInvariant().StartsWith('zh')) {
    $script:Lang = 'zh'
} elseif (-not $env:AKASHA_LANG) {
    try {
        if ((Get-Culture).Name.ToLowerInvariant().StartsWith('zh')) { $script:Lang = 'zh' }
    } catch {
        # Culture lookup is best-effort; English remains the safe default.
    }
}

[hashtable]$script:Strings = @{
    detected      = @{ en = 'Detected Windows {0} on a 64-bit operating system.'; zh = '检测到 Windows {0}，64 位操作系统。' }
    venvReady     = @{ en = 'Backend virtual environment is ready.';              zh = '后端虚拟环境已就绪。' }
    installPython = @{ en = 'Installing isolated Python 3.12 runtime.';           zh = '正在安装项目隔离的 Python 3.12 运行时。' }
    syncBackend   = @{ en = 'Creating backend virtual environment and synchronizing locked dependencies.'; zh = '正在创建后端虚拟环境并同步锁定的依赖。' }
    frontendReady = @{ en = 'Frontend dependencies are ready.';                   zh = '前端依赖已就绪。' }
    installNode   = @{ en = 'Installing locked frontend dependencies.';           zh = '正在安装锁定的前端依赖。' }
    nodeFrom      = @{ en = 'Using Node from {0}';                                zh = '使用的 Node 位于 {0}' }
    ffmpegFrom    = @{ en = 'Using ffmpeg from {0}';                               zh = '使用的 ffmpeg 位于 {0}' }
    chromiumReady = @{ en = 'Project-local Playwright Chromium is ready.';        zh = '项目内的 Playwright Chromium 已就绪。' }
    installBrowser= @{ en = 'Installing project-local Playwright Chromium.';      zh = '正在安装项目内的 Playwright Chromium。' }
    downloading   = @{ en = 'Downloading {0}';                                    zh = '正在下载 {0}' }
    done          = @{ en = 'Preflight completed successfully.';                  zh = '环境预检已全部完成。' }
    failed        = @{ en = 'FAILED: {0}';                                        zh = '失败: {0}' }
    badOs         = @{ en = 'Unsupported operating system. Akasha-RAG supports Windows only; Linux and macOS are not supported.'; zh = '不支持的操作系统。Akasha-RAG 仅支持 Windows，不支持 Linux 与 macOS。' }
    oldWindows    = @{ en = "Unsupported operating system: Windows {0}. Akasha-RAG's one-click setup requires Windows 10 or newer; on Windows 7/8/8.1 use the manual installation steps in the README."; zh = '不支持的操作系统: Windows {0}。一键安装需要 Windows 10 及以上；Windows 7/8/8.1 请按 README 手动安装。' }
    badArch       = @{ en = 'Unsupported architecture: 64-bit Windows is required.'; zh = '不支持的架构: 需要 64 位 Windows。' }
    armWarning    = @{ en = 'Detected ARM64 Windows. The downloaded tools are x64 builds running under emulation -- this is expected to work but has not been fully verified.'; zh = '检测到 ARM64 Windows。下载的工具是 x64 版本，将通过模拟层运行——预期可用，但未经完整验证。' }
    serverCore    = @{ en = 'Unsupported edition: Windows Server Core. Playwright Chromium has documented missing-DLL failures on Server Core (it lacks the Desktop Experience component). Install the Desktop Experience feature, or use the manual installation steps in the README.'; zh = '不支持的版本: Windows Server Core。Playwright Chromium 在 Server Core 上已知会因缺少 Desktop Experience 组件而报错。请安装 Desktop Experience 功能，或按 README 手动安装。' }
    apiKeyMissing = @{ en = 'API key not configured yet -- the app will still start. You will be prompted to enter it in Settings the first time you need it.'; zh = '还没有配置 API Key，应用仍会正常启动；等你第一次真正需要用到它时（入库或对话），会在设置里提示你填写。' }
}

function Get-Text {
    param(
        [Parameter(Mandatory = $true)][string]$Key,
        [Parameter(ValueFromRemainingArguments = $true)][object[]]$Arguments
    )
    [string]$template = $script:Strings[$Key][$script:Lang]
    if ([string]::IsNullOrEmpty($template)) { $template = $script:Strings[$Key]['en'] }
    if ($Arguments) { return ($template -f $Arguments) }
    return $template
}

function Write-Stage {
    param([Parameter(Mandatory = $true)][string]$Message)
    Write-Host "[BOOTSTRAP] $Message" -ForegroundColor Cyan
}

function Assert-LastExitCode {
    param([Parameter(Mandatory = $true)][string]$Operation)
    if ($LASTEXITCODE -ne 0) {
        throw "$Operation failed with exit code $LASTEXITCODE."
    }
}

function Get-Sha256 {
    param([Parameter(Mandatory = $true)][string]$Path)
    $stream = [System.IO.File]::OpenRead($Path)
    try {
        $sha256 = [System.Security.Cryptography.SHA256]::Create()
        try {
            return ([System.BitConverter]::ToString($sha256.ComputeHash($stream))).Replace('-', '').ToLowerInvariant()
        } finally {
            $sha256.Dispose()
        }
    } finally {
        $stream.Dispose()
    }
}

function Invoke-Download {
    param(
        [Parameter(Mandatory = $true)][string]$Uri,
        [Parameter(Mandatory = $true)][string]$OutputPath
    )
    Write-Stage (Get-Text 'downloading' $Uri)
    # Windows PowerShell 5.1 renders a progress bar per chunk, which dominates
    # Invoke-WebRequest's runtime -- a ~30 MB archive can take minutes instead of
    # seconds.  Suppress it for the duration of the transfer only.
    $previousProgress = $ProgressPreference
    $ProgressPreference = 'SilentlyContinue'
    try {
        Invoke-WebRequest -Uri $Uri -OutFile $OutputPath -UseBasicParsing -ErrorAction Stop
    } finally {
        $ProgressPreference = $previousProgress
    }
}

function Assert-Sha256 {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$ExpectedHash
    )
    [string]$actualHash = Get-Sha256 -Path $Path
    if ($actualHash -ne $ExpectedHash.ToLowerInvariant()) {
        throw "SHA-256 verification failed for $Path."
    }
}

# Pure comparison -- no environment reads.  Kept separate from Test-SupportedWindows
# so a fabricated [Version] can be passed in during manual verification (Windows
# 7/8/8.1 rejected, Windows 10 / Server 2016+ accepted) without needing a matching
# real OS to test against.  Keep the floor in sync with launcher.py's
# unsupported_platform_message().
function Test-WindowsVersionSupported {
    param([Parameter(Mandatory = $true)][Version]$Version)
    # Floor is Windows 10, not Windows 8.  The runtimes this script installs
    # (CPython 3.12, Node 22) do not support Windows 7/8/8.1, so accepting them
    # here only trades a clear refusal for a confusing mid-download failure.
    # This also covers Windows Server 2016 and newer without a separate branch:
    # every Server release since 2016 reports Major=10 too (2016=14393,
    # 2019=17763, 2022=20348, 2025=26100 -- the last one is even built on
    # Windows 11 24H2).
    return $Version.Major -ge 10
}

# [System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture is
# manifest-independent (unlike some legacy WOW64 checks) and works on both
# Windows PowerShell 5.1 and PowerShell 7.  The env-var read is a best-effort
# fallback only.
function Get-WindowsArchitecture {
    try {
        return [System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture.ToString()
    } catch {
        [string]$arch = $env:PROCESSOR_ARCHITEW6432
        if ([string]::IsNullOrWhiteSpace($arch)) { $arch = $env:PROCESSOR_ARCHITECTURE }
        return $arch
    }
}

# Server Core ships without Desktop Experience, and Playwright Chromium has
# documented missing-DLL failures there (e.g. api-ms-win-core-winrt-error-l1-1-0.dll)
# that no amount of downloading fixes.  This registry value is the documented,
# reliable way to detect it; every non-Server edition simply lacks the key, which
# reads as "not Server Core" rather than throwing.
function Test-WindowsServerCore {
    try {
        [int]$value = Get-ItemPropertyValue `
            -Path 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Server\ServerLevels' `
            -Name 'ServerCore' -ErrorAction Stop
        return $value -eq 1
    } catch {
        return $false
    }
}

function Test-SupportedWindows {
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
        throw (Get-Text 'badOs')
    }
    [Version]$version = [Environment]::OSVersion.Version
    if (-not (Test-WindowsVersionSupported -Version $version)) {
        throw (Get-Text 'oldWindows' $version)
    }
    if (-not [Environment]::Is64BitOperatingSystem) {
        throw (Get-Text 'badArch')
    }
    if (Test-WindowsServerCore) {
        # Hard block, not a warning: continuing would only fail confusingly later,
        # deep inside a Chromium launch, with an unreadable missing-DLL error --
        # the same principle as rejecting Windows 8.1 up front instead of letting
        # the Python/Node install fail midway.
        throw (Get-Text 'serverCore')
    }
    if ((Get-WindowsArchitecture) -eq 'Arm64') {
        # Warning, not a block: x64-under-emulation is expected to work on modern
        # Windows-on-ARM, just not verified by this project -- unlike Server Core,
        # there's no known hard failure to pre-empt.
        Write-Host "[BOOTSTRAP] $(Get-Text 'armWarning')" -ForegroundColor Yellow
    }
    Write-Stage (Get-Text 'detected' $version)
}

function Get-UvCommand {
    $systemUv = Get-Command -Name 'uv.exe' -ErrorAction SilentlyContinue
    if ($null -eq $systemUv) {
        $systemUv = Get-Command -Name 'uv' -ErrorAction SilentlyContinue
    }
    if ($null -ne $systemUv) {
        [string]$reportedVersion = (& $systemUv.Source --version).Trim()
        if ($reportedVersion -match '^uv\s+(?<version>\d+\.\d+\.\d+)') {
            if ([Version]$Matches.version -ge [Version]$UvVersion) {
                return $systemUv.Source
            }
        }
        Write-Stage "System uv is missing or older than $UvVersion; using the project-local runtime."
    }

    [string]$uvDirectory = Join-Path -Path $RuntimeDirectory -ChildPath "uv-$UvVersion"
    [string]$uvExecutable = Join-Path -Path $uvDirectory -ChildPath 'uv.exe'
    if (Test-Path -LiteralPath $uvExecutable -PathType Leaf) {
        return $uvExecutable
    }

    New-Item -ItemType Directory -Path $uvDirectory -Force | Out-Null
    [string]$downloadsDirectory = Join-Path -Path $RuntimeDirectory -ChildPath 'downloads'
    New-Item -ItemType Directory -Path $downloadsDirectory -Force | Out-Null
    [string]$archivePath = Join-Path -Path $downloadsDirectory -ChildPath $UvArchive
    # The checksum is ALWAYS fetched from the official release host -- see the
    # SECURITY INVARIANT at the top of this file.  Only the archive itself may
    # come from a mirror.
    [string]$officialBase = "https://github.com/astral-sh/uv/releases/download/$UvVersion"
    [string]$releaseBase = if ([string]::IsNullOrWhiteSpace($env:AKASHA_RUNTIME_MIRROR)) {
        $officialBase
    } else {
        "$($env:AKASHA_RUNTIME_MIRROR.TrimEnd('/'))/uv/$UvVersion"
    }
    [string]$checksumPath = "$archivePath.sha256"
    Invoke-Download -Uri "$officialBase/$UvArchive.sha256" -OutputPath $checksumPath
    [string]$expectedHash = ((Get-Content -LiteralPath $checksumPath -Raw -Encoding utf8).Trim() -split '\s+')[0]
    if ($expectedHash -notmatch '^[a-fA-F0-9]{64}$') {
        throw "The uv checksum file is invalid."
    }
    Invoke-Download -Uri "$releaseBase/$UvArchive" -OutputPath $archivePath
    Assert-Sha256 -Path $archivePath -ExpectedHash $expectedHash
    Expand-Archive -LiteralPath $archivePath -DestinationPath $uvDirectory -Force
    $found = Get-ChildItem -LiteralPath $uvDirectory -Recurse -Filter 'uv.exe' -File | Select-Object -First 1
    if ($null -eq $found) {
        throw "The verified uv archive did not contain uv.exe."
    }
    if ($found.FullName -ne $uvExecutable) {
        Copy-Item -LiteralPath $found.FullName -Destination $uvExecutable -Force
    }
    return $uvExecutable
}

function Get-NodeTools {
    $nodeCommand = Get-Command -Name 'node.exe' -ErrorAction SilentlyContinue
    if ($null -eq $nodeCommand) {
        $nodeCommand = Get-Command -Name 'node' -ErrorAction SilentlyContinue
    }
    $npmCommand = Get-Command -Name 'npm.cmd' -ErrorAction SilentlyContinue
    if ($null -eq $npmCommand) {
        $npmCommand = Get-Command -Name 'npm' -ErrorAction SilentlyContinue
    }
    if ($null -ne $nodeCommand -and $null -ne $npmCommand) {
        [string]$reportedVersion = (& $nodeCommand.Source --version).Trim()
        if ($reportedVersion -match '^v(?<major>\d+)\.') {
            if ([int]$Matches.major -ge 22) {
                return [PSCustomObject]@{ Node = $nodeCommand.Source; Npm = $npmCommand.Source }
            }
        }
    }

    [string]$nodeDirectory = Join-Path -Path $RuntimeDirectory -ChildPath "node-$NodeVersion-win-x64"
    [string]$nodeExecutable = Join-Path -Path $nodeDirectory -ChildPath 'node.exe'
    [string]$npmExecutable = Join-Path -Path $nodeDirectory -ChildPath 'npm.cmd'
    if (-not (Test-Path -LiteralPath $nodeExecutable -PathType Leaf) -or -not (Test-Path -LiteralPath $npmExecutable -PathType Leaf)) {
        [string]$downloadsDirectory = Join-Path -Path $RuntimeDirectory -ChildPath 'downloads'
        New-Item -ItemType Directory -Path $downloadsDirectory -Force | Out-Null
        [string]$archivePath = Join-Path -Path $downloadsDirectory -ChildPath $NodeArchive
        # Checksum ALWAYS from nodejs.org -- see the SECURITY INVARIANT at the
        # top of this file.  Reading it from the published SHASUMS256.txt rather
        # than hardcoding a hash also means bumping $NodeVersion cannot silently
        # leave a stale expected hash behind.
        [string]$officialBase = 'https://nodejs.org/dist'
        [string]$nodeBase = if ([string]::IsNullOrWhiteSpace($env:AKASHA_RUNTIME_MIRROR)) {
            $officialBase
        } else {
            "$($env:AKASHA_RUNTIME_MIRROR.TrimEnd('/'))/node"
        }
        [string]$shasumsPath = Join-Path -Path $downloadsDirectory -ChildPath "SHASUMS256-$NodeVersion.txt"
        Invoke-Download -Uri "$officialBase/$NodeVersion/SHASUMS256.txt" -OutputPath $shasumsPath
        [string]$expectedNodeHash = ''
        foreach ($line in (Get-Content -LiteralPath $shasumsPath -Encoding utf8)) {
            [string[]]$fields = $line.Trim() -split '\s+'
            if ($fields.Count -ge 2 -and $fields[-1] -eq $NodeArchive) {
                $expectedNodeHash = $fields[0]
                break
            }
        }
        if ($expectedNodeHash -notmatch '^[a-fA-F0-9]{64}$') {
            throw "Could not find a valid SHA-256 entry for $NodeArchive in the official SHASUMS256.txt."
        }
        Invoke-Download -Uri "$nodeBase/$NodeVersion/$NodeArchive" -OutputPath $archivePath
        Assert-Sha256 -Path $archivePath -ExpectedHash $expectedNodeHash
        Expand-Archive -LiteralPath $archivePath -DestinationPath $RuntimeDirectory -Force
    }
    if (-not (Test-Path -LiteralPath $nodeExecutable -PathType Leaf) -or -not (Test-Path -LiteralPath $npmExecutable -PathType Leaf)) {
        throw "The verified Node archive did not provide node.exe and npm.cmd."
    }
    return [PSCustomObject]@{ Node = $nodeExecutable; Npm = $npmExecutable }
}

function Get-FfmpegTools {
    # media_service.py needs a general-purpose ffmpeg (mp3 encoding, arbitrary
    # input demuxing) plus ffprobe. Playwright's own bundled ffmpeg (installed
    # alongside Chromium for its screen-recording feature) is NOT a substitute --
    # verified by running it: it's a `--disable-everything` build with no mp3
    # encoder and no ffprobe binary at all, built only for its own webm/vp8 use.
    $systemFfmpeg = Get-Command -Name 'ffmpeg.exe' -ErrorAction SilentlyContinue
    if ($null -eq $systemFfmpeg) {
        $systemFfmpeg = Get-Command -Name 'ffmpeg' -ErrorAction SilentlyContinue
    }
    $systemFfprobe = Get-Command -Name 'ffprobe.exe' -ErrorAction SilentlyContinue
    if ($null -eq $systemFfprobe) {
        $systemFfprobe = Get-Command -Name 'ffprobe' -ErrorAction SilentlyContinue
    }
    if ($null -ne $systemFfmpeg -and $null -ne $systemFfprobe) {
        try {
            & $systemFfmpeg.Source '-version' | Out-Null
            if ($LASTEXITCODE -eq 0) {
                return [PSCustomObject]@{ Ffmpeg = $systemFfmpeg.Source; Ffprobe = $systemFfprobe.Source }
            }
        } catch {
            # Falls through to the project-local install below.
        }
    }

    [string]$ffmpegRoot = Join-Path -Path $RuntimeDirectory -ChildPath 'ffmpeg'
    [string]$ffmpegExecutable = ''
    [string]$ffprobeExecutable = ''
    $existing = if (Test-Path -LiteralPath $ffmpegRoot -PathType Container) {
        Get-ChildItem -LiteralPath $ffmpegRoot -Recurse -Filter 'ffmpeg.exe' -File | Select-Object -First 1
    } else {
        $null
    }
    if ($null -ne $existing) {
        [string]$probe = Join-Path -Path $existing.DirectoryName -ChildPath 'ffprobe.exe'
        if (Test-Path -LiteralPath $probe -PathType Leaf) {
            $ffmpegExecutable = $existing.FullName
            $ffprobeExecutable = $probe
        }
    }

    if ([string]::IsNullOrEmpty($ffmpegExecutable)) {
        New-Item -ItemType Directory -Path $ffmpegRoot -Force | Out-Null
        [string]$downloadsDirectory = Join-Path -Path $RuntimeDirectory -ChildPath 'downloads'
        New-Item -ItemType Directory -Path $downloadsDirectory -Force | Out-Null
        [string]$archivePath = Join-Path -Path $downloadsDirectory -ChildPath $FfmpegArchive
        # Checksum ALWAYS from gyan.dev -- see the SECURITY INVARIANT at the top of
        # this file. The mirror, if set, only substitutes where the zip bytes come
        # from, never where the expected hash comes from.
        [string]$officialBase = 'https://www.gyan.dev/ffmpeg/builds'
        [string]$ffmpegBase = if ([string]::IsNullOrWhiteSpace($env:AKASHA_RUNTIME_MIRROR)) {
            $officialBase
        } else {
            "$($env:AKASHA_RUNTIME_MIRROR.TrimEnd('/'))/ffmpeg"
        }
        [string]$checksumPath = "$archivePath.sha256"
        Invoke-Download -Uri "$officialBase/$FfmpegArchive.sha256" -OutputPath $checksumPath
        [string]$expectedHash = ((Get-Content -LiteralPath $checksumPath -Raw -Encoding utf8).Trim() -split '\s+')[0]
        if ($expectedHash -notmatch '^[a-fA-F0-9]{64}$') {
            throw "The ffmpeg checksum file is invalid."
        }
        Invoke-Download -Uri "$ffmpegBase/$FfmpegArchive" -OutputPath $archivePath
        Assert-Sha256 -Path $archivePath -ExpectedHash $expectedHash
        # The essentials build extracts into an unpredictable versioned folder
        # (e.g. ffmpeg-8.0-essentials_build\bin\ffmpeg.exe) -- locate it
        # recursively rather than assume a fixed path, same approach as Get-UvCommand.
        Expand-Archive -LiteralPath $archivePath -DestinationPath $ffmpegRoot -Force
        $found = Get-ChildItem -LiteralPath $ffmpegRoot -Recurse -Filter 'ffmpeg.exe' -File | Select-Object -First 1
        if ($null -eq $found) {
            throw "The verified ffmpeg archive did not contain ffmpeg.exe."
        }
        [string]$probe = Join-Path -Path $found.DirectoryName -ChildPath 'ffprobe.exe'
        if (-not (Test-Path -LiteralPath $probe -PathType Leaf)) {
            throw "The verified ffmpeg archive did not contain ffprobe.exe."
        }
        $ffmpegExecutable = $found.FullName
        $ffprobeExecutable = $probe
    }

    return [PSCustomObject]@{ Ffmpeg = $ffmpegExecutable; Ffprobe = $ffprobeExecutable }
}

function Sync-Backend {
    [string]$lockPath = Join-Path -Path $BackendDirectory -ChildPath 'uv.lock'
    [string]$venvDirectory = Join-Path -Path $BackendDirectory -ChildPath '.venv'
    [string]$stampPath = Join-Path -Path $venvDirectory -ChildPath '.akasha-lock.sha256'
    [string]$uv = Get-UvCommand
    # Verify the lock before allowing uv to touch an existing environment.  A stale
    # lock would otherwise let `uv sync --locked` remove the old venv before failing.
    & $uv lock --check --project $BackendDirectory
    Assert-LastExitCode -Operation 'Backend lockfile validation'
    [string]$lockHash = Get-Sha256 -Path $lockPath
    [bool]$needsSync = -not (Test-Path -LiteralPath $BackendPython -PathType Leaf)
    if (-not $needsSync -and -not (Test-Path -LiteralPath $stampPath -PathType Leaf)) {
        $needsSync = $true
    }
    if (-not $needsSync -and (Get-Content -LiteralPath $stampPath -Raw -Encoding utf8).Trim() -ne $lockHash) {
        $needsSync = $true
    }
    if (-not $needsSync) {
        Write-Stage (Get-Text 'venvReady')
        return
    }
    [string]$pythonDirectory = Join-Path -Path $RuntimeDirectory -ChildPath 'python'
    New-Item -ItemType Directory -Path $pythonDirectory -Force | Out-Null
    $env:UV_PYTHON_INSTALL_DIR = $pythonDirectory
    Write-Stage (Get-Text 'installPython')
    & $uv python install 3.12 --install-dir $pythonDirectory --no-registry
    Assert-LastExitCode -Operation 'Python 3.12 installation'
    Write-Stage (Get-Text 'syncBackend')
    & $uv sync --locked --project $BackendDirectory --python 3.12 --managed-python
    Assert-LastExitCode -Operation 'Backend dependency synchronization'
    if (-not (Test-Path -LiteralPath $BackendPython -PathType Leaf)) {
        throw "Backend virtual environment was not created: $BackendPython"
    }
    Set-Content -LiteralPath $stampPath -Value $lockHash -Encoding utf8
}

function Sync-Frontend {
    param([Parameter(Mandatory = $true)][PSCustomObject]$NodeTools)
    [string]$lockPath = Join-Path -Path $FrontendDirectory -ChildPath 'package-lock.json'
    [string]$nodeModules = Join-Path -Path $FrontendDirectory -ChildPath 'node_modules'
    [string]$stampPath = Join-Path -Path $nodeModules -ChildPath '.akasha-lock.sha256'
    [string]$lockHash = Get-Sha256 -Path $lockPath
    [bool]$needsInstall = -not (Test-Path -LiteralPath $nodeModules -PathType Container)
    if (-not $needsInstall -and -not (Test-Path -LiteralPath $stampPath -PathType Leaf)) {
        $needsInstall = $true
    }
    if (-not $needsInstall -and (Get-Content -LiteralPath $stampPath -Raw -Encoding utf8).Trim() -ne $lockHash) {
        $needsInstall = $true
    }
    if (-not $needsInstall) {
        & $NodeTools.Npm --prefix $FrontendDirectory ls --depth=0 | Out-Null
        if ($LASTEXITCODE -ne 0) {
            $needsInstall = $true
        }
    }
    if ($needsInstall) {
        Write-Stage (Get-Text 'installNode')
        & $NodeTools.Npm --prefix $FrontendDirectory ci --no-audit --no-fund
        Assert-LastExitCode -Operation 'Frontend dependency installation'
        Set-Content -LiteralPath $stampPath -Value $lockHash -Encoding utf8
    } else {
        Write-Stage (Get-Text 'frontendReady')
    }
}

try {
    Test-SupportedWindows
    New-Item -ItemType Directory -Path $RuntimeDirectory -Force | Out-Null
    Sync-Backend
    [PSCustomObject]$nodeTools = Get-NodeTools
    Sync-Frontend -NodeTools $nodeTools

    # launcher.py resolves the frontend toolchain through shutil.which("node"),
    # so a project-local Node would be invisible to it even though `npm ci` above
    # used it by absolute path.  Record the directory we actually selected; the
    # caller (start.bat) prepends it to PATH for the launcher process only.
    [string]$nodeDirectory = Split-Path -Parent -Path $nodeTools.Node
    Set-Content -LiteralPath (Join-Path -Path $RuntimeDirectory -ChildPath 'node-dir.txt') `
        -Value $nodeDirectory -Encoding ascii
    Write-Stage (Get-Text 'nodeFrom' $nodeDirectory)

    # media_service.py resolves ffmpeg itself (shutil.which, then a couple of
    # well-known fallback paths, then this state file) -- it owns that logic
    # directly, so unlike Node there is no need to also prepend PATH in start.bat.
    [PSCustomObject]$ffmpegTools = Get-FfmpegTools
    [string]$ffmpegDirectory = Split-Path -Parent -Path $ffmpegTools.Ffmpeg
    Set-Content -LiteralPath (Join-Path -Path $RuntimeDirectory -ChildPath 'ffmpeg-dir.txt') `
        -Value $ffmpegDirectory -Encoding ascii
    Write-Stage (Get-Text 'ffmpegFrom' $ffmpegDirectory)

    # Exit code 20 (app.core.startup_preflight.CONFIGURATION_REQUIRED_EXIT_CODE) means
    # "no API key yet" -- that is no longer a bootstrap failure: the app itself now
    # starts regardless and prompts for the key from Settings when it is actually
    # needed (ingest / chat). Any other non-zero exit code is a real setup problem
    # (e.g. .env.example missing) and still aborts the bootstrap.
    Push-Location -LiteralPath $BackendDirectory
    try {
        & $BackendPython -m app.core.startup_preflight --backend-dir $BackendDirectory
        if ($LASTEXITCODE -eq 20) {
            Write-Host "[BOOTSTRAP] $(Get-Text 'apiKeyMissing')" -ForegroundColor Yellow
        } else {
            Assert-LastExitCode -Operation 'API key configuration check'
        }
    } finally {
        Pop-Location
    }

    [string]$browserDirectory = Join-Path -Path $BackendDirectory -ChildPath 'app\storage\playwright_browsers'
    $chromium = if (Test-Path -LiteralPath $browserDirectory -PathType Container) {
        Get-ChildItem -LiteralPath $browserDirectory -Recurse -Filter 'chrome.exe' -File | Select-Object -First 1
    } else {
        $null
    }
    if ($null -eq $chromium) {
        $env:PLAYWRIGHT_BROWSERS_PATH = $browserDirectory
        Write-Stage (Get-Text 'installBrowser')
        & $BackendPython -m playwright install chromium
        Assert-LastExitCode -Operation 'Playwright Chromium installation'
    } else {
        Write-Stage (Get-Text 'chromiumReady')
    }
    Write-Stage (Get-Text 'done')
    exit 0
} catch {
    Write-Host "[BOOTSTRAP] $(Get-Text 'failed' $_.Exception.Message)" -ForegroundColor Red
    exit 1
}
