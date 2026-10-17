# Adds Saydown to the Start menu, with its icon. Run it from the Saydown folder:
#     powershell -ExecutionPolicy Bypass -File install.ps1            (Start menu)
#     powershell -ExecutionPolicy Bypass -File install.ps1 -Desktop   (and a desktop shortcut)
#     powershell -ExecutionPolicy Bypass -File install.ps1 -Startup   (and start Saydown when you sign in)
#     powershell -ExecutionPolicy Bypass -File install.ps1 -Remove    (take all of them away again)
# Saydown itself stays in this folder; the shortcuts only point at it.
param([switch]$Desktop, [switch]$Startup, [switch]$Remove)

$here = $PSScriptRoot
$places = @{
    "Start menu" = [Environment]::GetFolderPath("Programs")
    "desktop"    = [Environment]::GetFolderPath("Desktop")
    "Startup"    = [Environment]::GetFolderPath("Startup")
}

if ($Remove) {
    foreach ($place in $places.Keys) {
        $link = Join-Path $places[$place] "Saydown.lnk"
        if (Test-Path $link) { Remove-Item $link; Write-Output "Removed the $place shortcut." }
    }
    exit 0
}

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Output "Saydown needs uv, which installs its Python packages. Installing it with winget..."
    winget install --id astral-sh.uv --accept-source-agreements
    if (-not $?) { Write-Output "Could not install uv. Install it from https://docs.astral.sh/uv/ and run this again."; exit 1 }
    Write-Output "uv is installed. Open a new terminal if 'uv' is not found yet."
}

$shell = New-Object -ComObject WScript.Shell
$wanted = @("Start menu") + $(if ($Desktop) { "desktop" }) + $(if ($Startup) { "Startup" })
foreach ($place in $wanted) {
    $link = $shell.CreateShortcut((Join-Path $places[$place] "Saydown.lnk"))
    $link.TargetPath = Join-Path $env:WINDIR "System32\wscript.exe"  # runs Saydown.vbs: no console window
    $link.Arguments = '"' + (Join-Path $here "Saydown.vbs") + '"'
    $link.WorkingDirectory = $here
    $link.IconLocation = (Join-Path $here "assets\saydown.ico") + ",0"
    $link.Description = "Hold right Alt, talk, release: private dictation into any app"
    $link.Save()
    Write-Output "Added a $place shortcut."
}
Write-Output "Done. Open Saydown from the Start menu. The first start downloads about 9 GB and shows its progress."
