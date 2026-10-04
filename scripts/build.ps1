#Requires -Version 5.1
<#
.SYNOPSIS
    Builds the observer, the read-only IL gate and (optionally) runs all tests.

.DESCRIPTION
    Builds observer\RoiMcpObserver.sln when it exists; otherwise builds, in this order:
        observer\readonly-gate\RoiMcp.ReadOnlyGate\RoiMcp.ReadOnlyGate.csproj
        observer\src\RoiMcp.Observer\RoiMcp.Observer.csproj
    With -Test it then runs "dotnet test" on the solution (or on every *Tests.csproj under observer\
    when there is no solution) and "uv run --directory mcp-server pytest".

    Stops at the first failure with a non-zero exit code. Paths are resolved from the repository
    root ($PSScriptRoot\..), so the script can be run from any working directory.

    This script never touches the game installation; deployment is install-observer.ps1.

.PARAMETER Configuration
    Release (default) or Debug.

.PARAMETER DebugFaults
    Passes -p:RoiDebugFaults=true (compiles the DEBUG_FAULTS fault-injection code). This build is ONLY
    for implementation gate E5 (PRD 22) and must NEVER be installed for normal use. The release IL gate
    rejects it.

.PARAMETER GameDir
    Passed to MSBuild as -p:RoiGameDir=<path> (the game installation folder, forwarded to the projects).

.PARAMETER Test
    Also run the .NET tests and the MCP server pytest suite.
#>
[CmdletBinding()]
param(
    [ValidateSet('Release', 'Debug')]
    [string]$Configuration = 'Release',
    [switch]$DebugFaults,
    [string]$GameDir,
    [switch]$Test
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

function Invoke-Step {
    param([string]$Title, [string]$Exe, [string[]]$Arguments)
    Write-Host ''
    Write-Host "==> $Title"
    Write-Host ("    {0} {1}" -f $Exe, ($Arguments -join ' '))
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Title failed (exit code $LASTEXITCODE)."
    }
}

$exitCode = 1
try {
    $repoRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::Combine($PSScriptRoot, '..'))
    $sln = [System.IO.Path]::Combine($repoRoot, 'observer', 'RoiMcpObserver.sln')
    $gateProj = [System.IO.Path]::Combine($repoRoot, 'observer', 'readonly-gate', 'RoiMcp.ReadOnlyGate', 'RoiMcp.ReadOnlyGate.csproj')
    $obsProj = [System.IO.Path]::Combine($repoRoot, 'observer', 'src', 'RoiMcp.Observer', 'RoiMcp.Observer.csproj')
    $serverDir = [System.IO.Path]::Combine($repoRoot, 'mcp-server')

    if (-not (Get-Command dotnet -ErrorAction SilentlyContinue)) {
        throw 'The .NET SDK (dotnet) was not found on PATH.'
    }

    $props = @()
    if ($DebugFaults) {
        $props += '-p:RoiDebugFaults=true'
        Write-Host '*****************************************************************'
        Write-Host '* DEBUG_FAULTS build: for implementation gate E5 ONLY.          *'
        Write-Host '* Never install this build for normal use.                      *'
        Write-Host '*****************************************************************'
    }
    if (-not [string]::IsNullOrWhiteSpace($GameDir)) {
        if (-not (Test-Path -LiteralPath $GameDir -PathType Container)) {
            throw "GameDir '$GameDir' does not exist."
        }
        $props += ('-p:RoiGameDir=' + [System.IO.Path]::GetFullPath($GameDir).TrimEnd([char]'\'))
    }

    $common = @('-c', $Configuration, '-nologo') + $props

    if (Test-Path -LiteralPath $sln -PathType Leaf) {
        Invoke-Step -Title 'Build solution' -Exe 'dotnet' -Arguments (@('build', $sln) + $common)
    } else {
        foreach ($p in @($gateProj, $obsProj)) {
            if (-not (Test-Path -LiteralPath $p -PathType Leaf)) { throw "Project not found: $p" }
        }
        Invoke-Step -Title 'Build read-only gate' -Exe 'dotnet' -Arguments (@('build', $gateProj) + $common)
        Invoke-Step -Title 'Build observer' -Exe 'dotnet' -Arguments (@('build', $obsProj) + $common)
    }

    if ($Test) {
        if (Test-Path -LiteralPath $sln -PathType Leaf) {
            Invoke-Step -Title 'Test solution' -Exe 'dotnet' -Arguments (@('test', $sln) + $common)
        } else {
            $testProjects = @(Get-ChildItem -LiteralPath ([System.IO.Path]::Combine($repoRoot, 'observer')) -Recurse -Filter '*Tests.csproj' -File |
                    Where-Object { $_.FullName -notmatch '\\(bin|obj)\\' })
            if ($testProjects.Count -eq 0) { throw 'No observer test projects found.' }
            foreach ($tp in $testProjects) {
                Invoke-Step -Title "Test $($tp.Name)" -Exe 'dotnet' -Arguments (@('test', $tp.FullName) + $common)
            }
        }
        if (-not (Test-Path -LiteralPath ([System.IO.Path]::Combine($serverDir, 'pyproject.toml')) -PathType Leaf)) {
            throw "MCP server project not found: $serverDir\pyproject.toml"
        }
        if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
            throw 'uv was not found on PATH (needed to run the MCP server tests).'
        }
        Invoke-Step -Title 'MCP server tests' -Exe 'uv' -Arguments @('run', '--directory', $serverDir, 'pytest')
    }

    Write-Host ''
    Write-Host ("Build succeeded ({0}{1})." -f $Configuration, $(if ($DebugFaults) { ', DEBUG_FAULTS' } else { '' }))
    $exitCode = 0
} catch {
    Write-Host "ERROR: $($_.Exception.Message)"
    $exitCode = 1
}

exit $exitCode
