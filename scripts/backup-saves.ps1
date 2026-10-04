#Requires -Version 5.1
<#
.SYNOPSIS
    Copy-only, stability-checked backup of the Rise of Industry data/saves folder (PRD section 18).

.DESCRIPTION
    Copies every file below -SourceDir (default %APPDATA%\RiseOfIndustry), recursively and with
    relative paths preserved, to <DestRoot>\backups\saves-<yyyyMMdd-HHmmss>\ and writes manifest.json.

    Source files are only ever opened read-only with FileShare.ReadWrite|Delete, so the game can keep
    reading, writing, renaming or deleting them. Source files are never modified, locked, renamed,
    moved or deleted.

    Game not running: each file is copied while hashing the bytes read; valid when the SHA-256 of the
    copy equals the SHA-256 of the source bytes.

    Game running: per file, size, mtime and SHA-256 are recorded before the copy and again after it,
    and the copy is hashed. Valid only if before == after == copy.

    In both modes the file list is enumerated again at the end and the game process is checked again;
    a changed file list, or a game that started during a "not running" backup, makes the backup invalid.

    If any file is invalid or any read error occurs, the backup folder created by this run (and only
    that folder) is deleted, a message asks the user to return to the main menu or close the game and
    retry, and the script exits with code 1. On success it prints the folder and file count and exits 0.

.PARAMETER SourceDir
    Folder to back up. Default: %APPDATA%\RiseOfIndustry.

.PARAMETER DestRoot
    Root under which backups\saves-<timestamp>\ is created. Default: $env:ROI_MCP_EXCHANGE_DIR if set,
    else %LOCALAPPDATA%\RoiMcp.

.PARAMETER TestGameState
    TEST ONLY. 'Auto' (default) detects the game process with Get-Process. 'Running' / 'NotRunning'
    force the code path for the self-tests in scripts\tests. Do not use for real backups.

.PARAMETER TestHookAfterFirstHash
    TEST ONLY. Scriptblock invoked as & $hook <sourceFullPath> <relativePath> after the first hash of
    each source file (in running mode: after the "before" hash; in not-running mode: after the copy).
    Used by the self-tests to inject source instability. Never pass it for real backups.

.EXAMPLE
    pwsh -File scripts\backup-saves.ps1
#>
[CmdletBinding()]
param(
    [string]$SourceDir,
    [string]$DestRoot,
    [ValidateSet('Auto', 'Running', 'NotRunning')]
    [string]$TestGameState = 'Auto',
    [scriptblock]$TestHookAfterFirstHash
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot 'lib\RoiMcpCommon.psm1') -Force -DisableNameChecking

function Get-GameRunningState {
    param([string]$Mode)
    if ($Mode -eq 'Running') { return [pscustomobject]@{ Running = $true; Pids = @() } }
    if ($Mode -eq 'NotRunning') { return [pscustomobject]@{ Running = $false; Pids = @() } }
    $procs = Get-RoiGameProcess
    $pids = @()
    foreach ($p in $procs) { $pids += $p.Id }
    return [pscustomobject]@{ Running = ($procs.Count -gt 0); Pids = $pids }
}

function Get-SourceFileList {
    param([string]$Root)
    $list = New-Object System.Collections.Generic.List[string]
    foreach ($f in [System.IO.Directory]::EnumerateFiles($Root, '*', [System.IO.SearchOption]::AllDirectories)) {
        $list.Add((Get-RoiRelativePath -Base $Root -Path $f))
    }
    $arr = $list.ToArray()
    [System.Array]::Sort($arr, [System.StringComparer]::OrdinalIgnoreCase)
    return , $arr
}

function Get-SourceDirList {
    param([string]$Root)
    $list = New-Object System.Collections.Generic.List[string]
    foreach ($d in [System.IO.Directory]::EnumerateDirectories($Root, '*', [System.IO.SearchOption]::AllDirectories)) {
        $list.Add((Get-RoiRelativePath -Base $Root -Path $d))
    }
    return , $list.ToArray()
}

function Get-FileFacts {
    param([string]$Path)
    $fi = New-Object System.IO.FileInfo($Path)
    $fi.Refresh()
    if (-not $fi.Exists) { throw "File disappeared: $Path" }
    return [pscustomobject]@{
        Size  = [long]$fi.Length
        Mtime = $fi.LastWriteTimeUtc
    }
}

function Format-Utc {
    param([datetime]$Value)
    return $Value.ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ss.fffffffZ', [System.Globalization.CultureInfo]::InvariantCulture)
}

$backupDir = $null
$createdBackupDir = $false
$exitCode = 1

try {
    if ($TestGameState -ne 'Auto' -or $TestHookAfterFirstHash) {
        Write-Host "TEST MODE: TestGameState=$TestGameState, TestHookAfterFirstHash=$([bool]$TestHookAfterFirstHash). Not for real backups."
    }

    if ([string]::IsNullOrWhiteSpace($SourceDir)) { $SourceDir = Get-RoiSavesDir }
    if ([string]::IsNullOrWhiteSpace($DestRoot)) { $DestRoot = Get-RoiExchangeDir }
    $SourceDir = Get-RoiFullPath $SourceDir
    $DestRoot = Get-RoiFullPath $DestRoot

    if (-not (Test-Path -LiteralPath $SourceDir -PathType Container)) {
        throw "Source folder '$SourceDir' does not exist. Nothing was backed up."
    }
    if (Test-RoiPathUnder -Child $DestRoot -Parent $SourceDir) {
        throw "DestRoot '$DestRoot' is inside the source folder '$SourceDir'. Refusing (the backup would write into the source)."
    }
    if (Test-RoiPathUnder -Child $SourceDir -Parent $DestRoot) {
        throw "Source folder '$SourceDir' is inside DestRoot '$DestRoot'. Refusing."
    }

    $startUtc = [datetime]::UtcNow
    $stamp = [datetime]::Now.ToString('yyyyMMdd-HHmmss', [System.Globalization.CultureInfo]::InvariantCulture)
    $backupsRoot = [System.IO.Path]::Combine($DestRoot, 'backups')
    $candidate = [System.IO.Path]::Combine($backupsRoot, 'saves-' + $stamp)
    if (Test-Path -LiteralPath $candidate) {
        throw "Backup folder '$candidate' already exists. Wait a second and retry."
    }

    $gameAtStart = Get-GameRunningState -Mode $TestGameState
    $running = $gameAtStart.Running
    Write-Host ("Source:      {0}" -f $SourceDir)
    Write-Host ("Destination: {0}" -f $candidate)
    Write-Host ("Game running: {0}{1}" -f $running, $(if ($gameAtStart.Pids.Count -gt 0) { ' (PID ' + ($gameAtStart.Pids -join ', ') + ')' } else { '' }))

    $relFiles = Get-SourceFileList -Root $SourceDir
    $relDirs = Get-SourceDirList -Root $SourceDir

    [void][System.IO.Directory]::CreateDirectory($backupsRoot)
    # Create our own folder; fail if it appeared in the meantime (never reuse a folder we did not create).
    if (Test-Path -LiteralPath $candidate) { throw "Backup folder '$candidate' already exists. Retry." }
    [void][System.IO.Directory]::CreateDirectory($candidate)
    $backupDir = $candidate
    $createdBackupDir = $true

    foreach ($d in $relDirs) {
        [void][System.IO.Directory]::CreateDirectory([System.IO.Path]::Combine($backupDir, $d))
    }

    $entries = New-Object System.Collections.Generic.List[object]
    $problems = New-Object System.Collections.Generic.List[string]
    [long]$totalBytes = 0

    foreach ($rel in $relFiles) {
        $src = [System.IO.Path]::Combine($SourceDir, $rel)
        $dst = [System.IO.Path]::Combine($backupDir, $rel)
        $entry = [ordered]@{
            path                 = $rel
            size                 = $null
            source_mtime_utc     = $null
            sha256_source_before = $null
            sha256_source_after  = $null
            sha256_copy          = $null
            valid                = $false
        }
        try {
            [void][System.IO.Directory]::CreateDirectory([System.IO.Path]::GetDirectoryName($dst))
            if ($running) {
                $before = Get-FileFacts -Path $src
                $hashBefore = Get-RoiFileSha256 -Path $src
                $entry.size = $before.Size
                $entry.source_mtime_utc = Format-Utc $before.Mtime
                $entry.sha256_source_before = $hashBefore
                if ($TestHookAfterFirstHash) { & $TestHookAfterFirstHash $src $rel }

                $copy = Copy-RoiFileShared -Source $src -Destination $dst
                [System.IO.File]::SetLastWriteTimeUtc($dst, $before.Mtime)

                $after = Get-FileFacts -Path $src
                $hashAfter = Get-RoiFileSha256 -Path $src
                $hashCopy = Get-RoiFileSha256 -Path $dst
                $entry.sha256_source_after = $hashAfter
                $entry.sha256_copy = $hashCopy
                $entry.size_after = $after.Size
                $entry.source_mtime_after_utc = Format-Utc $after.Mtime
                $entry.copy_size = (New-Object System.IO.FileInfo($dst)).Length

                $entry.valid = (
                    ($hashBefore -eq $hashAfter) -and
                    ($hashBefore -eq $copy.Sha256) -and
                    ($hashBefore -eq $hashCopy) -and
                    ($before.Size -eq $after.Size) -and
                    ($before.Size -eq $copy.BytesRead) -and
                    ($before.Size -eq $entry.copy_size) -and
                    ($before.Mtime -eq $after.Mtime)
                )
            } else {
                $facts = Get-FileFacts -Path $src
                $entry.size = $facts.Size
                $entry.source_mtime_utc = Format-Utc $facts.Mtime
                $copy = Copy-RoiFileShared -Source $src -Destination $dst
                [System.IO.File]::SetLastWriteTimeUtc($dst, $facts.Mtime)
                $entry.sha256_source_before = $copy.Sha256
                if ($TestHookAfterFirstHash) { & $TestHookAfterFirstHash $src $rel }
                $hashCopy = Get-RoiFileSha256 -Path $dst
                $entry.sha256_copy = $hashCopy
                $entry.copy_size = (New-Object System.IO.FileInfo($dst)).Length
                $entry.valid = (($copy.Sha256 -eq $hashCopy) -and ($copy.BytesRead -eq $entry.copy_size))
            }
        } catch {
            $entry.valid = $false
            $entry.error = $_.Exception.Message
            $problems.Add("Read/copy error on '$rel': $($_.Exception.Message)")
        }
        if (-not $entry.valid -and -not $entry.Contains('error')) {
            $problems.Add("File changed or copy mismatch: '$rel'")
        }
        if ($entry.size) { $totalBytes += [long]$entry.size }
        $entries.Add([pscustomobject]$entry)
        if ($problems.Count -gt 0) { break }
    }

    if ($problems.Count -eq 0) {
        $relFilesAfter = Get-SourceFileList -Root $SourceDir
        if (($relFilesAfter -join "`n") -ne ($relFiles -join "`n")) {
            $problems.Add('The set of files in the source folder changed during the backup.')
        }
        $gameAtEnd = Get-GameRunningState -Mode $TestGameState
        if (-not $running -and $gameAtEnd.Running) {
            $problems.Add('The game started while the backup was running.')
        }
    }

    $endUtc = [datetime]::UtcNow
    $valid = ($problems.Count -eq 0)
    $manifest = [ordered]@{
        schema         = 'roi-mcp/save-backup-manifest'
        schema_version = '1.0.0'
        source_dir     = $SourceDir
        backup_dir     = $backupDir
        game_running   = $running
        game_pids      = $gameAtStart.Pids
        start_utc      = Format-Utc $startUtc
        end_utc        = Format-Utc $endUtc
        file_count     = $entries.Count
        total_bytes    = $totalBytes
        valid          = $valid
        files          = $entries.ToArray()
    }

    if (-not $valid) {
        foreach ($p in $problems) { Write-Host "INVALID: $p" }
        throw [System.InvalidOperationException]::new('BACKUP_UNSTABLE')
    }

    Write-RoiJsonFile -Path ([System.IO.Path]::Combine($backupDir, 'manifest.json')) -InputObject $manifest -Depth 6
    Write-Host ''
    if ($entries.Count -eq 0) { Write-Host 'WARNING: the source folder contains no files.' }
    Write-Host ("Backup OK: {0} file(s), {1} bytes" -f $entries.Count, $totalBytes)
    Write-Host ("Folder:    {0}" -f $backupDir)
    $exitCode = 0
} catch {
    $msg = $_.Exception.Message
    if ($createdBackupDir -and $backupDir -and (Test-Path -LiteralPath $backupDir)) {
        try {
            Remove-Item -LiteralPath $backupDir -Recurse -Force -ErrorAction Stop
            Write-Host "Discarded the incomplete backup folder: $backupDir"
        } catch {
            Write-Host "WARNING: could not delete the incomplete backup folder '$backupDir': $($_.Exception.Message). It is NOT a valid backup."
        }
    }
    if ($msg -ne 'BACKUP_UNSTABLE') { Write-Host "ERROR: $msg" }
    if ($createdBackupDir) {
        Write-Host 'The backup could not be verified as stable. No valid backup was produced.'
        Write-Host 'Return to the main menu (or close the game) and run this script again.'
    }
    $exitCode = 1
}

exit $exitCode
