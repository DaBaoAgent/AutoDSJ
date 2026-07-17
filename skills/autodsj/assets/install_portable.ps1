param(
    [string]$ProjectPath = "$HOME\AutoDSJ",
    [switch]$InstallAudio,
    [ValidateSet("all", "claude", "codex", "hermes", "openclaw", "opencode", "shared")]
    [string[]]$Agent = @("all")
)

$ErrorActionPreference = "Stop"
$PackageRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$SkillSource = Join-Path $PackageRoot "autodsj"
$Bundle = Join-Path $PackageRoot "AutoDSJ.bundle"

if (-not (Test-Path -LiteralPath $SkillSource)) { throw "缺少技能目录：$SkillSource" }
if (-not (Test-Path -LiteralPath $Bundle)) { throw "缺少项目源码包：$Bundle" }

function Get-SkillTarget([string]$AgentName) {
    switch ($AgentName) {
        "claude" { return Join-Path $HOME ".claude\skills\autodsj" }
        "codex" {
            $CodexRoot = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $HOME ".codex" }
            return Join-Path $CodexRoot "skills\autodsj"
        }
        "hermes" { return Join-Path $HOME ".hermes\skills\autodsj" }
        "openclaw" { return Join-Path $HOME ".openclaw\skills\autodsj" }
        "opencode" {
            $OpenCodeRoot = if ($env:APPDATA) { $env:APPDATA } else { Join-Path $HOME "AppData\Roaming" }
            return Join-Path $OpenCodeRoot "opencode\skills\autodsj"
        }
        "shared" { return Join-Path $HOME ".agents\skills\autodsj" }
    }
}

$InstallAgents = if ($Agent -contains "all") {
    @("claude", "codex", "hermes", "openclaw", "opencode", "shared")
} else {
    $Agent
}

$InstalledSkills = @()
foreach ($AgentName in $InstallAgents) {
    $SkillTarget = Get-SkillTarget $AgentName
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $SkillTarget) | Out-Null
    if (Test-Path -LiteralPath $SkillTarget) { Remove-Item -LiteralPath $SkillTarget -Recurse -Force }
    Copy-Item -LiteralPath $SkillSource -Destination $SkillTarget -Recurse -Force
    $InstalledSkills += "$AgentName=$SkillTarget"
}

if (Test-Path -LiteralPath $ProjectPath) {
    throw "项目目录已存在，请改用 -ProjectPath 指定空目录：$ProjectPath"
}
git clone $Bundle $ProjectPath
python -m venv (Join-Path $ProjectPath ".venv")
$Python = Join-Path $ProjectPath ".venv\Scripts\python.exe"
& $Python -m pip install --upgrade pip
& $Python -m pip install -r (Join-Path $ProjectPath "requirements.txt")
if ($InstallAudio) {
    & $Python -m pip install -r (Join-Path $ProjectPath "requirements-audio.txt")
    & $Python -m pip install --no-deps speakerlab==0.0.6
}

Write-Host 'Installation completed.'
Write-Host ('Project: ' + $ProjectPath)
Write-Host ('Skills: ' + ($InstalledSkills -join '; '))
$CliPath = Join-Path $ProjectPath 'autodsj.py'
Write-Host ('Next: ' + $Python + ' ' + $CliPath + ' set-key --dashscope YOUR_KEY')
Write-Host ('Then: ' + $Python + ' ' + $CliPath + ' doctor')
