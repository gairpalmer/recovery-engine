# Daily recovery cycle: pull -> compute -> render -> push to GitHub Pages.
# Run by Windows Task Scheduler each morning. Logs to logs\daily.log.
$root = "C:\Users\rwgpa\recovery-engine"
Set-Location $root
New-Item -ItemType Directory -Force -Path "$root\logs" | Out-Null
Start-Transcript -Path "$root\logs\daily.log" -Append | Out-Null
try {
    & "$root\.venv\Scripts\python.exe" run.py 60
    & "$root\.venv\Scripts\python.exe" dashboard.py
    git add docs/index.html
    git commit -m ("daily update {0}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm'))
    git push origin main
}
catch { "ERROR: $_" }
finally { Stop-Transcript | Out-Null }
