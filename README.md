# Codex 多账号并行与任务接力

一套面向 Windows Codex 桌面端的可审计工作流：用独立 `CODEX_HOME` 与 Electron 数据目录并行运行多个本机账号，并用校验过的接力包在账号之间继续未完成任务。

> Auditable Windows workflows for isolated Codex desktop profiles and verified cross-account task handoffs.

[![Skill](https://img.shields.io/badge/skill-1-2563eb)](#包含内容)
[![Windows](https://img.shields.io/badge/platform-Windows-0078d4)](docs/three-account-parallel-windows.md)
[![Explicit only](https://img.shields.io/badge/invocation-explicit--only-d97706)](#安全边界)
[![License: MIT](https://img.shields.io/badge/license-MIT-16a34a)](LICENSE)

## 这不是“实时同步”

这里有两种互补能力，不能混为一谈：

1. **并行账号隔离**：每个 Codex 实例使用不同的 `CODEX_HOME` 与 Electron `user-data-dir`，可以同时登录不同账号。
2. **任务接力**：`main-account-takeover` 只读核验来源会话、附件和项目现场，生成带哈希的接力包，再由目标账号显式接收。

接力包不会改写 Codex 数据库、自动唤醒另一账号或在账号间持续复制状态，所以本仓库不把它宣传成实时同步。

## 包含内容

| 内容 | 用途 | 核心边界 |
|---|---|---|
| [`parallel-setup/Start-CodexProfile.ps1`](parallel-setup/Start-CodexProfile.ps1) | 启动一个具有独立 Home 与浏览器数据目录的 Codex 桌面实例 | 不处理密码，不复制登录态，不强制结束已有进程 |
| [`docs/three-account-parallel-windows.md`](docs/three-account-parallel-windows.md) | 从零建立三个隔离账号及桌面快捷方式 | 每个账号首次登录必须由用户本人完成 |
| [`main-account-takeover`](skills/main-account-takeover/SKILL.md) | 在显式指定的本机 Codex 账号之间安全接力任务 | 不执行包内代码，不写账号数据库，不继承历史授权 |

## 快速开始

先阅读[三账号并行指南](docs/three-account-parallel-windows.md)，复制示例配置，并为每个账号建立独立目录：

```powershell
Copy-Item .\parallel-setup\profiles.example.json .\parallel-setup\profiles.local.json
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\parallel-setup\Start-CodexProfile.ps1 `
  -Profile account-a `
  -ConfigPath .\parallel-setup\profiles.local.json
```

`profiles.local.json` 可能暴露本机目录结构，应保存在仓库外或保持未跟踪；真实账号名称、Token、Cookie 与浏览器数据不得提交。

## 安装 Skill

正式版本请从 GitHub Releases 下载，并核对同一版本的 `SHA256SUMS.txt`。

1. 下载 `main-account-takeover-<version>.zip`。
2. 核对 SHA-256，并逐文件阅读 `SKILL.md`、脚本和权限说明。
3. 将完整目录复制到公共 Skill 位置：

   ```text
   C:\Users\<你的用户名>\.agents\skills\main-account-takeover
   ```

4. 真实账号表必须保存在 Skill 目录外。
5. 重启 Codex 后，通过 `$main-account-takeover` 显式调用。

整套仓库下载文件名为 `codex-multi-account-workflows-v0.1.1.zip`，其中同时包含并行启动模板与接力 Skill。

## 安全边界

- `main-account-takeover` 设置为 `allow_implicit_invocation: false`。
- 启动器只创建所选 profile 的空目录并启动官方已安装 AppX；不下载或安装 Codex。
- 不读取、复制或提交 `auth.json`、Token、Cookie、密码或浏览器登录态。
- 不在三个账号间共享 `CODEX_HOME` 或 Electron 数据目录。
- 多个账号写同一项目时，必须使用 Git worktree、独立副本或明确的单写者规则。
- 接力包只传递上下文和现场证明，不是实时同步，也不是账号合并。
- 历史对话中的旧授权不会随接力包自动延续。

更多信息见 [SECURITY.md](SECURITY.md)、[catalog.json](catalog.json) 和 [provenance.json](provenance.json)。

## 开发与验证

```powershell
python -X utf8 scripts/build_catalog.py
python -X utf8 scripts/validate_repo.py
python -X utf8 scripts/package_release.py --release all --verify-only
python -X utf8 -m unittest discover -s scripts/tests -v
python -X utf8 -m unittest discover -s skills/main-account-takeover/tests -v
```

