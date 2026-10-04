#Requires -Version 5.1
<#
.SYNOPSIS
    Shared helpers for the RoiMcp PowerShell scripts.

.DESCRIPTION
    Exchange directory resolution, Rise of Industry install detection (read-only:
    registry value read, libraryfolders.vdf and appmanifest parsing), read-only
    SHA-256 hashing with sharing that never blocks the game, baseline constants
    (PRD section 3.1) and small path-safety helpers.

    Compatible with Windows PowerShell 5.1 and PowerShell 7+.
#>

Set-StrictMode -Version 2.0

# ---------------------------------------------------------------------------
# Baseline constants (PRD section 3.1)
# ---------------------------------------------------------------------------

$script:RoiBaseline = [pscustomobject]@{
    SteamAppId             = '671440'
    SteamBuildId           = '9064059'
    AssemblyCSharpSha256   = 'D62599EFD0CFCB9F343E7FF74AAC19533F572062B9CECA82E1D3911507D04803'
    AssemblyCSharpRelPath  = 'Rise of Industry_Data\Managed\Assembly-CSharp.dll'
    ProcessName            = 'Rise of Industry'
    ModName                = 'RoiMcpObserver'
    ModDllName             = 'RoiMcpObserver.dll'
    DefaultSteamPath       = 'C:\Program Files (x86)\Steam'
    ExchangeDirEnvVar      = 'ROI_MCP_EXCHANGE_DIR'
    ExchangeDirName        = 'RoiMcp'
    SavesDirName           = 'RiseOfIndustry'
}

function Get-RoiBaseline {
    <# Returns the verified baseline constants (PRD section 3.1). #>
    [CmdletBinding()]
    param()
    return $script:RoiBaseline
}

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

function Get-RoiFullPath {
    <# Normalises a path to an absolute path without a trailing separator (except for roots). #>
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$Path)
    $full = [System.IO.Path]::GetFullPath($Path)
    $root = [System.IO.Path]::GetPathRoot($full)
    if ($full.Length -gt $root.Length) {
        $full = $full.TrimEnd('\', '/')
    }
    return $full
}

function Test-RoiPathEqual {
    [CmdletBinding()]
    param([string]$A, [string]$B)
    return [string]::Equals((Get-RoiFullPath $A), (Get-RoiFullPath $B), [System.StringComparison]::OrdinalIgnoreCase)
}

function Test-RoiPathUnder {
    <# True when $Child is equal to $Parent or located below it (case-insensitive). #>
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$Child, [Parameter(Mandatory = $true)][string]$Parent)
    $c = Get-RoiFullPath $Child
    $p = Get-RoiFullPath $Parent
    if ([string]::Equals($c, $p, [System.StringComparison]::OrdinalIgnoreCase)) { return $true }
    $prefix = $p
    if (-not $prefix.EndsWith('\')) { $prefix = $prefix + '\' }
    return $c.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)
}

function Get-RoiRelativePath {
    <# Relative path of $Path below $Base (both absolute). #>
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$Base, [Parameter(Mandatory = $true)][string]$Path)
    $b = Get-RoiFullPath $Base
    $p = Get-RoiFullPath $Path
    if (-not $b.EndsWith('\')) { $b = $b + '\' }
    if (-not $p.StartsWith($b, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Path '$p' is not below '$b'."
    }
    return $p.Substring($b.Length)
}

function Get-RoiExchangeDir {
    <#
    .SYNOPSIS
        Exchange directory: $env:ROI_MCP_EXCHANGE_DIR if set, else %LOCALAPPDATA%\RoiMcp (PRD 11.1).
    #>
    [CmdletBinding()]
    param()
    $override = [Environment]::GetEnvironmentVariable($script:RoiBaseline.ExchangeDirEnvVar)
    if (-not [string]::IsNullOrWhiteSpace($override)) {
        return (Get-RoiFullPath $override)
    }
    $local = [Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)
    if ([string]::IsNullOrWhiteSpace($local)) { $local = $env:LOCALAPPDATA }
    if ([string]::IsNullOrWhiteSpace($local)) { throw 'Cannot resolve %LOCALAPPDATA%.' }
    return (Get-RoiFullPath ([System.IO.Path]::Combine($local, $script:RoiBaseline.ExchangeDirName)))
}

function Get-RoiSavesDir {
    <# Default game data/saves directory: %APPDATA%\RiseOfIndustry. Path only; never opened here. #>
    [CmdletBinding()]
    param()
    $roaming = [Environment]::GetFolderPath([Environment+SpecialFolder]::ApplicationData)
    if ([string]::IsNullOrWhiteSpace($roaming)) { $roaming = $env:APPDATA }
    if ([string]::IsNullOrWhiteSpace($roaming)) { throw 'Cannot resolve %APPDATA%.' }
    return (Get-RoiFullPath ([System.IO.Path]::Combine($roaming, $script:RoiBaseline.SavesDirName)))
}

function Get-RoiRepoRoot {
    <# Repository root (parent of scripts\). #>
    [CmdletBinding()]
    param()
    return (Get-RoiFullPath ([System.IO.Path]::Combine($PSScriptRoot, '..', '..')))
}

# ---------------------------------------------------------------------------
# Read-only file access and hashing
# ---------------------------------------------------------------------------

function Open-RoiSharedReadStream {
    <#
    .SYNOPSIS
        Opens a file read-only with FileShare.ReadWrite|Delete so that other processes
        (the game) can keep reading, writing, renaming or deleting it. Never locks.
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$Path)
    $share = [System.IO.FileShare]::ReadWrite -bor [System.IO.FileShare]::Delete
    return [System.IO.FileStream]::new($Path, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, $share, 1048576)
}

function ConvertTo-RoiHex {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][byte[]]$Bytes)
    return ([System.BitConverter]::ToString($Bytes) -replace '-', '')
}

function Get-RoiFileSha256 {
    <# Uppercase hex SHA-256 of a file, read through a shared read-only stream. #>
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$Path)
    $stream = Open-RoiSharedReadStream -Path $Path
    try {
        $sha = [System.Security.Cryptography.SHA256]::Create()
        try {
            return (ConvertTo-RoiHex ($sha.ComputeHash($stream)))
        } finally {
            $sha.Dispose()
        }
    } finally {
        $stream.Dispose()
    }
}

function Read-RoiFileBytesShared {
    <# Reads a whole file into memory through a shared read-only stream. #>
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$Path)
    $stream = Open-RoiSharedReadStream -Path $Path
    try {
        $ms = New-Object System.IO.MemoryStream
        try {
            $stream.CopyTo($ms)
            return , $ms.ToArray()
        } finally {
            $ms.Dispose()
        }
    } finally {
        $stream.Dispose()
    }
}

function Copy-RoiFileShared {
    <#
    .SYNOPSIS
        Copies Source to Destination. The source is opened read-only with
        FileShare.ReadWrite|Delete; the destination must not exist (CreateNew).
        Returns an object with the SHA-256 of the bytes read from the source and the byte count.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Destination
    )
    $src = Open-RoiSharedReadStream -Path $Source
    try {
        $dst = [System.IO.FileStream]::new($Destination, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None, 1048576)
        try {
            $sha = [System.Security.Cryptography.SHA256]::Create()
            try {
                $buffer = New-Object byte[] 1048576
                [long]$total = 0
                while ($true) {
                    $n = $src.Read($buffer, 0, $buffer.Length)
                    if ($n -le 0) { break }
                    [void]$sha.TransformBlock($buffer, 0, $n, $null, 0)
                    $dst.Write($buffer, 0, $n)
                    $total += $n
                }
                [void]$sha.TransformFinalBlock((New-Object byte[] 0), 0, 0)
                $dst.Flush($true)
                return [pscustomobject]@{
                    Sha256    = (ConvertTo-RoiHex $sha.Hash)
                    BytesRead = $total
                }
            } finally {
                $sha.Dispose()
            }
        } finally {
            $dst.Dispose()
        }
    } finally {
        $src.Dispose()
    }
}

function Write-RoiJsonFile {
    <# Writes an object as UTF-8 (no BOM) JSON. #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)]$InputObject,
        [int]$Depth = 8
    )
    $json = $InputObject | ConvertTo-Json -Depth $Depth
    [System.IO.File]::WriteAllText($Path, $json, (New-Object System.Text.UTF8Encoding($false)))
}

function Read-RoiJsonFileShared {
    <# Reads and parses a JSON file through a shared read-only stream. Throws on failure. #>
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$Path)
    $bytes = Read-RoiFileBytesShared -Path $Path
    $text = (New-Object System.Text.UTF8Encoding($false)).GetString($bytes)
    if ($text.Length -gt 0 -and $text[0] -eq [char]0xFEFF) { $text = $text.Substring(1) }
    return ($text | ConvertFrom-Json)
}

# ---------------------------------------------------------------------------
# Game process (read-only)
# ---------------------------------------------------------------------------

function Get-RoiGameProcess {
    <# Returns the Rise of Industry process objects, or an empty array. Read-only (Get-Process). #>
    [CmdletBinding()]
    param()
    $p = @(Get-Process -Name $script:RoiBaseline.ProcessName -ErrorAction SilentlyContinue)
    return , $p
}

# ---------------------------------------------------------------------------
# Steam / install detection (read-only)
# ---------------------------------------------------------------------------

function Get-RoiSteamPath {
    <# Steam root from HKCU:\Software\Valve\Steam SteamPath (read-only), else the default path. #>
    [CmdletBinding()]
    param()
    $candidate = $null
    try {
        $item = Get-ItemProperty -LiteralPath 'HKCU:\Software\Valve\Steam' -Name 'SteamPath' -ErrorAction Stop
        if ($item -and -not [string]::IsNullOrWhiteSpace($item.SteamPath)) {
            $candidate = ($item.SteamPath -replace '/', '\')
        }
    } catch {
        $candidate = $null
    }
    if ($candidate -and (Test-Path -LiteralPath $candidate -PathType Container)) {
        return (Get-RoiFullPath $candidate)
    }
    return $script:RoiBaseline.DefaultSteamPath
}

function ConvertFrom-RoiVdfString {
    [CmdletBinding()]
    param([string]$Value)
    return ($Value -replace '\\\\', '\')
}

function Get-RoiVdfValue {
    <# First value of a quoted key in VDF/ACF text, or $null. #>
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$Text, [Parameter(Mandatory = $true)][string]$Key)
    $m = [regex]::Match($Text, '"' + [regex]::Escape($Key) + '"\s+"((?:[^"\\]|\\.)*)"', [System.Text.RegularExpressions.RegexOptions]::IgnoreCase)
    if ($m.Success) { return (ConvertFrom-RoiVdfString $m.Groups[1].Value) }
    return $null
}

function Get-RoiSteamLibraries {
    <# Steam library roots: the Steam root plus every path in steamapps\libraryfolders.vdf. #>
    [CmdletBinding()]
    param([string]$SteamPath)
    if (-not $SteamPath) { $SteamPath = Get-RoiSteamPath }
    $libs = New-Object System.Collections.Generic.List[string]
    $libs.Add((Get-RoiFullPath $SteamPath))
    $vdf = [System.IO.Path]::Combine($SteamPath, 'steamapps', 'libraryfolders.vdf')
    if (Test-Path -LiteralPath $vdf -PathType Leaf) {
        $text = [System.Text.Encoding]::UTF8.GetString((Read-RoiFileBytesShared -Path $vdf))
        # New format: "path"  "D:\\SteamLibrary"
        foreach ($m in [regex]::Matches($text, '"path"\s+"((?:[^"\\]|\\.)*)"', [System.Text.RegularExpressions.RegexOptions]::IgnoreCase)) {
            $libs.Add((ConvertFrom-RoiVdfString $m.Groups[1].Value))
        }
        # Old format: "1"  "D:\\SteamLibrary"
        foreach ($m in [regex]::Matches($text, '"\d+"\s+"([A-Za-z]:(?:[^"\\]|\\.)*)"')) {
            $libs.Add((ConvertFrom-RoiVdfString $m.Groups[1].Value))
        }
    }
    $seen = @{}
    $result = New-Object System.Collections.Generic.List[string]
    foreach ($l in $libs) {
        try { $full = Get-RoiFullPath $l } catch { continue }
        $k = $full.ToLowerInvariant()
        if (-not $seen.ContainsKey($k)) { $seen[$k] = $true; $result.Add($full) }
    }
    return , $result.ToArray()
}

function Read-RoiAppManifest {
    <# Parses appmanifest_671440.acf. Returns AppId, BuildId, InstallDir, ManifestPath. #>
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$ManifestPath)
    $text = [System.Text.Encoding]::UTF8.GetString((Read-RoiFileBytesShared -Path $ManifestPath))
    return [pscustomobject]@{
        ManifestPath = (Get-RoiFullPath $ManifestPath)
        AppId        = (Get-RoiVdfValue -Text $text -Key 'appid')
        BuildId      = (Get-RoiVdfValue -Text $text -Key 'buildid')
        InstallDir   = (Get-RoiVdfValue -Text $text -Key 'installdir')
    }
}

function Find-RoiGameInstall {
    <#
    .SYNOPSIS
        Auto-detects the Rise of Industry install via Steam (read-only). Returns $null when not found.
        Result: GameDir, Manifest (Read-RoiAppManifest result), Library.
    #>
    [CmdletBinding()]
    param()
    $manifestName = 'appmanifest_' + $script:RoiBaseline.SteamAppId + '.acf'
    foreach ($lib in (Get-RoiSteamLibraries)) {
        $acf = [System.IO.Path]::Combine($lib, 'steamapps', $manifestName)
        if (-not (Test-Path -LiteralPath $acf -PathType Leaf)) { continue }
        $manifest = Read-RoiAppManifest -ManifestPath $acf
        if ([string]::IsNullOrWhiteSpace($manifest.InstallDir)) { continue }
        $gameDir = [System.IO.Path]::Combine($lib, 'steamapps', 'common', $manifest.InstallDir)
        if (Test-Path -LiteralPath $gameDir -PathType Container) {
            return [pscustomobject]@{
                GameDir  = (Get-RoiFullPath $gameDir)
                Manifest = $manifest
                Library  = $lib
                Source   = 'auto-detected'
            }
        }
    }
    return $null
}

function Resolve-RoiGameInstall {
    <#
    .SYNOPSIS
        Resolves the game install. With -GameDir the given folder is used (it must exist; there is
        NO fallback to auto-detection), and the manifest is looked up at <GameDir>\..\..\appmanifest_671440.acf.
        Without -GameDir, Find-RoiGameInstall is used. Throws when nothing usable is found.
    #>
    [CmdletBinding()]
    param([string]$GameDir)
    if (-not [string]::IsNullOrWhiteSpace($GameDir)) {
        if (-not (Test-Path -LiteralPath $GameDir -PathType Container)) {
            throw "GameDir '$GameDir' does not exist or is not a directory."
        }
        $full = Get-RoiFullPath $GameDir
        $manifest = $null
        $common = [System.IO.Path]::GetDirectoryName($full)
        if ($common) {
            $steamapps = [System.IO.Path]::GetDirectoryName($common)
            if ($steamapps) {
                $acf = [System.IO.Path]::Combine($steamapps, 'appmanifest_' + $script:RoiBaseline.SteamAppId + '.acf')
                if (Test-Path -LiteralPath $acf -PathType Leaf) {
                    $manifest = Read-RoiAppManifest -ManifestPath $acf
                }
            }
        }
        return [pscustomobject]@{
            GameDir  = $full
            Manifest = $manifest
            Library  = $null
            Source   = 'parameter'
        }
    }
    $found = Find-RoiGameInstall
    if (-not $found) {
        throw "Rise of Industry (Steam app $($script:RoiBaseline.SteamAppId)) was not found in any Steam library. Pass -GameDir."
    }
    return $found
}

function Get-RoiAssemblyCSharpPath {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$GameDir)
    return [System.IO.Path]::Combine($GameDir, $script:RoiBaseline.AssemblyCSharpRelPath)
}

function Get-RoiModDir {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$GameDir)
    return [System.IO.Path]::Combine($GameDir, 'Mods', $script:RoiBaseline.ModName)
}

function Get-RoiUnexpectedModFiles {
    <#
    .SYNOPSIS
        Lists entries in <GameDir>\Mods\RoiMcpObserver other than desc.json, code\ and
        code\RoiMcpObserver.dll (relative paths). Empty array when the folder only holds those.
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$ModDir)
    $allowed = @('desc.json', 'code', ('code\' + $script:RoiBaseline.ModDllName))
    $unexpected = New-Object System.Collections.Generic.List[string]
    if (-not (Test-Path -LiteralPath $ModDir -PathType Container)) { return , $unexpected.ToArray() }
    foreach ($item in (Get-ChildItem -LiteralPath $ModDir -Recurse -Force)) {
        $rel = Get-RoiRelativePath -Base $ModDir -Path $item.FullName
        $ok = $false
        foreach ($a in $allowed) {
            if ([string]::Equals($rel, $a, [System.StringComparison]::OrdinalIgnoreCase)) { $ok = $true; break }
        }
        if ($ok) {
            # 'code' must be a directory and the two files must be files.
            $isDir = $item.PSIsContainer
            if (([string]::Equals($rel, 'code', [System.StringComparison]::OrdinalIgnoreCase)) -ne $isDir) { $ok = $false }
        }
        if (-not $ok) { $unexpected.Add($rel) }
    }
    return , $unexpected.ToArray()
}

function Get-RoiDescJsonName {
    <#
    .SYNOPSIS
        Reads the "name" of a desc.json. Returns an object with Name and Parsed (bool).
        When JSON parsing fails, falls back to a regex search for a "name" property.
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$Path)
    $bytes = Read-RoiFileBytesShared -Path $Path
    $text = (New-Object System.Text.UTF8Encoding($false)).GetString($bytes)
    if ($text.Length -gt 0 -and $text[0] -eq [char]0xFEFF) { $text = $text.Substring(1) }
    try {
        $obj = $text | ConvertFrom-Json -ErrorAction Stop
        $name = $null
        if ($obj -and ($obj.PSObject.Properties.Name -contains 'name')) { $name = [string]$obj.name }
        return [pscustomobject]@{ Name = $name; Parsed = $true }
    } catch {
        $m = [regex]::Match($text, '"name"\s*:\s*"((?:[^"\\]|\\.)*)"', [System.Text.RegularExpressions.RegexOptions]::IgnoreCase)
        $name = $null
        if ($m.Success) { $name = $m.Groups[1].Value }
        return [pscustomobject]@{ Name = $name; Parsed = $false }
    }
}

Export-ModuleMember -Function @(
    'Get-RoiBaseline',
    'Get-RoiFullPath',
    'Test-RoiPathEqual',
    'Test-RoiPathUnder',
    'Get-RoiRelativePath',
    'Get-RoiExchangeDir',
    'Get-RoiSavesDir',
    'Get-RoiRepoRoot',
    'Open-RoiSharedReadStream',
    'ConvertTo-RoiHex',
    'Get-RoiFileSha256',
    'Read-RoiFileBytesShared',
    'Copy-RoiFileShared',
    'Write-RoiJsonFile',
    'Read-RoiJsonFileShared',
    'Get-RoiGameProcess',
    'Get-RoiSteamPath',
    'Get-RoiVdfValue',
    'Get-RoiSteamLibraries',
    'Read-RoiAppManifest',
    'Find-RoiGameInstall',
    'Resolve-RoiGameInstall',
    'Get-RoiAssemblyCSharpPath',
    'Get-RoiModDir',
    'Get-RoiUnexpectedModFiles',
    'Get-RoiDescJsonName'
)
