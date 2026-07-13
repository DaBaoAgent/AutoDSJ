# 跨电脑部署

当用户要求把第10集验证过的完整方法迁移到另一台 Windows 电脑时读取本文件。

## 复用包内容

- `dy-workflow/`：Codex 技能，复制到 `%USERPROFILE%\.codex\skills\dy-workflow`。
- `DaobaoAI-DY.bundle`：包含完整 Git 历史的项目源码包。
- `安装到另一台电脑.ps1`：安装技能、克隆项目并创建 Python 虚拟环境。

不得打包 `config/secrets.json`、API Key、原片、角色声纹参考音频或人物照片。

## 安装

在复用包目录运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\安装到另一台电脑.ps1
```

默认项目安装到 `%USERPROFILE%\DaobaoAI-DY`，技能安装到 `%USERPROFILE%\.codex\skills\dy-workflow`。可用 `-ProjectPath "D:\DaobaoAI-DY"` 改项目位置；需要 CAM++ 时增加 `-InstallAudio`。

随后执行：

```powershell
cd "$HOME\DaobaoAI-DY"
.\.venv\Scripts\python.exe dy.py set-key --dashscope "<新电脑自己的Key>"
.\.venv\Scripts\python.exe dy.py doctor
```

按新电脑实际位置重新设置素材文件夹、Qwen 参考音频和人脸/声纹库。不要复制旧电脑的加密 secrets，因为其加密上下文不可移植。

## 方法完整性

另一台电脑仍必须遵守唯一管线：完整场景地图、字幕/剧本混合检索、CAM++ 可选声纹、父段全局序列解码、30～60 帧选择性视觉复核、广告硬禁区、画面去重、统一 -16 LUFS 等响、发布交付门禁和 `_DY工作文件` 自动归档。不得因为换电脑而降级为旧匹配器或密集全片视觉扫描。
