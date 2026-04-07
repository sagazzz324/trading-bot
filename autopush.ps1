Write-Host "👀 Watching for changes... Ctrl+C to stop" -ForegroundColor Cyan

$watcher = New-Object System.IO.FileSystemWatcher
$watcher.Path = "C:\Users\valet\Documents\trading-bot"
$watcher.IncludeSubdirectories = $true
$watcher.EnableRaisingEvents = $true
$watcher.Filter = "*.py"

$action = {
    Start-Sleep -Seconds 2
    Set-Location "C:\Users\valet\Documents\trading-bot"
    git add .
    git commit -m "auto: cambios guardados $(Get-Date -Format 'HH:mm:ss')"
    git push
    Write-Host "✅ Push automático $(Get-Date -Format 'HH:mm:ss')" -ForegroundColor Green
}

Register-ObjectEvent $watcher "Changed" -Action $action
Register-ObjectEvent $watcher "Created" -Action $action

while ($true) { Start-Sleep -Seconds 1 }