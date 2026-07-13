param(
    [string]$ProjectPath = "$HOME\DaobaoAI-DY",
    [switch]$InstallAudio
)

$ErrorActionPreference = "Stop"
$PackageRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$SkillSource = Join-Path $PackageRoot "dy-workflow"
$Bundle = Join-Path $PackageRoot "DaobaoAI-DY.bundle"
$SkillTarget = Join-Path $HOME ".codex\skills\dy-workflow"

if (-not (Test-Path -LiteralPath $SkillSource)) { throw "缺少技能目录：$SkillSource" }
if (-not (Test-Path -LiteralPath $Bundle)) { throw "缺少项目源码包：$Bundle" }

New-Item -ItemType Directory -Force -Path (Split-Path -Parent $SkillTarget) | Out-Null
if (Test-Path -LiteralPath $SkillTarget) { Remove-Item -LiteralPath $SkillTarget -Recurse -Force }
Copy-Item -LiteralPath $SkillSource -Destination $SkillTarget -Recurse -Force

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

Write-Host "安装完成。"
Write-Host "项目：$ProjectPath"
Write-Host "技能：$SkillTarget"
Write-Host "下一步：& '$Python' '$ProjectPath\dy.py' set-key --dashscope '<KEY>'"
Write-Host "然后运行：& '$Python' '$ProjectPath\dy.py' doctor"
