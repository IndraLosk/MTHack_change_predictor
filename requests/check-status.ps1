$ErrorActionPreference = "Stop"

$BASE = "http://localhost:8000"
$TEMP_CFG = Join-Path $env:TEMP "mthack_check_cfg.json"
$TEMP_STOP = Join-Path $env:TEMP "mthack_check_stop.json"
$pass = 0
$fail = 0

function Check-Response {
    param(
        [string]$Name,
        [string]$Url,
        [string]$Method = "GET",
        [bool]$SendBody = $false,
        [string]$BodyFile = "",
        [int]$Expected = 200,
        [string]$MustContain = ""
    )

    $args = @("-s", "-o", "-", "-w", "`n%{http_code}", "-X", $Method, $Url)
    if ($SendBody) {
        $args += @("-H", "Content-Type: application/json", "--data-binary", "@$BodyFile")
    }

    $output = & curl.exe @args
    $lines = @($output -split "`n")
    $code = $lines[-1].Trim()
    $body = ($lines[0..($lines.Length - 2)] -join "`n")

    $ok = $code -eq $Expected
    if ($ok -and $MustContain -ne "" -and -not $body.Contains($MustContain)) {
        $ok = $false
    }

    $global:pass = $pass
    $global:fail = $fail
    if ($ok) {
        $global:pass++
        Write-Output ("PASS  {0}  [{1}]" -f $Name, $code)
    }
    else {
        $global:fail++
        Write-Output ("FAIL  {0}  expected={1} got={2} body={3}" -f $Name, $Expected, $code, $body.Substring(0, [Math]::Min(120, $body.Length)))
    }
}

Write-Output "=== MTHack backend status checks ==="

Check-Response "health"        "$BASE/health"
Check-Response "root"          "$BASE/"
Check-Response "cells"         "$BASE/api/cells" -MustContain "G6CellNav00"
Check-Response "config get"    "$BASE/api/config"

$cfg = '{"targetHost":"backend","targetPort":9201,"units":[{"unitId":1099984,"intervalMs":1000,"autoGenerate":true,"cells":[]}]}'
$cfg | Out-File -Encoding ascii $TEMP_CFG
$stop = '{"targetHost":"backend","targetPort":9201,"units":[]}'
$stop | Out-File -Encoding ascii $TEMP_STOP

Check-Response "config post (start stream)" "$BASE/api/config" -Method "POST" -SendBody $true -BodyFile $TEMP_CFG

Start-Sleep -Seconds 3

$decoded = & curl.exe -s "$BASE/api/ndtp/decoded?limit=5"
if ($decoded -match '"total":\s*[1-9]') {
    $pass++
    Write-Output "PASS  decoded has rows"
}
else {
    $fail++
    Write-Output "FAIL  decoded has rows  body=$decoded"
}

Check-Response "config post (stop stream)" "$BASE/api/config" -Method "POST" -SendBody $true -BodyFile $TEMP_STOP

Remove-Item $TEMP_CFG, $TEMP_STOP -Force -ErrorAction SilentlyContinue

Write-Output ("`nRESULT: {0} passed, {1} failed" -f $pass, $fail)
if ($fail -gt 0) { exit 1 }