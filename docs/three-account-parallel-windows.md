# Windows 三账号 Codex 并行运行

## 原理

Codex 桌面端至少有两类必须分开的状态：

- `CODEX_HOME`：会话、设置、Skill 发现与 Codex 后端状态；
- Electron `user-data-dir`：桌面壳层的浏览器存储、窗口状态与登录会话。

只改其中一个目录不算完整隔离。三个 profile 必须同时使用三套不同目录。

## 前提

- Windows 10/11；
- 已从官方渠道安装 `OpenAI.Codex` AppX；
- PowerShell 5.1 或更高版本；
- 用户本人分别完成每个账号的首次登录。

本仓库不会下载 Codex，不读取密码、验证码或已有浏览器资料。

## 配置三个 profile

将 `parallel-setup/profiles.example.json` 复制到仓库外，例如 `D:\CodexProfiles\profiles.local.json`，再把根目录改成你自己的绝对路径。

每个 profile 的根目录下会使用：

```text
<root>\codex-home
<root>\electron-data
```

不要把真实配置放进 Skill 目录、Git 仓库、OneDrive 或其他自动同步目录。

## 启动与首次登录

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\parallel-setup\Start-CodexProfile.ps1 `
  -Profile account-a `
  -ConfigPath D:\CodexProfiles\profiles.local.json
```

对 `account-b` 和 `account-c` 重复执行。第一次打开每个实例后，由用户本人确认窗口身份并登录对应账号。不要让自动化输入密码、验证码或恢复密钥。

启动器执行以下操作：

1. 验证配置只包含允许字段、profile 名称唯一且目录互不重叠；
2. 为所选 profile 创建独立的 `codex-home` 与 `electron-data`；
3. 从已安装的 `OpenAI.Codex` AppX 定位官方可执行文件；
4. 为新进程设置 `CODEX_HOME` 与 `CODEX_ELECTRON_USER_DATA_PATH`，并传入 `--user-data-dir`。

它不会杀死、重启或修改其他 Codex 实例。

## 创建桌面快捷方式

每个快捷方式的目标可以是：

```text
C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe
```

参数示例：

```text
-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "D:\CodexMultiAccount\Start-CodexProfile.ps1" -Profile account-a -ConfigPath "D:\CodexProfiles\profiles.local.json"
```

为 B、C 两个账号只改 `-Profile`。不要在参数中放 Token、Cookie、密码或代理凭据。

## 并行工作规则

- 三个账号可同时运行，但同一 Windows 用户仍能访问彼此可读的本地文件；这不是安全沙箱。
- 同一项目同一时间只允许一个账号写入，除非每个账号使用不同 Git worktree 或完整独立副本。
- 不要让多个实例共享 `codex-home`、`electron-data`、缓存、接收账本或临时接力目录。
- 使用 `$main-account-takeover` 接力任务前，明确来源账号、目标账号及两者的 `CODEX_HOME`。
- 接力包完成后仍需由目标账号显式验证和接收；它不会自动同步后续改动。

## 卸载与恢复

关闭某个 profile 的窗口并不等于删除其数据。需要移除 profile 时，先备份并确认对应目录，再由用户单独处理；本仓库不提供自动删除命令。

