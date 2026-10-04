#Requires -Version 5.1
<#
.SYNOPSIS
    Self-tests for the scripts in scripts\ (plain PowerShell asserts, no Pester needed).

.DESCRIPTION
    Everything runs against FAKE folders created under the system temp directory:
      - backup-saves.ps1 against a fake saves folder (stable, injected instability, read error, ...);
      - install-observer.ps1 / uninstall-observer.ps1 against fake game folders. The real
        Assembly-CSharp.dll is only READ (copied into the fake game folder) when the game is installed;
        otherwise the positive install tests are skipped;
      - collect-diagnostics.ps1 against a fake exchange directory ($env:ROI_MCP_EXCHANGE_DIR).
    The real saves folder, the real game folder and the real %LOCALAPPDATA%\RoiMcp are never written.
    The temp folder is deleted at the end unless -KeepTemp is given.

    Exit code 0 when all tests pass, 1 otherwise.

.PARAMETER KeepTemp
    Keep the temp folder for inspection.
#>
[CmdletBinding()]
param([switch]$KeepTemp)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

$scriptsDir = [System.IO.Path]::GetFullPath([System.IO.Path]::Combine($PSScriptRoot, '..'))
Import-Module ([System.IO.Path]::Combine($scriptsDir, 'lib', 'RoiMcpCommon.psm1')) -Force -DisableNameChecking
$baseline = Get-RoiBaseline

$script:Passed = 0
$script:Failed = 0
$script:Skipped = 0
$script:FailureList = New-Object System.Collections.Generic.List[string]

function Assert-That {
    param([bool]$Condition, [string]$Message, [string]$Detail)
    if ($Condition) {
        $script:Passed++
        Write-Host "  PASS  $Message"
    } else {
        $script:Failed++
        $script:FailureList.Add($Message)
        Write-Host "  FAIL  $Message" -ForegroundColor Red
        if ($Detail) { Write-Host ($Detail -replace '(?m)^', '        | ') }
    }
}

function Skip-Test {
    param([string]$Message)
    $script:Skipped++
    Write-Host "  SKIP  $Message" -ForegroundColor Yellow
}

function Invoke-RoiScript {
    param([string]$Name, [hashtable]$Params)
    if (-not $Params) { $Params = @{} }
    $path = [System.IO.Path]::Combine($scriptsDir, $Name)
    $global:LASTEXITCODE = -999
    $out = & $path @Params *>&1 | Out-String
    return [pscustomobject]@{ ExitCode = $LASTEXITCODE; Output = $out }
}

function Get-TreeHashes {
    param([string]$Root)
    $map = @{}
    foreach ($f in (Get-ChildItem -LiteralPath $Root -Recurse -Force -File)) {
        $rel = Get-RoiRelativePath -Base $Root -Path $f.FullName
        $map[$rel] = (Get-RoiFileSha256 -Path $f.FullName) + '|' + $f.LastWriteTimeUtc.Ticks
    }
    return $map
}

function Test-MapsEqual {
    param([hashtable]$A, [hashtable]$B)
    if ($A.Count -ne $B.Count) { return $false }
    foreach ($k in $A.Keys) {
        if (-not $B.ContainsKey($k)) { return $false }
        if ($A[$k] -ne $B[$k]) { return $false }
    }
    return $true
}

function Get-BackupFolders {
    param([string]$DestRoot)
    $b = [System.IO.Path]::Combine($DestRoot, 'backups')
    if (-not (Test-Path -LiteralPath $b)) { return , @() }
    return , @(Get-ChildItem -LiteralPath $b -Directory -Filter 'saves-*')
}

function Write-Utf8 {
    param([string]$Path, [string]$Text)
    $dir = [System.IO.Path]::GetDirectoryName($Path)
    [void][System.IO.Directory]::CreateDirectory($dir)
    [System.IO.File]::WriteAllText($Path, $Text, (New-Object System.Text.UTF8Encoding($false)))
}

# ---------------------------------------------------------------------------
# Temp root and safety guards
# ---------------------------------------------------------------------------

$tempBase = Get-RoiFullPath ([System.IO.Path]::GetTempPath())
$tmp = [System.IO.Path]::Combine($tempBase, 'RoiMcpScriptTests-' + [guid]::NewGuid().ToString('N').Substring(0, 12))
[void][System.IO.Directory]::CreateDirectory($tmp)

$realSaves = Get-RoiSavesDir
$realInstall = $null
try { $realInstall = Find-RoiGameInstall } catch { $realInstall = $null }
$realGameDir = $null
if ($realInstall) { $realGameDir = $realInstall.GameDir }
elseif (Test-Path -LiteralPath ([System.IO.Path]::Combine($baseline.DefaultSteamPath, 'steamapps', 'common', 'RiseOfIndustry'))) {
    $realGameDir = Get-RoiFullPath ([System.IO.Path]::Combine($baseline.DefaultSteamPath, 'steamapps', 'common', 'RiseOfIndustry'))
}

function Assert-TempPath {
    param([string]$Path)
    if (-not (Test-RoiPathUnder -Child $Path -Parent $tmp)) { throw "SAFETY: '$Path' is not under the test temp folder." }
    if (Test-RoiPathUnder -Child $Path -Parent $realSaves) { throw "SAFETY: '$Path' is inside the real saves folder." }
    if ($realGameDir -and (Test-RoiPathUnder -Child $Path -Parent $realGameDir)) { throw "SAFETY: '$Path' is inside the real game folder." }
}

$realModProbe = $null
if ($realGameDir) { $realModProbe = Get-RoiModDir -GameDir $realGameDir }
$realModExistedBefore = $false
if ($realModProbe) { $realModExistedBefore = Test-Path -LiteralPath $realModProbe }

$savedExchangeEnv = [Environment]::GetEnvironmentVariable('ROI_MCP_EXCHANGE_DIR')
$defaultExchange = [System.IO.Path]::Combine($tmp, 'exchange-default')
[void][System.IO.Directory]::CreateDirectory($defaultExchange)
$env:ROI_MCP_EXCHANGE_DIR = $defaultExchange

Write-Host "Test temp folder: $tmp"

try {
    # =======================================================================
    Write-Host ''
    Write-Host '[syntax] all scripts parse'
    foreach ($f in (Get-ChildItem -LiteralPath $scriptsDir -Recurse -Include '*.ps1', '*.psm1' -File)) {
        $tokens = $null; $errors = $null
        [void][System.Management.Automation.Language.Parser]::ParseFile($f.FullName, [ref]$tokens, [ref]$errors)
        Assert-That ($errors.Count -eq 0) "parses: $($f.Name)" (($errors | ForEach-Object { $_.ToString() }) -join "`n")
    }

    # =======================================================================
    Write-Host ''
    Write-Host '[common] helpers'
    $env:ROI_MCP_EXCHANGE_DIR = $defaultExchange
    Assert-That (Test-RoiPathEqual (Get-RoiExchangeDir) $defaultExchange) 'exchange dir honours ROI_MCP_EXCHANGE_DIR'
    $env:ROI_MCP_EXCHANGE_DIR = ''
    $expectedDefault = [System.IO.Path]::Combine([Environment]::GetFolderPath('LocalApplicationData'), 'RoiMcp')
    Assert-That (Test-RoiPathEqual (Get-RoiExchangeDir) $expectedDefault) 'exchange dir defaults to %LOCALAPPDATA%\RoiMcp (path only)'
    $env:ROI_MCP_EXCHANGE_DIR = $defaultExchange
    $vdfText = "`"libraryfolders`"`n{`n`"0`"`n{`n`"path`"`t`t`"D:\\Games\\Steam`"`n}`n}"
    Assert-That ((Get-RoiVdfValue -Text $vdfText -Key 'path') -eq 'D:\Games\Steam') 'VDF path value is unescaped'
    $hf = [System.IO.Path]::Combine($tmp, 'hash.txt'); Write-Utf8 $hf 'abc'
    Assert-That ((Get-RoiFileSha256 -Path $hf) -eq 'BA7816BF8F01CFEA414140DE5DAE2223B00361A396177A9CB410FF61F20015AD') 'SHA-256 of "abc"'
    if ($realInstall) {
        Assert-That (-not [string]::IsNullOrWhiteSpace($realInstall.Manifest.BuildId)) ("auto-detect (read-only) found the install: {0}, buildid {1}" -f $realInstall.GameDir, $realInstall.Manifest.BuildId)
    } else {
        Skip-Test 'auto-detect: no Steam install of app 671440 found on this machine'
    }

    # =======================================================================
    Write-Host ''
    Write-Host '[backup-saves]'
    $src = [System.IO.Path]::Combine($tmp, 'fake-saves')
    Assert-TempPath $src
    Write-Utf8 ([System.IO.Path]::Combine($src, 'Saves', 'slot1.save')) ('save-one ' * 5000)
    Write-Utf8 ([System.IO.Path]::Combine($src, 'Saves', 'Sub Folder', 'slot2.save')) 'save-two'
    Write-Utf8 ([System.IO.Path]::Combine($src, 'settings.cfg')) 'cfg'
    $rnd = New-Object byte[] 300000; (New-Object System.Random 42).NextBytes($rnd)
    [System.IO.File]::WriteAllBytes([System.IO.Path]::Combine($src, 'Saves', 'binary.dat'), $rnd)
    [void][System.IO.Directory]::CreateDirectory([System.IO.Path]::Combine($src, 'EmptyDir'))
    $srcBefore = Get-TreeHashes $src

    # 1. not running, stable
    $dest1 = [System.IO.Path]::Combine($tmp, 'dest-notrunning'); Assert-TempPath $dest1
    $r = Invoke-RoiScript 'backup-saves.ps1' @{ SourceDir = $src; DestRoot = $dest1; TestGameState = 'NotRunning' }
    Assert-That ($r.ExitCode -eq 0) 'not running: exit code 0' $r.Output
    $folders = Get-BackupFolders $dest1
    Assert-That ($folders.Count -eq 1) 'not running: exactly one saves-<timestamp> folder' $r.Output
    if ($folders.Count -eq 1) {
        $bd = $folders[0].FullName
        Assert-That ($folders[0].Name -match '^saves-\d{8}-\d{6}$') 'backup folder name format saves-yyyyMMdd-HHmmss'
        $m = Get-Content -LiteralPath ([System.IO.Path]::Combine($bd, 'manifest.json')) -Raw | ConvertFrom-Json
        Assert-That ($m.valid -eq $true) 'not running: manifest valid=true'
        Assert-That ($m.game_running -eq $false) 'not running: manifest game_running=false'
        Assert-That (@($m.files).Count -eq 4) 'not running: manifest lists 4 files'
        $allOk = $true
        foreach ($e in $m.files) {
            $copyPath = [System.IO.Path]::Combine($bd, $e.path)
            if (-not (Test-Path -LiteralPath $copyPath)) { $allOk = $false; continue }
            $h = Get-RoiFileSha256 -Path $copyPath
            if ($e.sha256_copy -ne $h -or $e.sha256_source_before -ne $h -or -not $e.valid -or $null -ne $e.sha256_source_after) { $allOk = $false }
            if ($e.size -ne (Get-Item -LiteralPath $copyPath).Length) { $allOk = $false }
            if (-not $e.source_mtime_utc) { $allOk = $false }
        }
        Assert-That $allOk 'not running: every copy exists, hashes match source, relative paths preserved'
        Assert-That (Test-Path -LiteralPath ([System.IO.Path]::Combine($bd, 'Saves', 'Sub Folder', 'slot2.save'))) 'not running: nested relative path preserved'
        Assert-That ($m.start_utc -and $m.end_utc) 'not running: start/end UTC recorded'
    }
    Assert-That (Test-MapsEqual $srcBefore (Get-TreeHashes $src)) 'not running: source files unchanged (content and mtime)'

    # 2. running, stable
    $dest2 = [System.IO.Path]::Combine($tmp, 'dest-running'); Assert-TempPath $dest2
    $r = Invoke-RoiScript 'backup-saves.ps1' @{ SourceDir = $src; DestRoot = $dest2; TestGameState = 'Running' }
    Assert-That ($r.ExitCode -eq 0) 'running/stable: exit code 0' $r.Output
    $folders = Get-BackupFolders $dest2
    Assert-That ($folders.Count -eq 1) 'running/stable: one backup folder'
    if ($folders.Count -eq 1) {
        $m = Get-Content -LiteralPath ([System.IO.Path]::Combine($folders[0].FullName, 'manifest.json')) -Raw | ConvertFrom-Json
        Assert-That ($m.valid -eq $true -and $m.game_running -eq $true) 'running/stable: manifest valid, game_running=true'
        $ok = $true
        foreach ($e in $m.files) {
            if (-not ($e.sha256_source_before -and $e.sha256_source_before -eq $e.sha256_source_after -and $e.sha256_source_after -eq $e.sha256_copy -and $e.valid)) { $ok = $false }
        }
        Assert-That $ok 'running/stable: before == after == copy for every file'
    }
    Assert-That (Test-MapsEqual $srcBefore (Get-TreeHashes $src)) 'running/stable: source files unchanged'

    # 3. running, injected instability -> discarded
    $dest3 = [System.IO.Path]::Combine($tmp, 'dest-unstable'); Assert-TempPath $dest3
    $hook = {
        param($p, $rel)
        if ($rel -eq 'Saves\slot1.save') { [System.IO.File]::AppendAllText($p, 'changed by test hook') }
    }
    $r = Invoke-RoiScript 'backup-saves.ps1' @{ SourceDir = $src; DestRoot = $dest3; TestGameState = 'Running'; TestHookAfterFirstHash = $hook }
    Assert-That ($r.ExitCode -eq 1) 'running/unstable: exit code 1' $r.Output
    Assert-That ((Get-BackupFolders $dest3).Count -eq 0) 'running/unstable: backup folder discarded'
    Assert-That ($r.Output -match 'main menu') 'running/unstable: message asks to return to the main menu'
    Assert-That (Test-Path -LiteralPath ([System.IO.Path]::Combine($src, 'Saves', 'slot1.save'))) 'running/unstable: source file still present'

    # 3b. mtime-only change between hashes -> discarded
    $dest3b = [System.IO.Path]::Combine($tmp, 'dest-unstable-mtime'); Assert-TempPath $dest3b
    $hookMtime = {
        param($p, $rel)
        if ($rel -eq 'settings.cfg') { [System.IO.File]::SetLastWriteTimeUtc($p, [datetime]::UtcNow.AddMinutes(5)) }
    }
    $r = Invoke-RoiScript 'backup-saves.ps1' @{ SourceDir = $src; DestRoot = $dest3b; TestGameState = 'Running'; TestHookAfterFirstHash = $hookMtime }
    Assert-That ($r.ExitCode -eq 1 -and (Get-BackupFolders $dest3b).Count -eq 0) 'running/mtime changed: discarded, exit 1' $r.Output

    # 3c. file added during backup -> discarded
    $dest3c = [System.IO.Path]::Combine($tmp, 'dest-unstable-newfile'); Assert-TempPath $dest3c
    $hookAdd = {
        param($p, $rel)
        if ($rel -eq 'settings.cfg') { [System.IO.File]::WriteAllText(([System.IO.Path]::Combine([System.IO.Path]::GetDirectoryName($p), 'autosave-new.save')), 'new') }
    }
    $r = Invoke-RoiScript 'backup-saves.ps1' @{ SourceDir = $src; DestRoot = $dest3c; TestGameState = 'NotRunning'; TestHookAfterFirstHash = $hookAdd }
    Assert-That ($r.ExitCode -eq 1 -and (Get-BackupFolders $dest3c).Count -eq 0) 'file list changed during backup: discarded, exit 1' $r.Output
    Remove-Item -LiteralPath ([System.IO.Path]::Combine($src, 'autosave-new.save')) -Force

    # 4. read error (file locked exclusively by the test) -> discarded
    $dest4 = [System.IO.Path]::Combine($tmp, 'dest-readerror'); Assert-TempPath $dest4
    $lockPath = [System.IO.Path]::Combine($src, 'Saves', 'Sub Folder', 'slot2.save')
    $lock = [System.IO.FileStream]::new($lockPath, [System.IO.FileMode]::Open, [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::None)
    try {
        $r = Invoke-RoiScript 'backup-saves.ps1' @{ SourceDir = $src; DestRoot = $dest4; TestGameState = 'NotRunning' }
    } finally {
        $lock.Dispose()
    }
    Assert-That ($r.ExitCode -eq 1) 'read error: exit code 1' $r.Output
    Assert-That ((Get-BackupFolders $dest4).Count -eq 0) 'read error: backup folder discarded'

    # 5. pre-existing unrelated backup folder is never touched by a failing run
    $dest5 = [System.IO.Path]::Combine($tmp, 'dest-preexisting'); Assert-TempPath $dest5
    $oldBackup = [System.IO.Path]::Combine($dest5, 'backups', 'saves-20000101-000000')
    Write-Utf8 ([System.IO.Path]::Combine($oldBackup, 'keep.txt')) 'keep'
    $r = Invoke-RoiScript 'backup-saves.ps1' @{ SourceDir = $src; DestRoot = $dest5; TestGameState = 'Running'; TestHookAfterFirstHash = $hook }
    Assert-That ($r.ExitCode -eq 1) 'failing run with older backup present: exit 1'
    Assert-That (Test-Path -LiteralPath ([System.IO.Path]::Combine($oldBackup, 'keep.txt'))) 'failing run: older backup folder untouched'
    Assert-That ((Get-BackupFolders $dest5).Count -eq 1) 'failing run: only the older backup remains'

    # 6. missing source / destination inside source
    $r = Invoke-RoiScript 'backup-saves.ps1' @{ SourceDir = ([System.IO.Path]::Combine($tmp, 'does-not-exist')); DestRoot = $dest1; TestGameState = 'NotRunning' }
    Assert-That ($r.ExitCode -eq 1) 'missing source folder: exit code 1' $r.Output
    $r = Invoke-RoiScript 'backup-saves.ps1' @{ SourceDir = $src; DestRoot = ([System.IO.Path]::Combine($src, 'inner')); TestGameState = 'NotRunning' }
    Assert-That ($r.ExitCode -eq 1 -and -not (Test-Path -LiteralPath ([System.IO.Path]::Combine($src, 'inner')))) 'DestRoot inside source: refused, nothing created' $r.Output

    # 7. default DestRoot comes from ROI_MCP_EXCHANGE_DIR; Auto game detection (read-only Get-Process)
    $srcBefore7 = Get-TreeHashes $src   # the instability hooks above changed the source on purpose
    $exB = [System.IO.Path]::Combine($tmp, 'exchange-backup'); Assert-TempPath $exB
    $env:ROI_MCP_EXCHANGE_DIR = $exB
    $r = Invoke-RoiScript 'backup-saves.ps1' @{ SourceDir = $src }
    $env:ROI_MCP_EXCHANGE_DIR = $defaultExchange
    Assert-That ($r.ExitCode -eq 0) 'auto mode with DestRoot from ROI_MCP_EXCHANGE_DIR: exit 0' $r.Output
    $folders = Get-BackupFolders $exB
    Assert-That ($folders.Count -eq 1) 'auto mode: backup created under $env:ROI_MCP_EXCHANGE_DIR\backups'
    if ($folders.Count -eq 1) {
        $m = Get-Content -LiteralPath ([System.IO.Path]::Combine($folders[0].FullName, 'manifest.json')) -Raw | ConvertFrom-Json
        $isRunning = ((Get-RoiGameProcess).Count -gt 0)
        Assert-That ($m.game_running -eq $isRunning) ("auto mode: game_running={0} matches Get-Process" -f $m.game_running)
    }
    Assert-That (Test-MapsEqual $srcBefore7 (Get-TreeHashes $src)) 'auto mode: source files unchanged'

    # =======================================================================
    Write-Host ''
    Write-Host '[install-observer / uninstall-observer]'
    $placeholderDll = [System.IO.Path]::Combine($tmp, 'build', 'RoiMcpObserver.dll')
    [void][System.IO.Directory]::CreateDirectory([System.IO.Path]::GetDirectoryName($placeholderDll))
    [System.IO.File]::WriteAllBytes($placeholderDll, [byte[]](77, 90, 1, 2, 3, 4, 5))
    $placeholderDesc = [System.IO.Path]::Combine($tmp, 'build', 'desc.json')
    Write-Utf8 $placeholderDesc '{"author":"x","name":"RoiMcpObserver","version":1,"versionString":"1.0.0","dependencies":[],"description":"x"}'
    $wrongDesc = [System.IO.Path]::Combine($tmp, 'build', 'wrong-desc.json')
    Write-Utf8 $wrongDesc '{"author":"x","name":"SomethingElse","version":1,"versionString":"1.0.0","dependencies":[],"description":"x"}'

    $realAsm = $null
    if ($realGameDir) {
        $cand = Get-RoiAssemblyCSharpPath -GameDir $realGameDir
        if (Test-Path -LiteralPath $cand -PathType Leaf) { $realAsm = $cand }
    }
    $asmCache = $null
    if ($realAsm) {
        $asmCache = [System.IO.Path]::Combine($tmp, 'asm-cache', 'Assembly-CSharp.dll')
        [void][System.IO.Directory]::CreateDirectory([System.IO.Path]::GetDirectoryName($asmCache))
        [void](Copy-RoiFileShared -Source $realAsm -Destination $asmCache)   # read-only shared read of the real file
        $asmMatches = ((Get-RoiFileSha256 -Path $asmCache) -eq $baseline.AssemblyCSharpSha256)
        Assert-That $asmMatches 'real Assembly-CSharp.dll (read-only copy) matches the baseline hash'
        if (-not $asmMatches) { $asmCache = $null }
    }

    function New-FakeGame {
        param([string]$Name, [ValidateSet('Real', 'Dummy')][string]$Assembly = 'Real', [string]$ManifestBuildId)
        $lib = [System.IO.Path]::Combine($tmp, 'games', $Name)
        $gd = [System.IO.Path]::Combine($lib, 'steamapps', 'common', 'RiseOfIndustry')
        Assert-TempPath $gd
        $managed = [System.IO.Path]::Combine($gd, 'Rise of Industry_Data', 'Managed')
        [void][System.IO.Directory]::CreateDirectory($managed)
        $asmDest = [System.IO.Path]::Combine($managed, 'Assembly-CSharp.dll')
        if ($Assembly -eq 'Real') { Copy-Item -LiteralPath $asmCache -Destination $asmDest }
        else { [System.IO.File]::WriteAllBytes($asmDest, [byte[]](1, 2, 3)) }
        if ($ManifestBuildId) {
            $acf = "`"AppState`"`n{`n`t`"appid`"`t`t`"671440`"`n`t`"installdir`"`t`t`"RiseOfIndustry`"`n`t`"buildid`"`t`t`"$ManifestBuildId`"`n}`n"
            Write-Utf8 ([System.IO.Path]::Combine($lib, 'steamapps', 'appmanifest_671440.acf')) $acf
        }
        return $gd
    }

    function Get-FileSet {
        param([string]$Dir)
        if (-not (Test-Path -LiteralPath $Dir)) { return '' }
        return ((@(Get-ChildItem -LiteralPath $Dir -Recurse -Force | ForEach-Object { Get-RoiRelativePath -Base $Dir -Path $_.FullName }) | Sort-Object) -join ';')
    }

    $instArgs = @{ ObserverDll = $placeholderDll; DescJson = $placeholderDesc }

    # GameDir that does not exist: must fail, no fallback to auto-detection
    $missingGd = [System.IO.Path]::Combine($tmp, 'games', 'missing')
    $r = Invoke-RoiScript 'install-observer.ps1' (@{ GameDir = $missingGd } + $instArgs)
    Assert-That ($r.ExitCode -eq 1 -and $r.Output -notmatch 'Installed:') 'install: non-existent -GameDir fails (no auto-detect fallback)' $r.Output

    # Hash mismatch (dummy Assembly-CSharp.dll)
    $gdDummy = New-FakeGame -Name 'dummy-asm' -Assembly Dummy
    $r = Invoke-RoiScript 'install-observer.ps1' (@{ GameDir = $gdDummy } + $instArgs)
    Assert-That ($r.ExitCode -eq 1) 'install: Assembly-CSharp hash mismatch aborts' $r.Output
    Assert-That ($r.Output -match 'detected:' -and $r.Output -match [regex]::Escape($baseline.AssemblyCSharpSha256)) 'install: mismatch prints detected and expected hashes'
    Assert-That (-not (Test-Path -LiteralPath ([System.IO.Path]::Combine($gdDummy, 'Mods')))) 'install: mismatch created nothing'

    if (-not $asmCache) {
        Skip-Test 'positive install/uninstall tests: real Assembly-CSharp.dll not available on this machine'
    } else {
        # Dry run
        $gd = New-FakeGame -Name 'dryrun'
        $r = Invoke-RoiScript 'install-observer.ps1' (@{ GameDir = $gd; DryRun = $true } + $instArgs)
        Assert-That ($r.ExitCode -eq 0 -and $r.Output -match 'DRY RUN') 'install -DryRun: exit 0' $r.Output
        Assert-That (-not (Test-Path -LiteralPath ([System.IO.Path]::Combine($gd, 'Mods')))) 'install -DryRun: nothing written'

        # Install without manifest (warning), then verify exactly two files
        $gd = New-FakeGame -Name 'main'
        $treeBefore = Get-FileSet $gd
        $r = Invoke-RoiScript 'install-observer.ps1' (@{ GameDir = $gd } + $instArgs)
        Assert-That ($r.ExitCode -eq 0) 'install (no manifest): exit 0' $r.Output
        Assert-That ($r.Output -match 'WARNING: appmanifest') 'install (no manifest): warns that the build id cannot be checked'
        $modDir = Get-RoiModDir -GameDir $gd
        Assert-That ((Get-FileSet $modDir) -eq 'code;code\RoiMcpObserver.dll;desc.json') 'install: exactly desc.json and code\RoiMcpObserver.dll' (Get-FileSet $modDir)
        Assert-That ((Get-RoiFileSha256 ([System.IO.Path]::Combine($modDir, 'code', 'RoiMcpObserver.dll'))) -eq (Get-RoiFileSha256 $placeholderDll)) 'install: DLL content matches'
        Assert-That ((Get-RoiFileSha256 ([System.IO.Path]::Combine($modDir, 'desc.json'))) -eq (Get-RoiFileSha256 $placeholderDesc)) 'install: desc.json content matches'
        $expectedTree = (@(($treeBefore -split ';') + 'Mods', 'Mods\RoiMcpObserver', 'Mods\RoiMcpObserver\code', 'Mods\RoiMcpObserver\code\RoiMcpObserver.dll', 'Mods\RoiMcpObserver\desc.json') | Where-Object { $_ } | Sort-Object) -join ';'
        Assert-That ((Get-FileSet $gd) -eq $expectedTree) 'install: nothing else in the game folder changed' (Get-FileSet $gd)

        # Re-install (overwrite) works
        $r = Invoke-RoiScript 'install-observer.ps1' (@{ GameDir = $gd } + $instArgs)
        Assert-That ($r.ExitCode -eq 0) 'reinstall over an existing clean install: exit 0' $r.Output

        # Unexpected file inside own mod folder -> install refuses, uninstall refuses without -Force
        $extra = [System.IO.Path]::Combine($modDir, 'code', 'stray.dll')
        [System.IO.File]::WriteAllBytes($extra, [byte[]](1))
        $r = Invoke-RoiScript 'install-observer.ps1' (@{ GameDir = $gd } + $instArgs)
        Assert-That ($r.ExitCode -eq 1) 'install: own mod folder with unexpected file aborts' $r.Output
        $r = Invoke-RoiScript 'uninstall-observer.ps1' @{ GameDir = $gd }
        Assert-That ($r.ExitCode -eq 1 -and (Test-Path -LiteralPath $extra)) 'uninstall: unexpected file without -Force refuses and keeps folder' $r.Output
        $r = Invoke-RoiScript 'uninstall-observer.ps1' @{ GameDir = $gd; Force = $true }
        Assert-That ($r.ExitCode -eq 0 -and -not (Test-Path -LiteralPath $modDir)) 'uninstall -Force: folder removed' $r.Output
        Assert-That (Test-Path -LiteralPath ([System.IO.Path]::Combine($gd, 'Mods'))) 'uninstall: Mods folder itself kept'

        # Normal install + uninstall cycle
        $r = Invoke-RoiScript 'install-observer.ps1' (@{ GameDir = $gd } + $instArgs)
        Assert-That ($r.ExitCode -eq 0) 'install again: exit 0' $r.Output
        $r = Invoke-RoiScript 'uninstall-observer.ps1' @{ GameDir = $gd; DryRun = $true }
        Assert-That ($r.ExitCode -eq 0 -and (Test-Path -LiteralPath $modDir)) 'uninstall -DryRun: nothing removed' $r.Output
        $r = Invoke-RoiScript 'uninstall-observer.ps1' @{ GameDir = $gd }
        Assert-That ($r.ExitCode -eq 0 -and -not (Test-Path -LiteralPath $modDir)) 'uninstall: mod folder removed' $r.Output
        Assert-That ((Get-FileSet $gd) -eq ((@(($treeBefore -split ';') + 'Mods') | Where-Object { $_ } | Sort-Object) -join ';')) 'uninstall: only the empty Mods folder remains in addition to the original files' (Get-FileSet $gd)
        $r = Invoke-RoiScript 'uninstall-observer.ps1' @{ GameDir = $gd }
        Assert-That ($r.ExitCode -eq 0 -and $r.Output -match 'nothing to remove') 'uninstall when not installed: exit 0, nothing to remove' $r.Output

        # Manifest with correct / wrong build id
        $gd = New-FakeGame -Name 'manifest-ok' -ManifestBuildId $baseline.SteamBuildId
        $r = Invoke-RoiScript 'install-observer.ps1' (@{ GameDir = $gd } + $instArgs)
        Assert-That ($r.ExitCode -eq 0 -and $r.Output -match 'buildid 9064059') 'install: manifest with baseline build id accepted' $r.Output
        $gd = New-FakeGame -Name 'manifest-bad' -ManifestBuildId '1234567'
        $r = Invoke-RoiScript 'install-observer.ps1' (@{ GameDir = $gd } + $instArgs)
        Assert-That ($r.ExitCode -eq 1 -and -not (Test-Path -LiteralPath ([System.IO.Path]::Combine($gd, 'Mods')))) 'install: wrong manifest build id aborts, nothing created' $r.Output
        Assert-That ($r.Output -match '1234567' -and $r.Output -match '9064059') 'install: build id mismatch prints detected and expected'

        # Mods hygiene: subfolder without desc.json
        $gd = New-FakeGame -Name 'hygiene-nodesc'
        [void][System.IO.Directory]::CreateDirectory([System.IO.Path]::Combine($gd, 'Mods', 'OtherMod', 'code'))
        $r = Invoke-RoiScript 'install-observer.ps1' (@{ GameDir = $gd } + $instArgs)
        Assert-That ($r.ExitCode -eq 1 -and -not (Test-Path -LiteralPath (Get-RoiModDir -GameDir $gd))) 'install: Mods subfolder without desc.json aborts' $r.Output

        # Mods hygiene: another mod named RoiMcpObserver
        $gd = New-FakeGame -Name 'hygiene-dup'
        Write-Utf8 ([System.IO.Path]::Combine($gd, 'Mods', 'CopyOfObserver', 'desc.json')) '{"author":"y","name":"RoiMcpObserver","version":1,"versionString":"1.0.0","dependencies":[]}'
        $r = Invoke-RoiScript 'install-observer.ps1' (@{ GameDir = $gd } + $instArgs)
        Assert-That ($r.ExitCode -eq 1 -and -not (Test-Path -LiteralPath (Get-RoiModDir -GameDir $gd))) 'install: duplicate mod name aborts' $r.Output

        # Mods hygiene: other, well-formed mod is fine and left untouched
        $gd = New-FakeGame -Name 'hygiene-ok'
        $otherDesc = [System.IO.Path]::Combine($gd, 'Mods', 'GoodMod', 'desc.json')
        Write-Utf8 $otherDesc '{"author":"y","name":"GoodMod","version":1,"versionString":"1.0.0","dependencies":[]}'
        $otherHash = Get-RoiFileSha256 $otherDesc
        $r = Invoke-RoiScript 'install-observer.ps1' (@{ GameDir = $gd } + $instArgs)
        Assert-That ($r.ExitCode -eq 0) 'install: alongside another valid mod: exit 0' $r.Output
        $r2 = Invoke-RoiScript 'uninstall-observer.ps1' @{ GameDir = $gd }
        Assert-That ($r2.ExitCode -eq 0 -and (Get-RoiFileSha256 $otherDesc) -eq $otherHash) 'install/uninstall: other mod untouched' $r2.Output

        # Source desc.json with the wrong name, missing DLL
        $gd = New-FakeGame -Name 'bad-source'
        $r = Invoke-RoiScript 'install-observer.ps1' @{ GameDir = $gd; ObserverDll = $placeholderDll; DescJson = $wrongDesc }
        Assert-That ($r.ExitCode -eq 1 -and -not (Test-Path -LiteralPath ([System.IO.Path]::Combine($gd, 'Mods')))) 'install: source desc.json with wrong name aborts' $r.Output
        $r = Invoke-RoiScript 'install-observer.ps1' @{ GameDir = $gd; ObserverDll = ([System.IO.Path]::Combine($tmp, 'nope.dll')); DescJson = $placeholderDesc }
        Assert-That ($r.ExitCode -eq 1 -and -not (Test-Path -LiteralPath ([System.IO.Path]::Combine($gd, 'Mods')))) 'install: missing observer DLL aborts' $r.Output

        # Uninstall refuses a folder whose desc.json is not ours (without -Force)
        $gd = New-FakeGame -Name 'foreign-desc'
        Write-Utf8 ([System.IO.Path]::Combine($gd, 'Mods', 'RoiMcpObserver', 'desc.json')) '{"name":"NotOurs"}'
        $r = Invoke-RoiScript 'uninstall-observer.ps1' @{ GameDir = $gd }
        Assert-That ($r.ExitCode -eq 1 -and (Test-Path -LiteralPath (Get-RoiModDir -GameDir $gd))) 'uninstall: foreign desc.json name refused without -Force' $r.Output
    }

    # Uninstall -RemoveExchangeDir (fake exchange dir)
    $gdEx = New-FakeGame -Name 'exchange-removal' -Assembly Dummy
    $exR = [System.IO.Path]::Combine($tmp, 'exchange-removal'); Assert-TempPath $exR
    Write-Utf8 ([System.IO.Path]::Combine($exR, 'heartbeat.json')) '{}'
    Write-Utf8 ([System.IO.Path]::Combine($exR, 'observer.log')) 'log'
    Write-Utf8 ([System.IO.Path]::Combine($exR, 'backups', 'saves-20240101-000000', 'manifest.json')) '{}'
    $env:ROI_MCP_EXCHANGE_DIR = $exR
    $r = Invoke-RoiScript 'uninstall-observer.ps1' @{ GameDir = $gdEx; IncludeBackups = $true }
    Assert-That ($r.ExitCode -eq 1 -and (Test-Path -LiteralPath $exR)) 'uninstall: -IncludeBackups without -RemoveExchangeDir refused' $r.Output
    $r = Invoke-RoiScript 'uninstall-observer.ps1' @{ GameDir = $gdEx; RemoveExchangeDir = $true }
    Assert-That ($r.ExitCode -eq 0) 'uninstall -RemoveExchangeDir: exit 0' $r.Output
    Assert-That (-not (Test-Path -LiteralPath ([System.IO.Path]::Combine($exR, 'heartbeat.json'))) -and -not (Test-Path -LiteralPath ([System.IO.Path]::Combine($exR, 'observer.log')))) 'uninstall -RemoveExchangeDir: exchange files removed'
    Assert-That (Test-Path -LiteralPath ([System.IO.Path]::Combine($exR, 'backups', 'saves-20240101-000000', 'manifest.json'))) 'uninstall -RemoveExchangeDir: backups kept'
    $r = Invoke-RoiScript 'uninstall-observer.ps1' @{ GameDir = $gdEx; RemoveExchangeDir = $true; IncludeBackups = $true }
    Assert-That ($r.ExitCode -eq 0 -and -not (Test-Path -LiteralPath $exR)) 'uninstall -RemoveExchangeDir -IncludeBackups: whole exchange dir removed' $r.Output
    $env:ROI_MCP_EXCHANGE_DIR = $tmp
    $r = Invoke-RoiScript 'uninstall-observer.ps1' @{ GameDir = $gdEx; RemoveExchangeDir = $true }
    Assert-That ($r.ExitCode -eq 1 -and (Test-Path -LiteralPath $gdEx)) 'uninstall -RemoveExchangeDir: refuses an exchange dir that contains the game folder' $r.Output
    $env:ROI_MCP_EXCHANGE_DIR = $defaultExchange

    # =======================================================================
    Write-Host ''
    Write-Host '[collect-diagnostics]'
    $exD = [System.IO.Path]::Combine($tmp, 'exchange-diag'); Assert-TempPath $exD
    $env:ROI_MCP_EXCHANGE_DIR = $exD
    Write-Utf8 ([System.IO.Path]::Combine($exD, 'heartbeat.json')) '{"schema":"roi-mcp/heartbeat","schema_version":"1.0.0","observer_version":"1.0.0","state":"menu"}'
    Write-Utf8 ([System.IO.Path]::Combine($exD, 'state.json')) '{"schema":"roi-mcp/state","schema_version":"1.2.0","observer_version":"1.0.0","data":{"secret":"world"}}'
    Write-Utf8 ([System.IO.Path]::Combine($exD, 'static.json')) '{"schema":"roi-mcp/static","schema_version":"1.0.1","data":{}}'
    Write-Utf8 ([System.IO.Path]::Combine($exD, 'history.json')) 'not json'
    Write-Utf8 ([System.IO.Path]::Combine($exD, 'observer.log')) 'line'
    Write-Utf8 ([System.IO.Path]::Combine($exD, 'observer.log.1')) 'old line'
    Write-Utf8 ([System.IO.Path]::Combine($exD, 'server.log')) 'srv'
    Write-Utf8 ([System.IO.Path]::Combine($exD, 'server.log.2')) 'srv2'
    Write-Utf8 ([System.IO.Path]::Combine($exD, 'observer.config.json')) '{"capture_interval_s":10}'
    Write-Utf8 ([System.IO.Path]::Combine($exD, 'observer.disabled')) ''
    Write-Utf8 ([System.IO.Path]::Combine($exD, 'state.json.tmp-123')) 'partial'
    Write-Utf8 ([System.IO.Path]::Combine($exD, 'backups', 'saves-20240101-000000', 'Saves', 'my.save')) 'SAVEDATA'

    Add-Type -AssemblyName System.IO.Compression
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    function Get-ZipEntries {
        param([string]$Path)
        $z = [System.IO.Compression.ZipFile]::OpenRead($Path)
        try { return , @($z.Entries | ForEach-Object { $_.FullName } | Sort-Object) } finally { $z.Dispose() }
    }
    function Get-ZipText {
        param([string]$Path, [string]$Entry)
        $z = [System.IO.Compression.ZipFile]::OpenRead($Path)
        try {
            $e = $z.GetEntry($Entry)
            $sr = New-Object System.IO.StreamReader($e.Open())
            try { return $sr.ReadToEnd() } finally { $sr.Dispose() }
        } finally { $z.Dispose() }
    }

    $r = Invoke-RoiScript 'collect-diagnostics.ps1' @{}
    Assert-That ($r.ExitCode -eq 0) 'diagnostics default: exit 0' $r.Output
    $zips = @(Get-ChildItem -LiteralPath $exD -Filter 'diagnostics-*.zip' -File)
    Assert-That ($zips.Count -eq 1) 'diagnostics default: zip written to <exchange>\diagnostics-<timestamp>.zip'
    if ($zips.Count -eq 1) {
        Assert-That (($r.Output -replace '\s', '').Contains(($zips[0].FullName -replace '\s', ''))) 'diagnostics: prints the zip path' ($r.Output + ' | expected: ' + $zips[0].FullName)
        $entries = (Get-ZipEntries $zips[0].FullName) -join ';'
        Assert-That ($entries -eq 'heartbeat.json;observer.config.json;observer.log;observer.log.1;server.log;server.log.2;summary.json') 'diagnostics default: exact entry set (no snapshots, no saves, no backups, no temp files)' $entries
        $s = (Get-ZipText $zips[0].FullName 'summary.json') | ConvertFrom-Json
        Assert-That ($s.schema_versions.heartbeat.schema_version -eq '1.0.0' -and $s.schema_versions.state.schema_version -eq '1.2.0' -and $s.schema_versions.static.schema_version -eq '1.0.1') 'summary: schema versions read from heartbeat/state/static'
        Assert-That ($s.schema_versions.history.PSObject.Properties.Name -contains 'error') 'summary: unreadable history.json reported as error'
        Assert-That (-not [string]::IsNullOrWhiteSpace($s.os_version)) 'summary: OS version present'
        Assert-That ($s.game_running -eq ((Get-RoiGameProcess).Count -gt 0)) 'summary: game_running matches Get-Process'
        $names = @($s.files | ForEach-Object { $_.path })
        Assert-That (($names -contains 'state.json') -and ($names -contains 'observer.disabled') -and -not ($names | Where-Object { $_ -like 'backups*' })) 'summary: lists exchange files with sizes/mtimes, backups only summarised'
        Assert-That (@($s.backups).Count -eq 1 -and $s.backups[0].file_count -eq 1) 'summary: backups summarised as counts only'
        $summaryText = Get-ZipText $zips[0].FullName 'summary.json'
        Assert-That (($summaryText -notmatch 'my\.save') -and ($summaryText -notmatch 'SAVEDATA') -and ($summaryText -notmatch 'world')) 'summary: no save names/contents and no snapshot data'
    }

    $outFile = [System.IO.Path]::Combine($tmp, 'out', 'diag-custom.zip')
    $r = Invoke-RoiScript 'collect-diagnostics.ps1' @{ OutFile = $outFile; IncludeSnapshots = $true }
    Assert-That ($r.ExitCode -eq 0 -and (Test-Path -LiteralPath $outFile)) 'diagnostics -OutFile -IncludeSnapshots: exit 0, file written' $r.Output
    if (Test-Path -LiteralPath $outFile) {
        $entries = (Get-ZipEntries $outFile) -join ';'
        Assert-That ($entries -eq 'heartbeat.json;history.json;observer.config.json;observer.log;observer.log.1;server.log;server.log.2;state.json;static.json;summary.json') 'diagnostics -IncludeSnapshots: snapshots included, still no backups' $entries
    }
    $r = Invoke-RoiScript 'collect-diagnostics.ps1' @{ OutFile = $outFile }
    Assert-That ($r.ExitCode -eq 1) 'diagnostics: existing -OutFile is not overwritten' $r.Output

    $env:ROI_MCP_EXCHANGE_DIR = [System.IO.Path]::Combine($tmp, 'no-such-exchange')
    $r = Invoke-RoiScript 'collect-diagnostics.ps1' @{}
    Assert-That ($r.ExitCode -eq 1) 'diagnostics: missing exchange dir -> exit 1' $r.Output
    $env:ROI_MCP_EXCHANGE_DIR = $defaultExchange

    # =======================================================================
    Write-Host ''
    Write-Host '[global safety]'
    if ($realModProbe) {
        Assert-That ((Test-Path -LiteralPath $realModProbe) -eq $realModExistedBefore) 'real game folder: Mods\RoiMcpObserver presence unchanged by the tests'
    }
} catch {
    $script:Failed++
    $script:FailureList.Add("Unhandled: $($_.Exception.Message)")
    Write-Host "UNHANDLED: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host $_.ScriptStackTrace
} finally {
    [Environment]::SetEnvironmentVariable('ROI_MCP_EXCHANGE_DIR', $savedExchangeEnv)
    if (-not $KeepTemp) {
        try { Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction Stop } catch { Write-Host "WARNING: could not delete $tmp : $($_.Exception.Message)" }
    } else {
        Write-Host "Temp folder kept: $tmp"
    }
}

Write-Host ''
Write-Host ("Passed: {0}  Failed: {1}  Skipped: {2}" -f $script:Passed, $script:Failed, $script:Skipped)
if ($script:Failed -gt 0) {
    foreach ($f in $script:FailureList) { Write-Host "  - $f" -ForegroundColor Red }
    exit 1
}
exit 0
