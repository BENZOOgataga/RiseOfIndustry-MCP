#Requires -Version 5.1
<#
.SYNOPSIS
    Read-only heartbeat sampler and A/B comparison for gate E3 (PRD 17, 21 T-12, 22 E3).

.DESCRIPTION
    Sample mode (default): reads <exchange>\heartbeat.json every -IntervalSeconds for -Minutes and writes
      <OutDir>\perf-<label>-<timestamp>.csv   one row per sample
      <OutDir>\perf-<label>-<timestamp>.json  summary used by -Compare
    The exchange directory is -ExchangeDir, else $env:ROI_MCP_EXCHANGE_DIR, else %LOCALAPPDATA%\RoiMcp.
    The heartbeat is opened read-only with full sharing; nothing in the exchange directory is written.
    With -SampleProcess the game's private bytes are read with Get-Process (read-only) for T-12.

    Run it once with the observer active (-Label observer_on) and twice with the observer disabled by
    the kill switch (-Label observer_off). A disabled observer still publishes frame statistics in the
    heartbeat (state "disabled"); that is what the off runs measure.

    The heartbeat's frame statistics cover a rolling window (frame_stats.window_s, 60 s). Totals
    (frames, frames > 50 ms, average frame time) are therefore computed over non-overlapping windows:
    a sample is used only when its heartbeat time is at least window_s after the previously used one.
    Maxima (p99, observer max) use every sample. Capture statistics are de-duplicated by (family, utc).

    Compare mode: -Compare <on.json> <off1.json> [<off2.json>] evaluates
      PERF-1  observer per-frame work: max capture slice <= 2 ms and observer p99 <= 3 ms
      PERF-2  max state capture main-thread time <= 50 ms
      PERF-3  average frame time on vs off differs by <= 2 %
      PERF-5  managed allocation per state capture < 2 MiB
      PERF-7  sizes: state <= 5 MiB, static <= 4 MiB, history <= 5 MiB (heartbeat families), heartbeat <= 16 KiB
      PERF-10 frames > 50 ms (on) <= the maximum of the off runs
    and prints PASS / FAIL / INCONCLUSIVE per criterion, plus T-12 private bytes when sampled.
    The result is written to <OutDir>\perf-compare-<timestamp>.json. Exit 0 when all criteria pass.

.PARAMETER Label
    Run label used in file names, e.g. observer_on or observer_off.

.PARAMETER Minutes
    Sampling duration (default 10).

.PARAMETER IntervalSeconds
    Sampling interval (default 5).

.PARAMETER SampleProcess
    Also record the game's private bytes (Get-Process, read-only).

.PARAMETER OutDir
    Output folder (default <repo>\.local\perf).

.PARAMETER ExchangeDir
    Override the exchange directory.

.PARAMETER Compare
    Summary JSON of the observer-on run, followed by one or two observer-off summaries.

.EXAMPLE
    pwsh -File scripts\perf-report.ps1 -Label observer_on -Minutes 10 -SampleProcess

.EXAMPLE
    pwsh -File scripts\perf-report.ps1 -Compare .local\perf\perf-observer_on-*.json .local\perf\perf-observer_off-a.json .local\perf\perf-observer_off-b.json
#>
[CmdletBinding(DefaultParameterSetName = 'Sample')]
param(
    [Parameter(ParameterSetName = 'Sample')]
    [string]$Label = 'run',
    [Parameter(ParameterSetName = 'Sample')]
    [ValidateRange(0.01, 1440)]
    [double]$Minutes = 10,
    [Parameter(ParameterSetName = 'Sample')]
    [ValidateRange(0.2, 3600)]
    [double]$IntervalSeconds = 5,
    [Parameter(ParameterSetName = 'Sample')]
    [switch]$SampleProcess,
    [Parameter(ParameterSetName = 'Sample')]
    [string]$ExchangeDir,
    [string]$OutDir,
    [Parameter(ParameterSetName = 'Compare', Mandatory = $true)]
    [string[]]$Compare,
    [Parameter(ParameterSetName = 'Compare', ValueFromRemainingArguments = $true)]
    [string[]]$MoreFiles
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot 'lib\RoiMcpCommon.psm1') -Force -DisableNameChecking

$Inv = [System.Globalization.CultureInfo]::InvariantCulture
$MiB = 1048576.0
$Limits = [ordered]@{
    perf1_max_slice_ms    = 2.0
    perf1_observer_p99_ms = 3.0
    perf2_capture_ms      = 50.0
    perf3_avg_frame_pct   = 2.0
    perf5_alloc_bytes     = 2 * 1048576
    perf7_state_bytes     = 5 * 1048576
    perf7_static_bytes    = 4 * 1048576
    perf7_history_bytes   = 5 * 1048576
    perf7_heartbeat_bytes = 16 * 1024
}

function Get-Prop {
    param($Object, [string]$Name)
    if ($null -eq $Object) { return $null }
    if ($Object -is [System.Collections.IDictionary]) {
        if ($Object.Contains($Name)) { return $Object[$Name] }
        return $null
    }
    $p = $Object.PSObject.Properties[$Name]
    if ($null -eq $p) { return $null }
    return $p.Value
}

function Get-Num {
    param($Value)
    if ($null -eq $Value -or ($Value -is [string] -and $Value -eq '')) { return $null }
    return [double]$Value
}

function Get-Stat {
    <# min / max / avg / p50 / p95 of a list of numbers (nulls ignored). #>
    param([object[]]$Values)
    $v = @($Values | Where-Object { $null -ne $_ } | ForEach-Object { [double]$_ } | Sort-Object)
    if ($v.Count -eq 0) { return [ordered]@{ n = 0; min = $null; max = $null; avg = $null; p50 = $null; p95 = $null } }
    $sum = 0.0; foreach ($x in $v) { $sum += $x }
    $pick = { param($p) $v[[Math]::Min($v.Count - 1, [Math]::Max(0, [int][Math]::Ceiling($p * $v.Count) - 1))] }
    return [ordered]@{ n = $v.Count; min = $v[0]; max = $v[$v.Count - 1]; avg = [Math]::Round($sum / $v.Count, 4); p50 = (& $pick 0.5); p95 = (& $pick 0.95) }
}

function ConvertTo-Utc {
    param($Value)
    if ($null -eq $Value -or $Value -eq '') { return $null }
    if ($Value -is [datetime]) { return $Value.ToUniversalTime() }
    return [datetime]::Parse([string]$Value, $Inv, [System.Globalization.DateTimeStyles]::AdjustToUniversal -bor [System.Globalization.DateTimeStyles]::AssumeUniversal)
}

function Get-SafeLabel {
    param([string]$Text)
    $s = ($Text -replace '[^A-Za-z0-9_.-]', '_')
    if ($s.Length -eq 0) { $s = 'run' }
    return $s
}

function Read-HeartbeatSample {
    param([string]$Path, [switch]$WithProcess)
    $row = [ordered]@{
        utc = [datetime]::UtcNow.ToString('o'); heartbeat_written_utc = $null; read_error = $null; heartbeat_bytes = $null
        state = $null; paused = $null; speed_level = $null; time_scale = $null
        window_s = $null; frames = $null
        frame_ms_p50 = $null; frame_ms_p95 = $null; frame_ms_p99 = $null; frame_ms_avg = $null; frames_over_50ms = $null
        observer_ms_p99 = $null; observer_ms_max = $null; observer_ms_max_without_gc = $null; observer_ticks_with_gc = $null
        last_capture_family = $null; last_capture_utc = $null; last_capture_main_thread_ms = $null
        last_capture_alloc_bytes = $null; last_capture_gc_count_delta = $null; last_capture_size_bytes = $null
        last_capture_max_slice_ms = $null; last_capture_slices = $null; last_capture_serialize_ms = $null
        effective_interval_s = $null; degraded = $null
        state_size_bytes = $null; static_size_bytes = $null; history_size_bytes = $null
        private_bytes = $null; working_set_bytes = $null
    }
    if ($WithProcess) {
        $procs = Get-RoiGameProcess
        if ($procs.Count -gt 0) {
            $row.private_bytes = [long]$procs[0].PrivateMemorySize64
            $row.working_set_bytes = [long]$procs[0].WorkingSet64
        }
    }
    try {
        if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { $row.read_error = 'missing'; return $row }
        $row.heartbeat_bytes = (New-Object System.IO.FileInfo($Path)).Length
        $hb = Read-RoiJsonFileShared -Path $Path
    } catch {
        $row.read_error = 'unreadable: ' + $_.Exception.Message
        return $row
    }
    $d = Get-Prop $hb 'data'
    $written = Get-Prop $d 'written_utc'
    if ($null -eq $written) { $written = Get-Prop $hb 'written_utc' }
    $wu = ConvertTo-Utc $written
    if ($null -ne $wu) { $row.heartbeat_written_utc = $wu.ToString('o') }
    $row.state = Get-Prop $d 'state'
    $row.paused = Get-Prop $d 'paused'
    $row.speed_level = Get-Prop $d 'speed_level'
    $row.time_scale = Get-Prop $d 'time_scale'
    $row.effective_interval_s = Get-Prop $d 'effective_interval_s'
    $row.degraded = Get-Prop $d 'degraded'
    $fs = Get-Prop $d 'frame_stats'
    foreach ($k in 'window_s', 'frames', 'frame_ms_p50', 'frame_ms_p95', 'frame_ms_p99', 'frame_ms_avg', 'frames_over_50ms', 'observer_ms_p99', 'observer_ms_max', 'observer_ms_max_without_gc', 'observer_ticks_with_gc') {
        $row[$k] = Get-Prop $fs $k
    }
    $lc = Get-Prop $d 'last_capture'
    if ($null -ne $lc) {
        $row.last_capture_family = Get-Prop $lc 'family'
        $lu = ConvertTo-Utc (Get-Prop $lc 'utc')
        if ($null -ne $lu) { $row.last_capture_utc = $lu.ToString('o') }
        $row.last_capture_main_thread_ms = Get-Prop $lc 'main_thread_ms'
        $row.last_capture_alloc_bytes = Get-Prop $lc 'alloc_bytes_approx'
        $row.last_capture_gc_count_delta = Get-Prop $lc 'gc_count_delta'
        $row.last_capture_size_bytes = Get-Prop $lc 'size_bytes'
        $row.last_capture_max_slice_ms = Get-Prop $lc 'max_slice_ms'
        $row.last_capture_slices = Get-Prop $lc 'slices'
        $row.last_capture_serialize_ms = Get-Prop $lc 'serialize_ms'
    }
    $fam = Get-Prop $d 'families'
    foreach ($f in 'state', 'static', 'history') {
        $row[$f + '_size_bytes'] = Get-Prop (Get-Prop $fam $f) 'size_bytes'
    }
    return $row
}

function Get-RunSummary {
    param([object[]]$Rows, [string]$RunLabel, [hashtable]$Meta)
    $ok = @($Rows | Where-Object { $null -eq $_.read_error })
    $warnings = New-Object System.Collections.Generic.List[string]

    # Distinct heartbeats, in heartbeat time order.
    $distinct = New-Object System.Collections.Generic.List[object]
    $seen = @{}
    $stale = 0
    foreach ($r in $ok) {
        $key = [string]$r.heartbeat_written_utc
        if ($seen.ContainsKey($key)) { $stale++; continue }
        $seen[$key] = $true
        $distinct.Add($r)
    }
    $ordered = @($distinct | Sort-Object { if ($_.heartbeat_written_utc) { [datetime]::Parse($_.heartbeat_written_utc, $Inv).ToUniversalTime() } else { [datetime]::MinValue } })

    # Non-overlapping frame-stat windows.
    $last = $null
    $frames = 0L; $over = 0L; $weighted = 0.0; $windows = 0
    foreach ($r in $ordered) {
        $n = Get-Num $r.frames
        $w = Get-Num $r.window_s
        if ($null -eq $n -or $n -le 0 -or $null -eq $w -or $null -eq $r.heartbeat_written_utc) { continue }
        $t = [datetime]::Parse($r.heartbeat_written_utc, $Inv).ToUniversalTime()
        if ($null -ne $last -and ($t - $last).TotalSeconds -lt $w) { continue }
        $last = $t
        $windows++
        $frames += [long]$n
        $over += [long](Get-Num $r.frames_over_50ms)
        $avg = Get-Num $r.frame_ms_avg
        if ($null -ne $avg) { $weighted += $avg * $n }
    }
    if ($windows -lt 2) { $warnings.Add("only $windows non-overlapping frame window(s); run longer than 2 x window_s") }

    # Unique captures.
    $caps = @{}
    foreach ($r in $ok) {
        if ($null -eq $r.last_capture_utc) { continue }
        $k = "$($r.last_capture_family)|$($r.last_capture_utc)"
        if (-not $caps.ContainsKey($k)) { $caps[$k] = $r }
    }
    $capRows = @($caps.Values)
    $stateCaps = @($capRows | Where-Object { $_.last_capture_family -eq 'state' })
    $byFamily = [ordered]@{}
    foreach ($g in ($capRows | Group-Object -Property last_capture_family)) { $byFamily[[string]$g.Name] = $g.Count }

    $states = [ordered]@{}
    foreach ($g in ($ok | Group-Object -Property state)) { $states[[string]$g.Name] = $g.Count }
    $speeds = @($ok | ForEach-Object { $_.speed_level } | Where-Object { $null -ne $_ } | Sort-Object -Unique)
    $pausedCount = @($ok | Where-Object { $_.paused -eq $true }).Count
    if ($pausedCount -gt 0) { $warnings.Add("$pausedCount sample(s) taken while paused") }
    if ($speeds.Count -gt 1) { $warnings.Add('speed level changed during the run: ' + ($speeds -join ', ')) }
    if ($stale -gt 0) { $warnings.Add("$stale sample(s) repeated an unchanged heartbeat") }
    $errors = @($Rows | Where-Object { $null -ne $_.read_error }).Count
    if ($errors -gt 0) { $warnings.Add("$errors sample(s) could not read the heartbeat") }

    $priv = Get-Stat @($Rows | ForEach-Object { $_.private_bytes })
    $privRows = @($Rows | Where-Object { $null -ne $_.private_bytes })
    return [ordered]@{
        schema           = 'roi-mcp/perf-run-summary'
        schema_version   = '1.0.0'
        label            = $RunLabel
        meta             = $Meta
        samples          = @($Rows).Count
        samples_ok       = $ok.Count
        distinct_heartbeats = $distinct.Count
        read_errors      = $errors
        states           = $states
        speed_levels     = $speeds
        paused_samples   = $pausedCount
        frame            = [ordered]@{
            windows                 = $windows
            frames                  = $frames
            avg_frame_ms            = $(if ($frames -gt 0) { [Math]::Round($weighted / $frames, 4) } else { $null })
            frames_over_50ms_total  = $over
            frame_ms_p95_max        = (Get-Stat @($ok | ForEach-Object { $_.frame_ms_p95 })).max
            frame_ms_p99_max        = (Get-Stat @($ok | ForEach-Object { $_.frame_ms_p99 })).max
            frame_ms_avg_samples    = Get-Stat @($ok | ForEach-Object { $_.frame_ms_avg })
            observer_ms_p99_max     = (Get-Stat @($ok | ForEach-Object { $_.observer_ms_p99 })).max
            observer_ms_max_max     = (Get-Stat @($ok | ForEach-Object { $_.observer_ms_max })).max
            observer_ms_max_without_gc_max = (Get-Stat @($ok | Where-Object { $null -ne $_.observer_ms_max_without_gc } | ForEach-Object { $_.observer_ms_max_without_gc })).max
            observer_ticks_with_gc_max = (Get-Stat @($ok | Where-Object { $null -ne $_.observer_ticks_with_gc } | ForEach-Object { $_.observer_ticks_with_gc })).max
        }
        captures         = [ordered]@{
            unique                  = $capRows.Count
            by_family               = $byFamily
            max_slice_ms_max        = (Get-Stat @($capRows | ForEach-Object { $_.last_capture_max_slice_ms })).max
            state_main_thread_ms    = Get-Stat @($stateCaps | ForEach-Object { $_.last_capture_main_thread_ms })
            state_alloc_bytes       = Get-Stat @($stateCaps | ForEach-Object { $_.last_capture_alloc_bytes })
            gc_count_delta_total    = [long](@($capRows | ForEach-Object { Get-Num $_.last_capture_gc_count_delta } | Where-Object { $null -ne $_ }) | Measure-Object -Sum).Sum
            note                    = 'Only the last capture per heartbeat is visible; captures between two samples can be missed. Use -IntervalSeconds <= the capture interval.'
        }
        sizes            = [ordered]@{
            state_bytes_max     = (Get-Stat @($ok | ForEach-Object { $_.state_size_bytes })).max
            static_bytes_max    = (Get-Stat @($ok | ForEach-Object { $_.static_size_bytes })).max
            history_bytes_max   = (Get-Stat @($ok | ForEach-Object { $_.history_size_bytes })).max
            heartbeat_bytes_max = (Get-Stat @($ok | ForEach-Object { $_.heartbeat_bytes })).max
        }
        effective_interval_s = Get-Stat @($ok | ForEach-Object { $_.effective_interval_s })
        degraded_samples = @($ok | Where-Object { $_.degraded -eq $true }).Count
        process          = [ordered]@{
            sampled             = $privRows.Count -gt 0
            private_bytes       = $priv
            private_bytes_first = $(if ($privRows.Count) { $privRows[0].private_bytes } else { $null })
            private_bytes_last  = $(if ($privRows.Count) { $privRows[$privRows.Count - 1].private_bytes } else { $null })
        }
        warnings         = $warnings.ToArray()
    }
}

function ConvertTo-CsvRow {
    <# Culture-invariant text for CSV (numbers with '.', booleans as true/false). #>
    param($Row)
    $o = [ordered]@{}
    foreach ($p in $Row.PSObject.Properties) {
        $val = $p.Value
        if ($null -eq $val) { $o[$p.Name] = '' }
        elseif ($val -is [bool]) { $o[$p.Name] = $(if ($val) { 'true' } else { 'false' }) }
        elseif ($val -is [double] -or $val -is [single] -or $val -is [decimal]) { $o[$p.Name] = ([double]$val).ToString('R', $Inv) }
        elseif ($val -is [datetime]) { $o[$p.Name] = $val.ToUniversalTime().ToString('o', $Inv) }
        else { $o[$p.Name] = [string]::Format($Inv, '{0}', $val) }
    }
    return [pscustomobject]$o
}

function Expand-CompareFiles {
    <# Accepts "-Compare a b c" and "-Compare a,b,c" (also a single comma-joined string). #>
    param([string[]]$Items)
    $out = New-Object System.Collections.Generic.List[string]
    foreach ($i in $Items) {
        if ([string]::IsNullOrWhiteSpace($i)) { continue }
        if (-not (Test-Path -LiteralPath $i -PathType Leaf) -and $i.Contains(',')) {
            foreach ($part in $i.Split(',')) { if ($part.Trim()) { $out.Add($part.Trim()) } }
        } else {
            $out.Add($i)
        }
    }
    foreach ($f in $out) { if (-not (Test-Path -LiteralPath $f -PathType Leaf)) { throw "Summary file not found: $f" } }
    return , $out.ToArray()
}

function New-Verdict {
    param([string]$Id, [string]$Status, [string]$Detail, $Measured, $Limit)
    return [ordered]@{ id = $Id; status = $Status; measured = $Measured; limit = $Limit; detail = $Detail }
}

function Test-Le {
    param($Value, $Limit, [switch]$Strict)
    if ($null -eq $Value) { return 'INCONCLUSIVE' }
    if ($Strict) { if ([double]$Value -lt [double]$Limit) { return 'PASS' } else { return 'FAIL' } }
    if ([double]$Value -le [double]$Limit) { return 'PASS' } else { return 'FAIL' }
}

function Invoke-Compare {
    param([string[]]$Files, [string]$Out)
    if ($Files.Count -lt 2 -or $Files.Count -gt 3) { throw '-Compare needs <on.json> <off1.json> [<off2.json>].' }
    $on = Read-RoiJsonFileShared -Path $Files[0]
    $offs = @($Files[1..($Files.Count - 1)] | ForEach-Object { Read-RoiJsonFileShared -Path $_ })
    $notes = New-Object System.Collections.Generic.List[string]
    $v = New-Object System.Collections.Generic.List[object]

    $onStates = Get-Prop $on 'states'
    if ($null -eq (Get-Prop $onStates 'ready')) { $notes.Add('the observer-on run has no samples in state "ready"') }
    foreach ($o in $offs) {
        $st = Get-Prop $o 'states'
        if ($null -eq (Get-Prop $st 'disabled')) { $notes.Add("off run '$(Get-Prop $o 'label')' has no samples in state ""disabled"" (kill switch)") }
        if ((@(Get-Prop $o 'speed_levels') -join ',') -ne (@(Get-Prop $on 'speed_levels') -join ',')) { $notes.Add("speed levels differ between the on run and '$(Get-Prop $o 'label')'") }
    }
    foreach ($r in @($on) + $offs) { foreach ($w in @(Get-Prop $r 'warnings')) { if ($w) { $notes.Add("$(Get-Prop $r 'label'): $w") } } }
    $onFrame = Get-Prop $on 'frame'
    $onCaps = Get-Prop $on 'captures'
    $onSizes = Get-Prop $on 'sizes'

    # PERF-1
    $slice = Get-Prop $onCaps 'max_slice_ms_max'
    $p99 = Get-Prop $onFrame 'observer_ms_p99_max'
    $s1 = Test-Le $slice $Limits.perf1_max_slice_ms
    $s2 = Test-Le $p99 $Limits.perf1_observer_p99_ms
    $st = if ($s1 -eq 'FAIL' -or $s2 -eq 'FAIL') { 'FAIL' } elseif ($s1 -eq 'PASS' -and $s2 -eq 'PASS') { 'PASS' } else { 'INCONCLUSIVE' }
    $v.Add((New-Verdict 'PERF-1' $st 'max capture slice <= 2 ms; observer per-frame p99 <= 3 ms' ([ordered]@{ max_slice_ms = $slice; observer_ms_p99_max = $p99; observer_ms_max = (Get-Prop $onFrame 'observer_ms_max_max') }) ([ordered]@{ max_slice_ms = $Limits.perf1_max_slice_ms; observer_ms_p99 = $Limits.perf1_observer_p99_ms })))

    # PERF-2
    $cap = Get-Prop (Get-Prop $onCaps 'state_main_thread_ms') 'max'
    $v.Add((New-Verdict 'PERF-2' (Test-Le $cap $Limits.perf2_capture_ms) 'max state capture main-thread time' $cap $Limits.perf2_capture_ms))

    # PERF-3
    $avgOn = Get-Prop $onFrame 'avg_frame_ms'
    $offAvgs = @($offs | ForEach-Object { Get-Prop (Get-Prop $_ 'frame') 'avg_frame_ms' } | Where-Object { $null -ne $_ })
    if ($null -eq $avgOn -or $offAvgs.Count -eq 0) {
        $v.Add((New-Verdict 'PERF-3' 'INCONCLUSIVE' 'average frame time unavailable' $null $Limits.perf3_avg_frame_pct))
    } else {
        $sum = 0.0; foreach ($a in $offAvgs) { $sum += [double]$a }
        $avgOff = $sum / $offAvgs.Count
        $pct = if ($avgOff -gt 0) { [Math]::Round(100.0 * ([double]$avgOn - $avgOff) / $avgOff, 3) } else { $null }
        $status = if ($null -eq $pct) { 'INCONCLUSIVE' } elseif ([Math]::Abs($pct) -le $Limits.perf3_avg_frame_pct) { 'PASS' } else { 'FAIL' }
        $v.Add((New-Verdict 'PERF-3' $status '|avg_on - mean(avg_off)| / mean(avg_off) in %' ([ordered]@{ avg_on_ms = $avgOn; avg_off_ms = $offAvgs; diff_pct = $pct }) $Limits.perf3_avg_frame_pct))
    }

    # PERF-5
    $alloc = Get-Prop (Get-Prop $onCaps 'state_alloc_bytes') 'max'
    $v.Add((New-Verdict 'PERF-5' (Test-Le $alloc $Limits.perf5_alloc_bytes -Strict) 'max managed allocation per state capture (bytes, < 2 MiB)' ([ordered]@{ max = $alloc; p50 = (Get-Prop (Get-Prop $onCaps 'state_alloc_bytes') 'p50') }) $Limits.perf5_alloc_bytes))

    # PERF-7
    $sz = [ordered]@{
        state     = Get-Prop $onSizes 'state_bytes_max'
        static    = Get-Prop $onSizes 'static_bytes_max'
        history   = Get-Prop $onSizes 'history_bytes_max'
        heartbeat = Get-Prop $onSizes 'heartbeat_bytes_max'
    }
    $parts = @(
        (Test-Le $sz.state $Limits.perf7_state_bytes), (Test-Le $sz.static $Limits.perf7_static_bytes),
        (Test-Le $sz.history $Limits.perf7_history_bytes), (Test-Le $sz.heartbeat $Limits.perf7_heartbeat_bytes))
    $st = if ($parts -contains 'FAIL') { 'FAIL' } elseif ($parts -contains 'INCONCLUSIVE') { 'INCONCLUSIVE' } else { 'PASS' }
    $v.Add((New-Verdict 'PERF-7' $st 'observed sizes (the 3x-sample-count condition needs a scaled fixture)' $sz ([ordered]@{ state = $Limits.perf7_state_bytes; static = $Limits.perf7_static_bytes; history = $Limits.perf7_history_bytes; heartbeat = $Limits.perf7_heartbeat_bytes })))

    # PERF-10
    $onOver = Get-Prop $onFrame 'frames_over_50ms_total'
    $offOver = @($offs | ForEach-Object { Get-Prop (Get-Prop $_ 'frame') 'frames_over_50ms_total' } | Where-Object { $null -ne $_ })
    if ($null -eq $onOver -or $offOver.Count -eq 0) {
        $v.Add((New-Verdict 'PERF-10' 'INCONCLUSIVE' 'frames > 50 ms unavailable' $null $null))
    } else {
        $mx = ($offOver | Measure-Object -Maximum).Maximum
        $mn = ($offOver | Measure-Object -Minimum).Minimum
        if ($offOver.Count -lt 2) { $notes.Add('PERF-10 needs two observer-off runs; compared against a single baseline') }
        $v.Add((New-Verdict 'PERF-10' (Test-Le $onOver $mx) 'frames > 50 ms (on) within the range of the off runs' ([ordered]@{ on = $onOver; off_range = @($mn, $mx) }) $mx))
    }
    $durOn = Get-Prop (Get-Prop $on 'frame') 'frames'
    foreach ($o in $offs) {
        $f = Get-Prop (Get-Prop $o 'frame') 'frames'
        if ($durOn -and $f -and ([Math]::Abs([double]$durOn - [double]$f) / [double]$f) -gt 0.1) { $notes.Add("frame counts differ by > 10 % between the on run and '$(Get-Prop $o 'label')' (different run lengths or speeds)") }
    }

    # T-12 (informational)
    $t12 = [ordered]@{
        on  = Get-Prop (Get-Prop $on 'process') 'private_bytes'
        off = @($offs | ForEach-Object { Get-Prop (Get-Prop $_ 'process') 'private_bytes' })
    }

    $statuses = @($v | ForEach-Object { $_.status })
    $overall = if ($statuses -contains 'FAIL') { 'FAIL' } elseif ($statuses -contains 'INCONCLUSIVE') { 'INCONCLUSIVE' } else { 'PASS' }
    $result = [ordered]@{
        schema   = 'roi-mcp/perf-compare'
        on       = $Files[0]
        off      = @($Files[1..($Files.Count - 1)])
        overall  = $overall
        criteria = $v.ToArray()
        t12_private_bytes = $t12
        notes    = $notes.ToArray()
    }
    Write-RoiJsonFile -Path $Out -InputObject $result -Depth 8
    Write-Host ''
    Write-Host ('{0,-8} {1,-13} {2}' -f 'ID', 'RESULT', 'MEASURED')
    foreach ($c in $v) {
        $m = ($c.measured | ConvertTo-Json -Compress -Depth 4)
        Write-Host ('{0,-8} {1,-13} {2}' -f $c.id, $c.status, $m)
    }
    foreach ($n in $notes) { Write-Host "NOTE: $n" }
    Write-Host "Overall: $overall"
    Write-Host "Written: $Out"
    return $overall
}

# ---------------------------------------------------------------------------

$exitCode = 1
try {
    if ([string]::IsNullOrWhiteSpace($OutDir)) { $OutDir = [System.IO.Path]::Combine((Get-RoiRepoRoot), '.local', 'perf') }
    $OutDir = Get-RoiFullPath $OutDir
    [void][System.IO.Directory]::CreateDirectory($OutDir)
    $stamp = [datetime]::Now.ToString('yyyyMMdd-HHmmss', $Inv)

    if ($PSCmdlet.ParameterSetName -eq 'Compare') {
        $files = Expand-CompareFiles -Items (@($Compare) + @($MoreFiles | Where-Object { $_ }))
        $overall = Invoke-Compare -Files $files -Out ([System.IO.Path]::Combine($OutDir, "perf-compare-$stamp.json"))
        $exitCode = $(if ($overall -eq 'PASS') { 0 } else { 1 })
    } else {
        $exchange = if ([string]::IsNullOrWhiteSpace($ExchangeDir)) { Get-RoiExchangeDir } else { Get-RoiFullPath $ExchangeDir }
        $hbPath = [System.IO.Path]::Combine($exchange, 'heartbeat.json')
        $safe = Get-SafeLabel $Label
        $csvPath = [System.IO.Path]::Combine($OutDir, "perf-$safe-$stamp.csv")
        $jsonPath = [System.IO.Path]::Combine($OutDir, "perf-$safe-$stamp.json")
        Write-Host "Heartbeat: $hbPath (read-only)"
        Write-Host ([string]::Format($Inv, "Sampling every {0} s for {1} min, label '{2}'{3}", $IntervalSeconds, $Minutes, $Label, $(if ($SampleProcess) { ', with process private bytes (read-only)' } else { '' })))
        $rows = New-Object System.Collections.Generic.List[object]
        $start = [datetime]::UtcNow
        $end = $start.AddMinutes($Minutes)
        $next = $start
        try {
            while ([datetime]::UtcNow -lt $end) {
                $rows.Add([pscustomobject](Read-HeartbeatSample -Path $hbPath -WithProcess:$SampleProcess))
                $next = $next.AddSeconds($IntervalSeconds)
                $wait = ($next - [datetime]::UtcNow).TotalMilliseconds
                if ($wait -gt 0 -and $next -lt $end) { Start-Sleep -Milliseconds ([int]$wait) }
                elseif ($next -ge $end) { break }
            }
        } finally {
            if ($rows.Count -gt 0) {
                $rows | ForEach-Object { ConvertTo-CsvRow $_ } | Export-Csv -LiteralPath $csvPath -NoTypeInformation -Encoding UTF8
                $meta = @{
                    exchange_dir = $exchange; interval_s = $IntervalSeconds; minutes = $Minutes
                    start_utc = $start.ToString('o'); end_utc = [datetime]::UtcNow.ToString('o'); csv = $csvPath
                }
                $summary = Get-RunSummary -Rows $rows.ToArray() -RunLabel $Label -Meta $meta
                Write-RoiJsonFile -Path $jsonPath -InputObject $summary -Depth 8
                Write-Host "CSV:     $csvPath"
                Write-Host "Summary: $jsonPath"
                Write-Host ([string]::Format($Inv, "Samples {0} (ok {1}), windows {2}, avg frame {3} ms, frames>50ms {4}, unique captures {5}", $summary.samples, $summary.samples_ok, $summary.frame.windows, $summary.frame.avg_frame_ms, $summary.frame.frames_over_50ms_total, $summary.captures.unique))
                foreach ($w in $summary.warnings) { Write-Host "WARNING: $w" }
            }
        }
        $okCount = @($rows | Where-Object { $null -eq $_.read_error }).Count
        if ($okCount -eq 0) { Write-Host 'ERROR: no heartbeat sample could be read.' }
        $exitCode = $(if ($okCount -gt 0) { 0 } else { 1 })
    }
} catch {
    Write-Host "ERROR: $($_.Exception.Message)"
    $exitCode = 1
}
exit $exitCode
