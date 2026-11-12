# ClaudeEnvSetup v1.0.0

Claude Code 环境检测与静默安装工具，面向没有预装开发环境的 Windows 用户。

## 功能

- 检测 **Claude Code、Git for Windows、Node.js LTS、Python 3、VS Code** 的安装状态、版本和路径
- 显示已安装版本与最新版本；缺失项可一键下载并静默安装
- 官方源优先，失败自动回退 npmmirror 国内镜像
- 可提前选择下载目录和各组件安装目录，配置持久化
- 支持断点续传、取消、SHA256 校验（Node/VS Code）
- 已安装组件默认跳过；版本过低时可二次确认后原地升级
- 支持“仅下载”、`--dry-run` 预览、`--mock` 模拟、`--detect-json`/`--print-plan` 诊断
- GUI 单文件 exe 自带 Python 运行时，**目标电脑无需安装 Python**

## 系统要求

- Windows 10 1809+ / Windows 11，x64
- 联网（运行时下载组件安装包）
- 安装 Node.js / Git 时需要管理员权限（UAC）；Python/VS Code 默认按用户安装，不需要
- 当前构建为 x64；ARM64 设备可模拟运行，未做原生 ARM64 验证

## 使用

1. 解压 `ClaudeEnvSetup-1.0.0-win-x64.zip`，双击 `ClaudeEnvSetup.exe`
2. 等待检测完成，勾选需要安装的组件
3. 在“路径设置”中确认下载目录与安装目录
4. 点击“安装选中项”；按提示允许必要的 UAC
5. 安装完成后重开终端，使新 PATH 生效

诊断模式（可选）：

```bat
ClaudeEnvSetup-console.exe --detect-json
ClaudeEnvSetup-console.exe --print-plan
ClaudeEnvSetup.exe --mock --dry-run
```

## 资产

- `ClaudeEnvSetup.exe` — GUI 主程序
- `ClaudeEnvSetup-console.exe` — 命令行诊断/自动化版本
- `ClaudeEnvSetup-1.0.0-win-x64.zip` — 主程序 + README

SHA256：

```text
1610c58cc289c3be160903358589bf3f4a773b3b99ccaa4a80d29c61ed686fe6  ClaudeEnvSetup.exe
34a21abb1f91ab1574a7dd15b32ad1d1f93889f388576cedaa918cb66a4340e7  ClaudeEnvSetup-console.exe
a4c08151a1d51dd08bd5428a3333cd4659945d0d14ea267e970da629a11cdfa7  ClaudeEnvSetup-1.0.0-win-x64.zip
```

## 注意事项

- exe 未做代码签名，首次运行可能出现 SmartScreen 提示；请选择“更多信息 → 仍要运行”
- “静默安装”指安装器界面静默；Node/Git 系统级安装仍会弹 UAC
- Claude Code 的安装位置由 winget/npm/native 决定，不能自定义；winget 不支持“仅下载”预取
- Git/Python 官方未提供稳定的单文件 SHA256，目前做完整性检查；Node/VS Code 在获取到官方哈希时校验
- 完全离线环境无法使用本工具下载组件；需要另行准备离线安装包
- 详见仓库中的 `README.md`
