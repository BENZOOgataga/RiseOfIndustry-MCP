#Requires -Version 5.1
<#
.SYNOPSIS
    Ranks, extracts or builds local fixtures from save COPIES (PRD 6, 18, 21 T-10, 22 E3/E7).

.DESCRIPTION
    Works only on copies produced by scripts\backup-saves.ps1 (or another folder of copies). It never
    reads %APPDATA%\RiseOfIndustry: a source folder at or below that path is refused before any file
    access.

    1. Picks the source folder: -SaveCopiesDir, or -FromBackup latest (newest
       <exchange>\backups\saves-* whose manifest.json says valid: true).
    2. Re-verifies the SHA-256 of every .sav in the source folder against manifest.json (sha256_copy).
       Any mismatch, invalid entry or missing file stops the script before anything is parsed.
    3. Copies the .sav files to <repo>\.local\save-copies\<timestamp>\ and verifies each working copy.
    4. Runs scripts\validation\save_tools.py (through uv) on the working copies only:
         rank    -> .local\save-copies\<timestamp>\rank.json
         extract -> .local\save-copies\<timestamp>\extract\<name>.json
         fixture -> .local\fixtures\<timestamp>\<name>\fixture.json + state-subset.json
    5. Records the SHA-256 of every source and working copy before and after parsing in
       .local\save-copies\<timestamp>\hashes.json and fails if any of them changed.

    Everything written stays below <repo>\.local\ (gitignored).

.PARAMETER SaveCopiesDir
    Existing folder of save copies (for example %LOCALAPPDATA%\RoiMcp\backups\saves-<timestamp>).

.PARAMETER FromBackup
    'latest': use the newest valid backup below <exchange>\backups (exchange = $env:ROI_MCP_EXCHANGE_DIR
    or %LOCALAPPDATA%\RoiMcp).

.PARAMETER Mode
    rank (default), extract or fixture.

.PARAMETER SaveName
    extract / fixture: only this .sav file name (default: every .sav).

.PARAMETER AllowNoManifest
    Accept a -SaveCopiesDir without manifest.json (for example research copies). Hashes are still
    recorded before and after parsing, but nothing can be verified against a backup manifest.

.EXAMPLE
    pwsh -File scripts\gen-fixtures-from-save-copy.ps1 -FromBackup latest -Mode rank

.EXAMPLE
    pwsh -File scripts\gen-fixtures-from-save-copy.ps1 -SaveCopiesDir "$env:LOCALAPPDATA\RoiMcp\backups\saves-20261003-141500" -Mode fixture -SaveName "MySave.sav"
#>
[CmdletBinding()]
param(
    [string]$SaveCopiesDir,
    [ValidateSet('latest')]
    [string]$FromBackup,
    [ValidateSet('rank', 'extract', 'fixture')]
    [string]$Mode = 'rank',
    [string]$SaveName,
    [switch]$AllowNoManifest
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot 'lib\RoiMcpCommon.psm1') -Force -DisableNameChecking

function Get-Prop {
    param($Object, [string]$Name)
    if ($null -eq $Object) { return $null }
    $p = $Object.PSObject.Properties[$Name]
    if ($null -eq $p) { return $null }
    return $p.Value
}

function Assert-NotLiveSaves {
    <# Pure path comparison against %APPDATA%\RiseOfIndustry; no file system access. #>
    param([string]$Path)
    $live = Get-RoiSavesDir
    if ((Test-RoiPathUnder -Child $Path -Parent $live) -or (Test-RoiPathUnder -Child $live -Parent $Path)) {
        throw "Refusing '$Path': it is (or contains) the live saves folder '$live'. Use a copy made by scripts\backup-saves.ps1."
    }
}

function Find-LatestValidBackup {
    $root = [System.IO.Path]::Combine((Get-RoiExchangeDir), 'backups')
    Assert-NotLiveSaves -Path $root
    if (-not (Test-Path -LiteralPath $root -PathType Container)) {
        throw "No backups folder at '$root'. Run scripts\backup-saves.ps1 first."
    }
    $dirs = @(Get-ChildItem -LiteralPath $root -Directory -Filter 'saves-*' | Sort-Object -Property Name -Descending)
    foreach ($d in $dirs) {
        $m = [System.IO.Path]::Combine($d.FullName, 'manifest.json')
        if (-not (Test-Path -LiteralPath $m -PathType Leaf)) { Write-Host "Skipping $($d.Name): no manifest.json"; continue }
        try { $manifest = Read-RoiJsonFileShared -Path $m } catch { Write-Host "Skipping $($d.Name): unreadable manifest"; continue }
        if ((Get-Prop $manifest 'valid') -eq $true) { return $d.FullName }
        Write-Host "Skipping $($d.Name): manifest says valid=false"
    }
    throw "No valid backup (manifest valid: true) below '$root'."
}

$repo = Get-RoiRepoRoot
$localRoot = [System.IO.Path]::Combine($repo, '.local')
$toolPath = [System.IO.Path]::Combine($repo, 'scripts', 'validation', 'save_tools.py')
$exitCode = 1

try {
    if ([string]::IsNullOrWhiteSpace($SaveCopiesDir) -eq [string]::IsNullOrWhiteSpace($FromBackup)) {
        throw 'Pass exactly one of -SaveCopiesDir <folder> or -FromBackup latest.'
    }
    if (-not [string]::IsNullOrWhiteSpace($SaveCopiesDir)) {
        $source = Get-RoiFullPath $SaveCopiesDir
        Assert-NotLiveSaves -Path $source
    } else {
        $source = Find-LatestValidBackup
    }
    Assert-NotLiveSaves -Path $source
    if (Test-RoiPathUnder -Child $source -Parent $localRoot) {
        Write-Host "Note: source folder is below .local; working copies are still made."
    }
    if (-not (Test-Path -LiteralPath $source -PathType Container)) { throw "Source folder '$source' does not exist." }
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { throw "'uv' was not found on PATH." }
    if (-not (Test-Path -LiteralPath $toolPath -PathType Leaf)) { throw "Missing $toolPath." }
    Write-Host "Source copies: $source"

    # ---- 1. Inventory and manifest verification (before any parsing) ----
    $manifestPath = [System.IO.Path]::Combine($source, 'manifest.json')
    $expected = @{}
    $haveManifest = Test-Path -LiteralPath $manifestPath -PathType Leaf
    if ($haveManifest) {
        $manifest = Read-RoiJsonFileShared -Path $manifestPath
        if ((Get-Prop $manifest 'valid') -ne $true) { throw "manifest.json in '$source' does not say valid: true." }
        foreach ($f in @(Get-Prop $manifest 'files')) {
            $rel = [string](Get-Prop $f 'path')
            if (-not $rel.EndsWith('.sav', [System.StringComparison]::OrdinalIgnoreCase)) { continue }
            if ((Get-Prop $f 'valid') -ne $true) { throw "manifest entry '$rel' is not valid." }
            $expected[$rel] = ([string](Get-Prop $f 'sha256_copy')).ToUpperInvariant()
        }
        $relSaves = @($expected.Keys | Sort-Object)
    } elseif ($AllowNoManifest) {
        Write-Host 'WARNING: no manifest.json; hashes are recorded but cannot be verified against a backup manifest.'
        $relSaves = @(Get-ChildItem -LiteralPath $source -File -Filter '*.sav' | Sort-Object -Property Name | ForEach-Object { $_.Name })
    } else {
        throw "No manifest.json in '$source'. Use a backup made by scripts\backup-saves.ps1, or pass -AllowNoManifest."
    }
    if ($relSaves.Count -eq 0) { throw "No .sav files in '$source'." }

    if (-not [string]::IsNullOrWhiteSpace($SaveName)) {
        if ($Mode -eq 'rank') { throw '-SaveName applies to -Mode extract or fixture only.' }
        $relSaves = @($relSaves | Where-Object { [System.IO.Path]::GetFileName($_) -ieq $SaveName })
        if ($relSaves.Count -eq 0) { throw "'$SaveName' is not among the save copies." }
    }

    $names = @{}
    foreach ($rel in $relSaves) {
        $n = [System.IO.Path]::GetFileName($rel)
        if ($names.ContainsKey($n.ToLowerInvariant())) { throw "Two copies share the file name '$n'." }
        $names[$n.ToLowerInvariant()] = $true
    }

    $records = New-Object System.Collections.Generic.List[object]
    foreach ($rel in $relSaves) {
        $src = [System.IO.Path]::Combine($source, $rel)
        if (-not (Test-Path -LiteralPath $src -PathType Leaf)) { throw "Missing copy '$rel' in '$source'." }
        $hash = Get-RoiFileSha256 -Path $src
        if ($haveManifest -and $hash -ne $expected[$rel]) {
            throw "SHA-256 of '$rel' ($hash) differs from manifest.json ($($expected[$rel])). Not parsing anything."
        }
        $records.Add([ordered]@{
                file                     = [System.IO.Path]::GetFileName($rel)
                source_relative_path     = $rel
                sha256_manifest          = $(if ($haveManifest) { $expected[$rel] } else { $null })
                sha256_source_before     = $hash
                sha256_working_before    = $null
                sha256_source_after      = $null
                sha256_working_after     = $null
                unchanged                = $null
            })
    }
    Write-Host ("Verified {0} copy(ies){1}." -f $records.Count, $(if ($haveManifest) { ' against manifest.json' } else { '' }))

    # ---- 2. Working copies below .local ----
    $stamp = [datetime]::Now.ToString('yyyyMMdd-HHmmss', [System.Globalization.CultureInfo]::InvariantCulture)
    $work = [System.IO.Path]::Combine($localRoot, 'save-copies', $stamp)
    if (Test-Path -LiteralPath $work) { throw "Working folder '$work' already exists. Retry in a second." }
    [void][System.IO.Directory]::CreateDirectory($work)
    foreach ($r in $records) {
        $src = [System.IO.Path]::Combine($source, $r.source_relative_path)
        $dst = [System.IO.Path]::Combine($work, $r.file)
        $copy = Copy-RoiFileShared -Source $src -Destination $dst
        $r.sha256_working_before = Get-RoiFileSha256 -Path $dst
        if ($copy.Sha256 -ne $r.sha256_source_before -or $r.sha256_working_before -ne $r.sha256_source_before) {
            throw "Working copy of '$($r.file)' does not match its source."
        }
    }
    $hashesPath = [System.IO.Path]::Combine($work, 'hashes.json')
    $hashDoc = [ordered]@{
        schema      = 'roi-mcp/save-copy-hashes'
        source_dir  = $source
        manifest    = $haveManifest
        working_dir = $work
        mode        = $Mode
        started_utc = [datetime]::UtcNow.ToString('o')
        ended_utc   = $null
        all_unchanged = $null
        files       = $records.ToArray()
    }
    Write-RoiJsonFile -Path $hashesPath -InputObject $hashDoc -Depth 6
    Write-Host "Working copies: $work"

    # ---- 3. Parse the working copies only ----
    $toolFailed = $false
    switch ($Mode) {
        'rank' {
            & uv run --quiet $toolPath rank $work --out ([System.IO.Path]::Combine($work, 'rank.json'))
            if ($LASTEXITCODE -ne 0) { $toolFailed = $true }
        }
        'extract' {
            foreach ($r in $records) {
                $out = [System.IO.Path]::Combine($work, 'extract', [System.IO.Path]::GetFileNameWithoutExtension($r.file) + '.json')
                & uv run --quiet $toolPath extract ([System.IO.Path]::Combine($work, $r.file)) --out $out
                if ($LASTEXITCODE -ne 0) { $toolFailed = $true }
            }
        }
        'fixture' {
            foreach ($r in $records) {
                $out = [System.IO.Path]::Combine($localRoot, 'fixtures', $stamp, [System.IO.Path]::GetFileNameWithoutExtension($r.file))
                & uv run --quiet $toolPath fixture ([System.IO.Path]::Combine($work, $r.file)) --out $out
                if ($LASTEXITCODE -ne 0) { $toolFailed = $true }
            }
        }
    }

    # ---- 4. Hashes after parsing ----
    $allUnchanged = $true
    foreach ($r in $records) {
        $r.sha256_source_after = Get-RoiFileSha256 -Path ([System.IO.Path]::Combine($source, $r.source_relative_path))
        $r.sha256_working_after = Get-RoiFileSha256 -Path ([System.IO.Path]::Combine($work, $r.file))
        $r.unchanged = ($r.sha256_source_after -eq $r.sha256_source_before) -and ($r.sha256_working_after -eq $r.sha256_working_before)
        if (-not $r.unchanged) { $allUnchanged = $false; Write-Host "ERROR: '$($r.file)' changed during parsing." }
    }
    $hashDoc.ended_utc = [datetime]::UtcNow.ToString('o')
    $hashDoc.all_unchanged = $allUnchanged
    $hashDoc.files = $records.ToArray()
    Write-RoiJsonFile -Path $hashesPath -InputObject $hashDoc -Depth 6
    Write-Host "Hashes: $hashesPath (all unchanged: $allUnchanged)"

    if (-not $allUnchanged) { throw 'A save copy changed during parsing.' }
    if ($toolFailed) { throw 'save_tools.py reported an error (see above).' }
    $exitCode = 0
} catch {
    Write-Host "ERROR: $($_.Exception.Message)"
    $exitCode = 1
}
exit $exitCode
