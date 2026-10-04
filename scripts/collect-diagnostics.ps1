#Requires -Version 5.1
<#
.SYNOPSIS
    Collects RoiMcp diagnostics into a zip file (PRD section 19).

.DESCRIPTION
    Zips, from the exchange directory ($env:ROI_MCP_EXCHANGE_DIR or %LOCALAPPDATA%\RoiMcp):
        heartbeat.json, observer.log*, server.log*, observer.config.json
    plus a generated summary.json with:
        - name, size and mtime of every file in the exchange directory (backups\ is summarised per
          backup folder as file count and total bytes only; no save file names or contents);
        - schema / schema_version / observer_version read from heartbeat, static, state, history;
        - OS and PowerShell version;
        - whether the game process is running, with PID and start time.

    Saves and the backups\ folder are NEVER included. state.json, static.json and history.json are
    included only with -IncludeSnapshots. Files are read through shared read-only streams so the
    observer can keep writing. Prints the path of the zip.

.PARAMETER OutFile
    Zip path. Default: <exchange>\diagnostics-<yyyyMMdd-HHmmss>.zip. An existing file is never overwritten.

.PARAMETER IncludeSnapshots
    Also include state.json, static.json and history.json.
#>
[CmdletBinding()]
param(
    [string]$OutFile,
    [switch]$IncludeSnapshots
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot 'lib\RoiMcpCommon.psm1') -Force -DisableNameChecking

function Format-Utc {
    param([datetime]$Value)
    return $Value.ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ss.fffZ', [System.Globalization.CultureInfo]::InvariantCulture)
}

$exitCode = 1
$zip = $null
$zipStream = $null
$createdZip = $false
try {
    Add-Type -AssemblyName System.IO.Compression
    Add-Type -AssemblyName System.IO.Compression.FileSystem

    $ex = Get-RoiExchangeDir
    if (-not (Test-Path -LiteralPath $ex -PathType Container)) {
        throw "Exchange directory '$ex' does not exist. Has the observer or MCP server run yet?"
    }
    $backupsDir = [System.IO.Path]::Combine($ex, 'backups')

    if ([string]::IsNullOrWhiteSpace($OutFile)) {
        $stamp = [datetime]::Now.ToString('yyyyMMdd-HHmmss', [System.Globalization.CultureInfo]::InvariantCulture)
        $OutFile = [System.IO.Path]::Combine($ex, "diagnostics-$stamp.zip")
    }
    $OutFile = Get-RoiFullPath $OutFile
    if (Test-Path -LiteralPath $OutFile) { throw "Output file '$OutFile' already exists." }
    if (Test-RoiPathUnder -Child $OutFile -Parent $backupsDir) { throw 'Refusing to write the zip into the backups folder.' }

    # --- Inventory ---------------------------------------------------------------
    $inventory = New-Object System.Collections.Generic.List[object]
    $backupSummary = New-Object System.Collections.Generic.List[object]
    foreach ($item in (Get-ChildItem -LiteralPath $ex -Force)) {
        if ($item.PSIsContainer -and $item.Name -ieq 'backups') {
            foreach ($b in (Get-ChildItem -LiteralPath $item.FullName -Force)) {
                if ($b.PSIsContainer) {
                    $files = @(Get-ChildItem -LiteralPath $b.FullName -Recurse -Force -File)
                    $sum = 0L
                    foreach ($f in $files) { $sum += $f.Length }
                    $backupSummary.Add([ordered]@{
                            name                 = $b.Name
                            file_count           = $files.Count
                            total_bytes          = $sum
                            has_manifest         = (Test-Path -LiteralPath ([System.IO.Path]::Combine($b.FullName, 'manifest.json')))
                            modified_utc         = (Format-Utc $b.LastWriteTimeUtc)
                        })
                } else {
                    $backupSummary.Add([ordered]@{ name = $b.Name; size = $b.Length; modified_utc = (Format-Utc $b.LastWriteTimeUtc) })
                }
            }
            continue
        }
        if ($item.PSIsContainer) {
            foreach ($f in (Get-ChildItem -LiteralPath $item.FullName -Recurse -Force -File)) {
                $inventory.Add([ordered]@{
                        path         = (Get-RoiRelativePath -Base $ex -Path $f.FullName)
                        size         = $f.Length
                        modified_utc = (Format-Utc $f.LastWriteTimeUtc)
                    })
            }
        } else {
            $inventory.Add([ordered]@{
                    path         = $item.Name
                    size         = $item.Length
                    modified_utc = (Format-Utc $item.LastWriteTimeUtc)
                })
        }
    }

    # --- Schema versions -------------------------------------------------------------
    $schemas = [ordered]@{}
    foreach ($name in @('heartbeat', 'static', 'state', 'history')) {
        $p = [System.IO.Path]::Combine($ex, "$name.json")
        if (-not (Test-Path -LiteralPath $p -PathType Leaf)) { $schemas[$name] = $null; continue }
        try {
            $obj = Read-RoiJsonFileShared -Path $p
            $info = [ordered]@{ schema = $null; schema_version = $null; observer_version = $null }
            foreach ($k in @('schema', 'schema_version', 'observer_version')) {
                if ($obj -and ($obj.PSObject.Properties.Name -contains $k)) { $info[$k] = [string]$obj.$k }
            }
            $schemas[$name] = $info
        } catch {
            $schemas[$name] = [ordered]@{ error = ('unreadable: ' + $_.Exception.Message) }
        }
    }

    # --- Game process -------------------------------------------------------------------
    $procInfo = @()
    foreach ($proc in (Get-RoiGameProcess)) {
        $start = $null
        try { $start = Format-Utc $proc.StartTime } catch { $start = $null }
        $procInfo += [ordered]@{ pid = $proc.Id; start_utc = $start }
    }

    # --- Files to include -----------------------------------------------------------
    $include = New-Object System.Collections.Generic.List[string]
    foreach ($f in (Get-ChildItem -LiteralPath $ex -Force -File)) {
        $n = $f.Name
        $take = $false
        if ($n -ieq 'heartbeat.json' -or $n -ieq 'observer.config.json') { $take = $true }
        elseif ($n -like 'observer.log*' -or $n -like 'server.log*') { $take = $true }
        elseif ($IncludeSnapshots -and ($n -ieq 'state.json' -or $n -ieq 'static.json' -or $n -ieq 'history.json')) { $take = $true }
        if ($n -like '*.tmp-*') { $take = $false }
        if ($take) { $include.Add($n) }
    }

    $summary = [ordered]@{
        schema            = 'roi-mcp/diagnostics-summary'
        schema_version    = '1.0.0'
        generated_utc     = (Format-Utc ([datetime]::UtcNow))
        exchange_dir      = $ex
        include_snapshots = [bool]$IncludeSnapshots
        os_version        = [Environment]::OSVersion.VersionString
        os_64bit          = [Environment]::Is64BitOperatingSystem
        powershell        = $PSVersionTable.PSVersion.ToString()
        game_running      = ($procInfo.Count -gt 0)
        game_processes    = $procInfo
        schema_versions   = $schemas
        files             = $inventory.ToArray()
        backups           = $backupSummary.ToArray()
        included_in_zip   = @()
        skipped           = @()
    }

    # --- Write the zip ----------------------------------------------------------------
    $outParent = [System.IO.Path]::GetDirectoryName($OutFile)
    if ($outParent) { [void][System.IO.Directory]::CreateDirectory($outParent) }
    $zipStream = [System.IO.FileStream]::new($OutFile, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::None)
    $createdZip = $true
    $zip = New-Object System.IO.Compression.ZipArchive($zipStream, [System.IO.Compression.ZipArchiveMode]::Create, $false)
    $skipped = New-Object System.Collections.Generic.List[string]
    $added = New-Object System.Collections.Generic.List[string]
    foreach ($n in $include) {
        try {
            $bytes = Read-RoiFileBytesShared -Path ([System.IO.Path]::Combine($ex, $n))
        } catch {
            $skipped.Add("$n ($($_.Exception.Message))")
            continue
        }
        $entry = $zip.CreateEntry($n, [System.IO.Compression.CompressionLevel]::Optimal)
        $es = $entry.Open()
        try { $es.Write($bytes, 0, $bytes.Length) } finally { $es.Dispose() }
        $added.Add($n)
    }
    $added.Add('summary.json')
    $summary.skipped = $skipped.ToArray()
    $summary.included_in_zip = $added.ToArray()
    $json = $summary | ConvertTo-Json -Depth 8
    $sbytes = (New-Object System.Text.UTF8Encoding($false)).GetBytes($json)
    $entry = $zip.CreateEntry('summary.json', [System.IO.Compression.CompressionLevel]::Optimal)
    $es = $entry.Open()
    try { $es.Write($sbytes, 0, $sbytes.Length) } finally { $es.Dispose() }
    $zip.Dispose(); $zip = $null
    $zipStream.Dispose(); $zipStream = $null

    foreach ($s in $skipped) { Write-Host "WARNING: skipped $s" }
    Write-Host ("Included: {0}" -f ($added.ToArray() -join ', '))
    Write-Host "Diagnostics written to: $OutFile"
    $exitCode = 0
} catch {
    Write-Host "ERROR: $($_.Exception.Message)"
    if ($zip) { try { $zip.Dispose() } catch { } }
    if ($zipStream) { try { $zipStream.Dispose() } catch { } }
    if ($createdZip -and $OutFile -and (Test-Path -LiteralPath $OutFile)) {
        try { Remove-Item -LiteralPath $OutFile -Force } catch { }
    }
    $exitCode = 1
}

exit $exitCode
