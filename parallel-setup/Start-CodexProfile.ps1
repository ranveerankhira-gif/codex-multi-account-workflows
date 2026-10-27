[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$')]
    [string]$Profile,

    [Parameter(Mandatory = $true)]
    [string]$ConfigPath,

    [switch]$PlanOnly
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Get-NormalizedAbsolutePath {
    param([Parameter(Mandatory = $true)][string]$Path)

    $isDriveAbsolute = $Path -match '^[A-Za-z]:[\\/]'
    $isUncAbsolute = $Path -match '^\\\\[^\\/]+[\\/][^\\/]+'
    if (-not ($isDriveAbsolute -or $isUncAbsolute)) {
        throw "Profile root must be an absolute path: $Path"
    }
    $full = [System.IO.Path]::GetFullPath($Path).TrimEnd([System.IO.Path]::DirectorySeparatorChar)
    $driveRoot = [System.IO.Path]::GetPathRoot($full).TrimEnd([System.IO.Path]::DirectorySeparatorChar)
    if ($full -eq $driveRoot) {
        throw "Profile root must not be a drive root: $Path"
    }
    return $full
}

$configFile = (Resolve-Path -LiteralPath $ConfigPath -ErrorAction Stop).Path
$config = Get-Content -Raw -LiteralPath $configFile | ConvertFrom-Json
$topLevelFields = @($config.PSObject.Properties.Name)
if (($topLevelFields | Sort-Object) -join ',' -ne 'profiles') {
    throw 'Configuration must contain only a profiles array.'
}
if (-not $config.profiles -or @($config.profiles).Count -lt 1) {
    throw 'Configuration must contain at least one profile.'
}

$normalized = @()
foreach ($item in @($config.profiles)) {
    $fields = @($item.PSObject.Properties.Name | Sort-Object)
    if (($fields -join ',') -ne 'name,root') {
        throw 'Each profile must contain only name and root.'
    }
    if ([string]::IsNullOrWhiteSpace([string]$item.name) -or [string]$item.name -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$') {
        throw "Invalid profile name: $($item.name)"
    }
    $normalized += [pscustomobject]@{
        name = [string]$item.name
        root = Get-NormalizedAbsolutePath -Path ([string]$item.root)
    }
}

$duplicateNames = $normalized | Group-Object { $_.name.ToLowerInvariant() } | Where-Object Count -gt 1
if ($duplicateNames) { throw 'Profile names must be unique.' }

for ($left = 0; $left -lt $normalized.Count; $left++) {
    for ($right = $left + 1; $right -lt $normalized.Count; $right++) {
        $a = $normalized[$left].root.TrimEnd('\') + '\'
        $b = $normalized[$right].root.TrimEnd('\') + '\'
        if ($a.StartsWith($b, [System.StringComparison]::OrdinalIgnoreCase) -or
            $b.StartsWith($a, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Profile roots must not overlap: $($normalized[$left].name), $($normalized[$right].name)"
        }
    }
}

$selected = @($normalized | Where-Object { $_.name -ieq $Profile })
if ($selected.Count -ne 1) { throw "Profile was not found: $Profile" }

$profileRoot = $selected[0].root
$codexHome = Join-Path $profileRoot 'codex-home'
$electronData = Join-Path $profileRoot 'electron-data'
$plan = [ordered]@{
    profile = $selected[0].name
    profile_root = $profileRoot
    codex_home = $codexHome
    electron_data = $electronData
    creates_directories = -not $PlanOnly
    launches_official_appx = -not $PlanOnly
}

if ($PlanOnly) {
    [pscustomobject]$plan | ConvertTo-Json -Depth 3
    exit 0
}

$mutexName = 'Local\CodexProfile-' + ($selected[0].name -replace '[^A-Za-z0-9._-]', '-')
$mutex = [System.Threading.Mutex]::new($false, $mutexName)
try {
    if (-not $mutex.WaitOne(0)) {
        throw "Another launch is already in progress for profile: $($selected[0].name)"
    }

    New-Item -ItemType Directory -Force -Path $profileRoot, $codexHome, $electronData | Out-Null
    foreach ($path in @($profileRoot, $codexHome, $electronData)) {
        $item = Get-Item -LiteralPath $path -Force
        if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Profile paths must not be reparse points: $path"
        }
    }

    $package = Get-AppxPackage -Name OpenAI.Codex -ErrorAction SilentlyContinue |
        Sort-Object Version -Descending |
        Select-Object -First 1
    if (-not $package) { throw 'The official OpenAI Codex AppX package was not found.' }

    $executable = Join-Path $package.InstallLocation 'app\ChatGPT.exe'
    if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) {
        throw "Codex executable was not found in the installed AppX package: $executable"
    }

    $env:CODEX_HOME = $codexHome
    $env:CODEX_ELECTRON_USER_DATA_PATH = $electronData
    Start-Process -FilePath $executable `
        -ArgumentList "--user-data-dir=`"$electronData`"" `
        -WorkingDirectory (Split-Path -Parent $executable) | Out-Null
} finally {
    if ($mutex) {
        try { $mutex.ReleaseMutex() } catch {}
        $mutex.Dispose()
    }
}

