# Claude Code 环境配置助手（ClaudeEnvSetup）

一个 Windows 桌面小工具：检测安装 Claude Code 所需的组件及其版本，缺失时可在界面中选择下载目录、安装目录，并静默下载/静默安装。

构建产物：`dist\ClaudeEnvSetup.exe`（GUI）；`dist\ClaudeEnvSetup-console.exe`（诊断）。

## 功能

- 检测五个组件：**Claude Code**、**Git for Windows**、**Node.js LTS**、**Python 3**、**VS Code**
- 显示状态：已安装 / 可升级 / 版本过低 / 未安装 / 版本未知 / 检测失败
- 显示已安装版本、最新版本、安装位置
- 官方源优先，失败自动切换 npmmirror 国内镜像
- 下载目录、各组件安装目录可提前选择并持久化
- 支持断点续传、SHA256 校验（Node/VS Code）、取消下载
- 已安装组件默认跳过；版本过低时可二次确认后原地升级
- 提供 CLI 诊断模式，便于批量检查

## 运行要求

- Windows 10 1809+ / Windows 11，x64 或 ARM64
- 无需安装 Python，exe 自带运行时
- 联网（首次检测最新版本、下载安装包）

## 使用

1. 双击 `ClaudeEnvSetup.exe`。
2. 程序自动检测本机环境，并异步获取各组件最新版本。
3. 勾选缺失项（版本过低项默认勾选；普通“可升级”默认不勾选）。
4. 在“路径设置”中确认下载目录和各组件安装目录。
5. 点击“安装选中项”开始下载并静默安装；也可点击“仅下载”只保存安装包。
6. 安装完成后程序会刷新 PATH 并复检；**已打开的终端需要重开**才能使用新 PATH。

### 权限说明

“静默安装”指安装器界面静默，**不能消除管理员权限提示**：

| 组件 | 是否需要 UAC | 说明 |
|---|---|---|
| Node.js | 是 | MSI 系统级安装 |
| Git for Windows | 是 | Inno Setup 系统级安装 |
| Python | 默认否 | 按当前用户安装；若选 Program Files 或检测到系统级旧版本则提权 |
| VS Code | 否 | 使用 User Setup，仅支持用户可写目录 |
| Claude Code | 否 | winget/npm 按用户安装；winget 自身可能按需提权 |

## 组件与安装方式

| 组件 | 检测来源 | 静默安装 |
|---|---|---|
| Claude Code | PATH、`~/.local/bin`、npm 全局、WindowsApps、winget 包目录 | `winget install Anthropic.ClaudeCode`，失败后 npm 兜底 |
| Git | `HKLM\SOFTWARE\GitForWindows`、PATH、常见目录 | `Git-*-64-bit.exe /VERYSILENT ... /DIR="..."` |
| Node.js | `HKLM\SOFTWARE\Node.js`、PATH、常见目录 | `msiexec /i node.msi /qn INSTALLDIR="..."` |
| Python | PEP 514 注册表、`py -0p`、PATH、常见目录 | `python-*.exe /quiet InstallAllUsers=... TargetDir="..."` |
| VS Code | PATH、卸载项、常见目录 | `VSCodeUserSetup-*.exe /VERYSILENT ... /DIR="..."` |

最新版本下载源：

- Node.js：`nodejs.org/dist` → `registry.npmmirror.com/-/binary/node`
- Git：GitHub Releases → `registry.npmmirror.com/-/binary/git-for-windows`
- Python：`python.org` → `registry.npmmirror.com/-/binary/python`
- VS Code：`update.code.visualstudio.com`
- Claude Code：npm registry（版本元数据）→ npmmirror；安装走 winget/npm

Claude Code 的原生安装脚本 `claude.ai/install.ps1` 在部分网络区域会跳转 `app-unavailable-in-region`，因此本工具优先使用 winget，失败后使用 npm。原生安装仅作为手动入口，不在界面中自动执行。

## 诊断模式（控制台版）

构建后会同时生成 `ClaudeEnvSetup-console.exe`，用于自动化和排障：

```bat
rem 输出 JSON 检测结果（退出码 0=Claude Code 已安装）
ClaudeEnvSetup-console.exe --detect-json

rem 输出到文件（GUI 版无控制台时使用）
ClaudeEnvSetup.exe --detect-json --out D:\env-report.json

rem 只打印计划，不下载不安装
ClaudeEnvSetup-console.exe --print-plan

rem 模拟缺失环境，检查计划与界面
ClaudeEnvSetup-console.exe --mock --print-plan

rem 跳过远端请求，使用内置固定版本
ClaudeEnvSetup-console.exe --print-plan --no-network

rem 指定下载源/配置文件
ClaudeEnvSetup-console.exe --source mirror --print-plan
ClaudeEnvSetup.exe --config D:\my-config.json
```

其他参数：`--dry-run`（GUI 预览模式）、`--debug`。

> `--mock` 只用于演示/测试检测结果，**不要据此执行真实安装**；需要安全预览请同时加 `--dry-run`。

## 配置与日志

- 配置：`%APPDATA%\ClaudeEnvSetup\config.json`
- 日志：`%LOCALAPPDATA%\ClaudeEnvSetup\logs\`
- 版本缓存：`%LOCALAPPDATA%\ClaudeEnvSetup\cache\releases.json`
- 默认下载目录：`%USERPROFILE%\Downloads\ClaudeEnvSetup`

配置中的 `python_version` 可设为 `"3.15"`（解析最新 3.15.x）或完整版本（如 `"3.13.9"`）。

## 构建

要求：Python 3.10.x（本机使用 `D:\python-3.10.11-amd64\python.exe`），能访问 PyPI。

```bat
cd Pre_Claude
build\build.bat
```

脚本会创建 `.venv-build`、安装 PyInstaller、运行单元测试，并输出：

- `dist\ClaudeEnvSetup.exe`（GUI，无控制台）
- `dist\ClaudeEnvSetup-console.exe`（诊断）

如构建解释器路径不同，请修改 `build\build.bat` 顶部的 `BUILD_PY`。

## 常见问题

**SmartScreen 提示“Windows 已保护你的电脑”**
exe 未做代码签名。点击“更多信息 → 仍要运行”。企业分发建议使用代码签名证书。

**杀毒软件误报**
PyInstaller 单文件会出现临时解包行为，可能被启发式误报。已禁用 UPX；可用控制台版排障，或向厂商提交误报。

**安装后 `claude` 命令找不到**
重开终端。仍找不到时检查 `%APPDATA%\npm\claude.cmd` 或 `%LOCALAPPDATA%\Microsoft\WinGet\Packages` 是否在 PATH 中，也可运行 `claude doctor`。

**Claude Code 在 Git 自定义目录下找不到 Bash**
在 Claude Code 配置中设置：
```json
{ "env": { "CLAUDE_CODE_GIT_BASH_PATH": "D:\\Git\\bin\\bash.exe" } }
```

**winget 安装 Claude Code 失败**
可能受区域/商店源限制。工具会自动尝试 npm（需要 Node 22+）。也可以手动执行：
```bat
winget install --id Anthropic.ClaudeCode -e --source winget
npm install -g @anthropic-ai/claude-code
```

**Node.js 低于 22**
仅影响 npm 兜底安装 Claude Code；Claude Code 本体和 winget 安装不依赖 Node。工具会标记“版本过低”，可点击“升级”原地覆盖。

**Python 升级会弹 UAC**
检测到系统级 Python（安装在 Program Files 或非用户目录）时，会按“所有用户”升级以确保权限一致；按用户安装的 Python 不会弹 UAC。

**ARM64**
工具会按架构选择安装包；目前仅在 x64 真机验证，ARM64 未做完整回归。

## 已知限制

- 未做代码签名；首次运行可能有 SmartScreen 提示。
- Claude Code 的安装位置由 winget/npm/native 决定，不能自定义；winget 不支持“仅下载”预取。
- Git/Python 官方未提供稳定的单文件 SHA256，v1 仅做大小/完整性检查；Node/VS Code 在获取到官方哈希时校验 SHA256。
- 已安装组件的升级是覆盖安装，失败时原版本可能已被部分修改，请保留安装器日志。
- 镜像同步可能滞后，工具会优先使用版本更高的源/内置固定版本。
