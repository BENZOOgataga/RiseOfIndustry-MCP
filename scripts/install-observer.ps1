#Requires -Version 5.1
<#
.SYNOPSIS
    Installs the RoiMcpObserver mod into a Rise of Industry installation (PRD section 20).

.DESCRIPTION
    Writes exactly two files:
        <GameDir>\Mods\RoiMcpObserver\desc.json
        <GameDir>\Mods\RoiMcpObserver\code\RoiMcpObserver.dll
    creating only those folders (and Mods\ itself if it is missing). Nothing else in the installation,
    no PlayerPrefs/registry value and no save is touched.

    Before writing, it verifies (and aborts without installing on any failure):
      - the Steam build id in appmanifest_671440.acf is 9064059 (when the manifest is found; with an
        explicit -GameDir and no manifest next to it, a warning is printed instead);
      - the SHA-256 of Rise of Industry_Data\Managed\Assembly-CSharp.dll equals the baseline
        (PRD 3.1). There is NO override for this check;
      - Mods\ hygiene: every existing Mods\ subfolder contains desc.json, and no other mod folder has
        a desc.json named "RoiMcpObserver";
      - an existing Mods\RoiMcpObserver\ folder contains nothing but desc.json and
        code\RoiMcpObserver.dll (otherwise run uninstall-observer.ps1 -Force first);
      - the source desc.json parses and its name is "RoiMcpObserver";
      - Rise of Industry is not running from this installation (the game keeps the loaded observer
        DLL open, so it cannot be replaced while the game runs).

    Installing into a real game installation requires the user's explicit approval (PRD 20.2).

.PARAMETER GameDir
    Game installation folder. When omitted it is auto-detected from Steam (registry SteamPath, read
    only, else the default Steam path; libraryfolders.vdf; appmanifest_671440.acf). When given it must
    exist; there is no fallback to auto-detection.

.PARAMETER ObserverDll
    Built observer DLL. Default: observer\src\RoiMcp.Observer\bin\Release\net461\RoiMcpObserver.dll
    (relative to the repository root).

.PARAMETER DescJson
    Mod descriptor. Default: observer\mod-template\desc.json (relative to the repository root).

.PARAMETER DryRun
    Runs every check and prints what would be done without writing anything.
#>
[CmdletBinding()]
param(
    [string]$GameDir,
    [string]$ObserverDll,
    [string]$DescJson,
    [switch]$DryRun
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot 'lib\RoiMcpCommon.psm1') -Force -DisableNameChecking

$exitCode = 1
try {
    $baseline = Get-RoiBaseline
    $repoRoot = Get-RoiRepoRoot
    if ([string]::IsNullOrWhiteSpace($ObserverDll)) {
        $ObserverDll = [System.IO.Path]::Combine($repoRoot, 'observer', 'src', 'RoiMcp.Observer', 'bin', 'Release', 'net461', $baseline.ModDllName)
    }
    if ([string]::IsNullOrWhiteSpace($DescJson)) {
        $DescJson = [System.IO.Path]::Combine($repoRoot, 'observer', 'mod-template', 'desc.json')
    }
    $ObserverDll = Get-RoiFullPath $ObserverDll
    $DescJson = Get-RoiFullPath $DescJson

    # --- Source files -------------------------------------------------------
    if (-not (Test-Path -LiteralPath $ObserverDll -PathType Leaf)) {
        throw "Observer DLL not found: '$ObserverDll'. Build it first (scripts\build.ps1) or pass -ObserverDll."
    }
    if (-not (Test-Path -LiteralPath $DescJson -PathType Leaf)) {
        throw "desc.json not found: '$DescJson'. Pass -DescJson."
    }
    $descInfo = Get-RoiDescJsonName -Path $DescJson
    if (-not $descInfo.Parsed) {
        throw "Source desc.json '$DescJson' is not valid JSON."
    }
    if ($descInfo.Name -cne $baseline.ModName) {
        throw "Source desc.json name is '$($descInfo.Name)'; expected '$($baseline.ModName)'."
    }

    # --- Install location ---------------------------------------------------
    $install = Resolve-RoiGameInstall -GameDir $GameDir
    $gd = $install.GameDir
    Write-Host ("Game folder: {0} ({1})" -f $gd, $install.Source)

    # --- Build id -----------------------------------------------------------
    if ($install.Manifest) {
        $m = $install.Manifest
        Write-Host ("Steam manifest: {0} (appid {1}, buildid {2})" -f $m.ManifestPath, $m.AppId, $m.BuildId)
        if ($m.AppId -and ($m.AppId -ne $baseline.SteamAppId)) {
            throw "Steam manifest appid is '$($m.AppId)'; expected '$($baseline.SteamAppId)'."
        }
        if ($m.BuildId -ne $baseline.SteamBuildId) {
            throw ("Unsupported game build. Detected Steam buildid: '{0}'. Expected: '{1}'. Nothing was installed." -f $m.BuildId, $baseline.SteamBuildId)
        }
    } else {
        Write-Host ("WARNING: appmanifest_{0}.acf was not found next to '{1}'; the Steam build id cannot be checked. Relying on the Assembly-CSharp.dll hash." -f $baseline.SteamAppId, $gd)
    }

    # --- Assembly-CSharp hash (no override) ---------------------------------
    $asm = Get-RoiAssemblyCSharpPath -GameDir $gd
    if (-not (Test-Path -LiteralPath $asm -PathType Leaf)) {
        throw "Assembly-CSharp.dll not found at '$asm'. Is '$gd' a Rise of Industry installation?"
    }
    $asmHash = Get-RoiFileSha256 -Path $asm
    if ($asmHash -ne $baseline.AssemblyCSharpSha256) {
        Write-Host 'Assembly-CSharp.dll does not match the verified baseline (PRD 3.1).'
        Write-Host ("  detected: {0}" -f $asmHash)
        Write-Host ("  expected: {0}" -f $baseline.AssemblyCSharpSha256)
        throw 'Unsupported game build (Assembly-CSharp.dll hash mismatch). Nothing was installed. There is no override.'
    }
    Write-Host ("Assembly-CSharp.dll SHA-256 matches the baseline ({0})." -f $asmHash)

    # --- Mods hygiene -------------------------------------------------------
    $modsDir = [System.IO.Path]::Combine($gd, 'Mods')
    $modDir = Get-RoiModDir -GameDir $gd
    $hygiene = New-Object System.Collections.Generic.List[string]
    if (Test-Path -LiteralPath $modsDir -PathType Container) {
        foreach ($sub in (Get-ChildItem -LiteralPath $modsDir -Directory -Force)) {
            $isOwn = [string]::Equals($sub.Name, $baseline.ModName, [System.StringComparison]::OrdinalIgnoreCase)
            $desc = [System.IO.Path]::Combine($sub.FullName, 'desc.json')
            if ($isOwn) {
                $unexpected = Get-RoiUnexpectedModFiles -ModDir $sub.FullName
                if ($unexpected.Count -gt 0) {
                    $hygiene.Add(("Mods\{0}\ contains unexpected entries: {1}. Run uninstall-observer.ps1 -Force first." -f $sub.Name, ($unexpected -join ', ')))
                }
                if (Test-Path -LiteralPath $desc -PathType Leaf) {
                    $own = Get-RoiDescJsonName -Path $desc
                    if ($own.Name -and ($own.Name -cne $baseline.ModName)) {
                        $hygiene.Add(("Mods\{0}\desc.json has name '{1}', not '{2}'." -f $sub.Name, $own.Name, $baseline.ModName))
                    }
                }
                continue
            }
            if (-not (Test-Path -LiteralPath $desc -PathType Leaf)) {
                $hygiene.Add(("Mods\{0}\ has no desc.json (a Mods subfolder without desc.json disables all mods)." -f $sub.Name))
                continue
            }
            $other = Get-RoiDescJsonName -Path $desc
            if (-not $other.Parsed) {
                Write-Host ("WARNING: Mods\{0}\desc.json is not valid JSON; checked its name by text search." -f $sub.Name)
            }
            if ($other.Name -and [string]::Equals($other.Name, $baseline.ModName, [System.StringComparison]::OrdinalIgnoreCase)) {
                $hygiene.Add(("Mods\{0}\desc.json is also named '{1}' (a duplicate mod name blocks game boot)." -f $sub.Name, $baseline.ModName))
            }
        }
    }
    if ($hygiene.Count -gt 0) {
        foreach ($h in $hygiene) { Write-Host "HYGIENE: $h" }
        throw 'Mods folder hygiene check failed. Nothing was installed.'
    }
    Write-Host 'Mods folder hygiene: OK.'

    # A running game memory-maps the loaded observer DLL, so it cannot be replaced; refuse before
    # writing anything instead of leaving a half-updated mod folder. A process whose path cannot be
    # read is treated as running from this installation.
    $gdFull = [System.IO.Path]::GetFullPath($gd).TrimEnd('') + ''
    $procs = Get-RoiGameProcess  # assigned first: piping the call directly passes the array as one object
    $blocking = @($procs | Where-Object {
            $exe = $null
            try { $exe = $_.Path } catch { $exe = $null }
            [string]::IsNullOrEmpty($exe) -or [System.IO.Path]::GetFullPath($exe).StartsWith($gdFull, [System.StringComparison]::OrdinalIgnoreCase)
        })
    if ($blocking.Count -gt 0) {
        $pids = ($blocking | ForEach-Object { $_.Id }) -join ', '
        if ($DryRun) {
            Write-Host ("NOTE: Rise of Industry is running from this folder (PID {0}). A real install requires the game to be closed." -f $pids)
        } else {
            throw ("Rise of Industry is running from this folder (PID {0}) and holds the observer DLL open. Quit the game normally, then run the install again. Nothing was installed." -f $pids)
        }
    }

    # --- Copy exactly two files ---------------------------------------------
    $codeDir = [System.IO.Path]::Combine($modDir, 'code')
    $destDesc = [System.IO.Path]::Combine($modDir, 'desc.json')
    $destDll = [System.IO.Path]::Combine($codeDir, $baseline.ModDllName)

    if ($DryRun) {
        Write-Host 'DRY RUN: nothing was written. Would:'
        if (-not (Test-Path -LiteralPath $modsDir)) { Write-Host "  create folder $modsDir" }
        if (-not (Test-Path -LiteralPath $modDir)) { Write-Host "  create folder $modDir" }
        Write-Host "  copy $DescJson -> $destDesc"
        if (-not (Test-Path -LiteralPath $codeDir)) { Write-Host "  create folder $codeDir" }
        Write-Host "  copy $ObserverDll -> $destDll"
        $exitCode = 0
    } else {
        $actions = New-Object System.Collections.Generic.List[string]
        if (-not (Test-Path -LiteralPath $modsDir)) {
            [void][System.IO.Directory]::CreateDirectory($modsDir); $actions.Add("created folder $modsDir")
        }
        if (-not (Test-Path -LiteralPath $modDir)) {
            [void][System.IO.Directory]::CreateDirectory($modDir); $actions.Add("created folder $modDir")
        }
        # desc.json first: a Mods subfolder must never be left without desc.json.
        Copy-Item -LiteralPath $DescJson -Destination $destDesc -Force
        $actions.Add("copied $DescJson -> $destDesc")
        if (-not (Test-Path -LiteralPath $codeDir)) {
            [void][System.IO.Directory]::CreateDirectory($codeDir); $actions.Add("created folder $codeDir")
        }
        Copy-Item -LiteralPath $ObserverDll -Destination $destDll -Force
        $actions.Add("copied $ObserverDll -> $destDll")

        if ((Get-RoiFileSha256 -Path $destDesc) -ne (Get-RoiFileSha256 -Path $DescJson)) { throw "Verification failed for $destDesc." }
        if ((Get-RoiFileSha256 -Path $destDll) -ne (Get-RoiFileSha256 -Path $ObserverDll)) { throw "Verification failed for $destDll." }

        Write-Host 'Installed:'
        foreach ($a in $actions) { Write-Host "  $a" }
        Write-Host ("Observer DLL SHA-256: {0}" -f (Get-RoiFileSha256 -Path $destDll))
        Write-Host 'Nothing else was modified. Restart the game (launch via Steam) to load the observer.'
        $exitCode = 0
    }
} catch {
    Write-Host "ERROR: $($_.Exception.Message)"
    $exitCode = 1
}

exit $exitCode
