param(
    [string]$Executable = "dist\CollegeAutoSchedule\CollegeAutoSchedule.exe"
)

$ErrorActionPreference = "Stop"

$executablePath = Resolve-Path $Executable
$testLocalAppData = Join-Path $env:RUNNER_TEMP "college-auto-schedule-smoke"
$env:LOCALAPPDATA = $testLocalAppData
$process = Start-Process -FilePath $executablePath -PassThru

try {
    $lockPath = Join-Path $testLocalAppData "CollegeAutoSchedule\application.lock"
    $status = $null
    for ($attempt = 0; $attempt -lt 90; $attempt++) {
        if (Test-Path -LiteralPath $lockPath) {
            try {
                $lock = Get-Content -LiteralPath $lockPath -Raw | ConvertFrom-Json
                $status = Invoke-RestMethod -Uri "$($lock.url)/api/status" -TimeoutSec 1
                break
            }
            catch {
                # The process may have written the lock immediately before Uvicorn became ready.
            }
        }
        Start-Sleep -Milliseconds 250
    }

    if ($null -eq $status) {
        $errorLog = Join-Path $testLocalAppData "CollegeAutoSchedule\startup-error.log"
        if (Test-Path -LiteralPath $errorLog) {
            Write-Host "Desktop startup error:"
            Get-Content -LiteralPath $errorLog
        }
        throw "Packaged application did not answer /api/status (exit code: $($process.ExitCode))."
    }
    if ($process.HasExited) {
        throw "Packaged application exited unexpectedly."
    }

    $workbook = Get-Item "tests\fixtures\valid-import.xlsx"
    $activated = Invoke-RestMethod -Uri "$($lock.url)/api/imports/activate" -Method Post -Form @{ file = $workbook }
    if ($activated.versionId -ne 1) {
        throw "Canonical workbook was not activated by the packaged application."
    }

    $second = Start-Process -FilePath $executablePath -PassThru -Wait
    if ($second.ExitCode -ne 0) {
        throw "Second launch exited with code $($second.ExitCode)."
    }
    if ($process.HasExited) {
        throw "Second launch stopped the first application instance."
    }

    Stop-Process -Id $process.Id
    $process.WaitForExit()
    $process = Start-Process -FilePath $executablePath -PassThru
    $status = $null
    for ($attempt = 0; $attempt -lt 90; $attempt++) {
        try {
            $lock = Get-Content -LiteralPath $lockPath -Raw | ConvertFrom-Json
            $status = Invoke-RestMethod -Uri "$($lock.url)/api/status" -TimeoutSec 1
            if ($status.activeVersionId -eq 1) {
                break
            }
        }
        catch {
            # Wait until the restarted server is ready.
        }
        Start-Sleep -Milliseconds 250
    }
    if ($null -eq $status -or $status.activeVersionId -ne 1) {
        throw "Active import did not survive application restart."
    }
}
finally {
    if (-not $process.HasExited) {
        Stop-Process -Id $process.Id
        $process.WaitForExit()
    }
}
