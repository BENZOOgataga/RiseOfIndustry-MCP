#Requires -Version 5.1
<#
.SYNOPSIS
    Removes the RoiMcpObserver mod folder from a Rise of Industry installation (PRD section 20).

.DESCRIPTION
    Removes exactly <GameDir>\Mods\RoiMcpObserver\. The Mods\ folder itself and everything else in the
    installation are left alone. No PlayerPrefs/registry value and no save is touched.

    Refuses (exit 1) when that folder contains anything other than desc.json and
    code\RoiMcpObserver.dll, or when its desc.json is not named "RoiMcpObserver", unless -Force is given.

    Files are removed DLL first and desc.json last, so a failure never leaves a Mods subfolder holding
    files but no desc.json (which would disable all mods). If the folder cannot be removed completely
    the script says so and exits 1.

    Optional -RemoveExchangeDir also deletes the exchange directory ($env:ROI_MCP_EXCHANGE_DIR or
    %LOCALAPPDATA%\RoiMcp). Its backups\ subfolder (save backups made by backup-saves.ps1) is KEPT unless
    -IncludeBackups is also given.

.PARAMETER GameDir
    Game installation folder; auto-detected from Steam when omitted (same rules as install-observer.ps1).

.PARAMETER Force
    Remove Mods\RoiMcpObserver\ even if it contains unexpected files.

.PARAMETER RemoveExchangeDir
    Also delete the exchange directory contents (logs, snapshots, heartbeat, config), keeping backups\.

.PARAMETER IncludeBackups
    With -RemoveExchangeDir: also delete backups\ (your save backups) and the exchange directory itself.

.PARAMETER DryRun
    Prints what would be removed without deleting anything.
#>
[CmdletBinding()]
param(
    [string]$GameDir,
    [switch]$Force,
    [switch]$RemoveExchangeDir,
    [switch]$IncludeBackups,
    [switch]$DryRun
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot 'lib\RoiMcpCommon.psm1') -Force -DisableNameChecking

function Assert-SafeExchangeDir {
    param([string]$Dir, [string]$GameDirPath)
    $full = Get-RoiFullPath $Dir
    if ([string]::Equals($full, [System.IO.Path]::GetPathRoot($full), [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to delete '$full': it is a drive root."
    }
    $protected = New-Object System.Collections.Generic.List[string]
    foreach ($p in @(
            $env:USERPROFILE, $env:APPDATA, $env:LOCALAPPDATA, $env:SystemRoot, $env:ProgramFiles,
            ${env:ProgramFiles(x86)}, $env:ProgramData, $env:PUBLIC, [System.IO.Path]::GetTempPath(),
            [Environment]::GetFolderPath([Environment+SpecialFolder]::MyDocuments),
            [Environment]::GetFolderPath([Environment+SpecialFolder]::Desktop),
            (Get-RoiSavesDir), (Get-RoiRepoRoot), $GameDirPath)) {
        if (-not [string]::IsNullOrWhiteSpace($p)) { $protected.Add($p) }
    }
    foreach ($p in $protected) {
        # The exchange dir must not be (or contain) a protected folder ...
        if (Test-RoiPathUnder -Child $p -Parent $full) {
            throw "Refusing to delete '$full': it is or contains the protected folder '$p'."
        }
    }
    foreach ($p in @((Get-RoiSavesDir), $GameDirPath, (Get-RoiRepoRoot))) {
        # ... and must not be inside the saves folder, the game folder or the repository.
        if (-not [string]::IsNullOrWhiteSpace($p) -and (Test-RoiPathUnder -Child $full -Parent $p)) {
            throw "Refusing to delete '$full': it is inside '$p'."
        }
    }
}

function Remove-ModFolder {
    param([string]$ModDir)
    # Everything except desc.json first (deepest first), then desc.json, then the folder.
    $items = @(Get-ChildItem -LiteralPath $ModDir -Recurse -Force | Sort-Object { $_.FullName.Length } -Descending)
    foreach ($i in $items) {
        if (-not $i.PSIsContainer -and $i.Name -ieq 'desc.json' -and (Test-RoiPathEqual $i.DirectoryName $ModDir)) { continue }
        if ($i.PSIsContainer) {
            Remove-Item -LiteralPath $i.FullName -Recurse -Force
        } elseif (Test-Path -LiteralPath $i.FullName) {
            Remove-Item -LiteralPath $i.FullName -Force
        }
    }
    $desc = [System.IO.Path]::Combine($ModDir, 'desc.json')
    if (Test-Path -LiteralPath $desc) { Remove-Item -LiteralPath $desc -Force }
    Remove-Item -LiteralPath $ModDir -Recurse -Force
}

$exitCode = 1
try {
    $baseline = Get-RoiBaseline
    if ($IncludeBackups -and -not $RemoveExchangeDir) {
        throw '-IncludeBackups requires -RemoveExchangeDir.'
    }

    $install = Resolve-RoiGameInstall -GameDir $GameDir
    $gd = $install.GameDir
    $modsDir = [System.IO.Path]::Combine($gd, 'Mods')
    $modDir = Get-RoiModDir -GameDir $gd
    Write-Host ("Game folder: {0} ({1})" -f $gd, $install.Source)

    $procs = Get-RoiGameProcess
    if ($procs.Count -gt 0) {
        Write-Host ("NOTE: Rise of Industry is running (PID {0}). An already loaded observer stays active until the game restarts." -f (($procs | ForEach-Object { $_.Id }) -join ', '))
    }

    # --- Mod folder -----------------------------------------------------------
    if (-not (Test-Path -LiteralPath $modDir -PathType Container)) {
        Write-Host "Mod folder not present: $modDir (nothing to remove)."
    } else {
        $unexpected = Get-RoiUnexpectedModFiles -ModDir $modDir
        $descPath = [System.IO.Path]::Combine($modDir, 'desc.json')
        $nameProblem = $null
        if (Test-Path -LiteralPath $descPath -PathType Leaf) {
            $n = Get-RoiDescJsonName -Path $descPath
            if ($n.Name -cne $baseline.ModName) { $nameProblem = "desc.json name is '$($n.Name)', expected '$($baseline.ModName)'" }
        }
        if (($unexpected.Count -gt 0 -or $nameProblem) -and -not $Force) {
            if ($unexpected.Count -gt 0) { Write-Host ("Unexpected entries in {0}: {1}" -f $modDir, ($unexpected -join ', ')) }
            if ($nameProblem) { Write-Host "Unexpected descriptor: $nameProblem" }
            throw 'Refusing to remove the mod folder because it contains unexpected content. Re-run with -Force to remove it anyway.'
        }
        $listing = @(Get-ChildItem -LiteralPath $modDir -Recurse -Force -File | ForEach-Object { $_.FullName })
        if ($DryRun) {
            Write-Host "DRY RUN: would remove $modDir containing:"
            foreach ($l in $listing) { Write-Host "  $l" }
        } else {
            Remove-ModFolder -ModDir $modDir
            if (Test-Path -LiteralPath $modDir) {
                throw "The folder '$modDir' could not be removed completely. Remove it manually; a Mods subfolder without desc.json disables all mods."
            }
            Write-Host "Removed $modDir :"
            foreach ($l in $listing) { Write-Host "  $l" }
            Write-Host "The Mods folder itself was left in place: $modsDir"
        }
    }

    # --- Exchange directory -----------------------------------------------------
    if ($RemoveExchangeDir) {
        $ex = Get-RoiExchangeDir
        if (-not (Test-Path -LiteralPath $ex -PathType Container)) {
            Write-Host "Exchange directory not present: $ex (nothing to remove)."
        } else {
            Assert-SafeExchangeDir -Dir $ex -GameDirPath $gd
            $backups = [System.IO.Path]::Combine($ex, 'backups')
            $hasBackups = Test-Path -LiteralPath $backups
            $top = @(Get-ChildItem -LiteralPath $ex -Force)
            if ($IncludeBackups) {
                Write-Host "WARNING: removing the whole exchange directory INCLUDING save backups: $ex"
                if ($hasBackups) {
                    foreach ($b in @(Get-ChildItem -LiteralPath $backups -Force)) { Write-Host "  backup: $($b.FullName)" }
                }
            } else {
                Write-Host "Removing exchange directory contents (keeping backups\): $ex"
            }
            $toRemove = @()
            foreach ($t in $top) {
                if (-not $IncludeBackups -and $t.Name -ieq 'backups') { continue }
                $toRemove += $t
                Write-Host ("  {0}{1}" -f $t.FullName, $(if ($t.PSIsContainer) { '\' } else { '' }))
            }
            if ($DryRun) {
                Write-Host 'DRY RUN: nothing was deleted.'
            } else {
                foreach ($t in $toRemove) { Remove-Item -LiteralPath $t.FullName -Recurse -Force }
                if ($IncludeBackups) {
                    Remove-Item -LiteralPath $ex -Recurse -Force
                    Write-Host "Removed $ex"
                } elseif ($hasBackups) {
                    Write-Host "Kept save backups in $backups (use -IncludeBackups to delete them)."
                } else {
                    $left = @(Get-ChildItem -LiteralPath $ex -Force)
                    if ($left.Count -eq 0) { Remove-Item -LiteralPath $ex -Force; Write-Host "Removed $ex" }
                }
            }
        }
    }
    $exitCode = 0
} catch {
    Write-Host "ERROR: $($_.Exception.Message)"
    $exitCode = 1
}

exit $exitCode
