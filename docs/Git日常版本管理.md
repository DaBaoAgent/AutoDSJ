# Git 日常版本管理

项目使用 `main` 作为稳定主分支。每完成一轮可运行的修改就提交一次，不要把一天的多轮改动堆进同一个提交。

## 保存一个迭代版本

```powershell
git status --short
git add -A
git commit -m "fix: 修正文案指定原片的精确匹配"
```

常用提交前缀：

- `feat:` 新功能
- `fix:` 修复问题
- `refactor:` 内部改造但不改变功能
- `test:` 测试改动
- `docs:` 文档改动
- `chore:` 依赖、构建或配置维护

## 标记当天稳定版

一天可以标记多个稳定版本，编号从 1 递增：

```powershell
git tag -a v2026.07.11-1 -m "2026-07-11 第1个稳定版"
git tag -a v2026.07.11-2 -m "2026-07-11 第2个稳定版"
```

## 查看和恢复

```powershell
git log --oneline --decorate --graph -20
git diff HEAD~1..HEAD
git switch -c recover/<说明> <提交ID>
```

恢复旧版本时优先新建 `recover/*` 分支检查，不直接使用 `git reset --hard`，避免丢失当天尚未提交的工作。

## 不进入 Git 的内容

API 密钥、本机路径配置、运行任务、缓存、素材视频、成片、配音文件、前端依赖和 FFmpeg 二进制都由 `.gitignore` 排除。
