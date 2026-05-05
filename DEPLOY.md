# VPS 部署指南 (Windows Server 2023)

> 给 VPS 上的 Claude / 操作员阅读. 任务: 把这个项目从 GitHub 部署到 Windows Server 2023, 让 daemon 常驻并自动推送 Discord 信号.

## 系统要求

- Windows Server 2023 (其他 Windows 也行)
- 美国 IP (访问 yfinance / Discord 通畅)
- 至少 2GB 内存, 5GB 磁盘
- RDP 访问

## 项目背景 (1 段话)

这是一个美股 AI/科技/存储/光模块监控系统. 24 个标的, 用 Range Filter + 三层评级 (Tier 1+2+3), 仅多策略, 入场 ≥B 评级, 出场 ≥B 评级, 满仓时新信号比最低分高 12 分才换仓. 信号通过 Discord webhook 推送. 回测 Calmar 18-21, 1 年模拟 +254%.

## 部署步骤

### Step 1: 安装基础软件

```powershell
# 用 winget (Windows Server 2022+ 自带)
winget install Python.Python.3.11
winget install Git.Git

# 关掉 PowerShell 重开, 让 PATH 生效

# 验证
python --version  # 必须 3.11.x
git --version
```

如果 winget 不能用, 手动下:
- Python: https://www.python.org/downloads/  (3.11 系列, 安装时勾 "Add to PATH")
- Git: https://git-scm.com/download/win

### Step 2: clone 仓库

```powershell
cd C:\
mkdir monitor
cd monitor

# 替换成你的 GitHub URL (private repo, 第一次会要求登录)
git clone https://github.com/<YOUR_USERNAME>/<REPO_NAME>.git ai-monitor
cd ai-monitor
```

### Step 3: 创建 venv + 装依赖

```powershell
# 如果第一次运行 Activate.ps1 报错 "无法运行脚本":
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser -Force

python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install --upgrade pip
pip install -r requirements.txt

# 应该看到一堆包安装成功. 包括: pandas, requests, yfinance, plotly, streamlit, typer 等
```

### Step 4: 设环境变量 (Discord Webhook URL)

⚠️ **找用户要 Discord Webhook URL** (不要写到任何文件里, 不要提交到 git).

```powershell
# 永久设置 (User 级)
[System.Environment]::SetEnvironmentVariable(
    'DISCORD_WEBHOOK_URL',
    'https://discord.com/api/webhooks/.../...',
    'User'
)

# 关掉当前 PowerShell, 重新打开让生效
# 验证:
echo $env:DISCORD_WEBHOOK_URL
# 应该输出 URL
```

### Step 5: 拉数据 (5-10 分钟)

```powershell
cd C:\monitor\ai-monitor
.\.venv\Scripts\Activate.ps1

# 拉 24 个美股的全部数据 (1h/2h/4h/8h/1d), 全部含盘前盘后
python -m src.cli fetch-stocks
```

预期输出: 一个表格, 每个标的 × 5 个周期 = 120 行, 全 OK 状态.

### Step 6: 测试 + 初始化

```powershell
# 测试 Discord webhook 通不通
python -m src.cli notify-test
# 如果成功, 用户的 Discord 频道会收到 2 条消息 (一条文字 + 一条 STX 样卡)

# 初始化信号状态 (静默, 记录基线时间, 不会推历史信号)
python -m src.cli notify-init
# 输出: "✓ 初始化完成: 记录 24 个标的的当前状态"

# 干跑确认状态正确 (预期 0 信号被推送)
python -m src.cli notify-once --dry-run
# 输出: "✓ 推送 BUY: 0 条 / SELL: 0 条"
```

### Step 7: 设置开机自启 (Windows Task Scheduler)

```powershell
# 创建启动脚本
@'
$env:DISCORD_WEBHOOK_URL = [System.Environment]::GetEnvironmentVariable('DISCORD_WEBHOOK_URL', 'User')
cd C:\monitor\ai-monitor
.\.venv\Scripts\python.exe -m src.cli notify-daemon --interval-min 30
'@ | Out-File -FilePath C:\monitor\start_daemon.ps1 -Encoding utf8

# 注册任务
$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-ExecutionPolicy Bypass -WindowStyle Hidden -File C:\monitor\start_daemon.ps1"

$trigger = New-ScheduledTaskTrigger -AtStartup

# 用当前用户跑 (这样能访问到 User 级环境变量)
$principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -RunLevel Highest `
    -LogonType S4U

$settings = New-ScheduledTaskSettingsSet `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 5) `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Days 365)

Register-ScheduledTask `
    -TaskName "AI-Tech-Monitor" `
    -Action $action `
    -Trigger $trigger `
    -Principal $principal `
    -Settings $settings `
    -Force

# 立即启动
Start-ScheduledTask -TaskName "AI-Tech-Monitor"

# 等 30 秒, 验证在跑
Start-Sleep -Seconds 30
Get-ScheduledTask -TaskName "AI-Tech-Monitor" | Get-ScheduledTaskInfo
# LastTaskResult 应该是 0x41301 (运行中) 或 0 (成功)
```

### Step 8: 验证常驻

```powershell
# 看 Python 进程在不在
Get-Process python -ErrorAction SilentlyContinue
# 应该至少有 1 个 python.exe 进程, 占用几十 MB 内存

# 30 分钟后 (或下一个 4H bar 收盘后), 去用户的 Discord 频道看
# 如果有新信号, 应该能看到推送
```

## 常见问题

### Q1: pip install 失败

```
A: 如果是 numpy/pandas 报错, 可能是 Python 不是 3.11.
   重新装 Python 3.11.x, 删 .venv, 重新 venv + install.
```

### Q2: notify-test 没收到消息

```
A: 检查环境变量是否生效:
   - 关掉 PowerShell 窗口, 完全重新打开
   - echo $env:DISCORD_WEBHOOK_URL 看输出
   - 如果空, 用 [System.Environment]::SetEnvironmentVariable 重新设
```

### Q3: 任务计划程序启动后没运行

```
A: 打开"任务计划程序"图形界面 (taskschd.msc)
   找到 AI-Tech-Monitor 任务
   右键 → "运行"
   看历史记录, 找错误原因 (一般是路径错或权限错)
```

### Q4: yfinance 拉数据失败

```
A: 美国 IP 一般没问题. 如果偶发失败:
   重试: python -m src.cli fetch-stocks
   或者: 减少 STOCK_SYMBOLS 数量分批拉
```

### Q5: VPS 时区是 UTC, 显示信号时间不对

```
A: 不需要改时区. 代码里 hardcode +8h offset, 显示的就是 CST.
   Get-TimeZone 看, 如果是 UTC 也没关系.
```

## 后续维护

### 更新代码

```powershell
cd C:\monitor\ai-monitor
git pull
# 如果改了依赖:
.\.venv\Scripts\pip.exe install -r requirements.txt
# 重启 daemon:
Stop-ScheduledTask -TaskName "AI-Tech-Monitor"
Start-ScheduledTask -TaskName "AI-Tech-Monitor"
```

### 看日志

```powershell
# 任务计划程序里看历史
# 或: 修改 start_daemon.ps1 加日志输出:
# .\.venv\Scripts\python.exe -m src.cli notify-daemon --interval-min 30 *>> C:\monitor\daemon.log
```

### 停止

```powershell
Stop-ScheduledTask -TaskName "AI-Tech-Monitor"
# 或彻底删除:
Unregister-ScheduledTask -TaskName "AI-Tech-Monitor" -Confirm:$false
```

### 复现历史信号 (可选)

```powershell
# 把过去 30 天所有 ≥B 信号按时间顺序推到 Discord (一次性)
python -m src.cli notify-replay --days 30 --confirm
```

## 完成判断

部署成功的标志:
1. Discord 收到测试消息 ✓
2. .signal_state.json 存在且有 24 个标的状态 ✓
3. AI-Tech-Monitor 任务已注册并运行 ✓
4. python.exe 进程在跑 ✓
5. 重启 VPS 后任务自动启动 ✓ (可选验证)

## 关键文件 / 命令快速参考

```
项目目录:           C:\monitor\ai-monitor
启动脚本:           C:\monitor\start_daemon.ps1
状态文件:           C:\monitor\ai-monitor\.signal_state.json
数据目录:           C:\monitor\ai-monitor\data\stocks\
任务计划名:         AI-Tech-Monitor

关键命令:
  python -m src.cli notify-test          - 测试 webhook
  python -m src.cli notify-init          - 初始化状态
  python -m src.cli notify-once          - 扫一次推一次
  python -m src.cli notify-daemon        - 常驻 daemon
  python -m src.cli notify-replay        - 复现历史信号
  python -m src.cli fetch-stocks         - 重新拉数据
  python -m src.cli backtest <SYM>       - 回测某标的
```

## 给执行 Claude 的具体指引

按 Step 1 → Step 8 顺序执行, 每步都验证成功才进下一步.

**关键: Step 4 必须找用户要 Discord Webhook URL** —— 不要硬编码, 不要写文件里, 只用环境变量.

Step 5 拉数据时如果看到某些标的失败 (如 ALAB / ARM / GEV 等新 IPO 数据少), 那是正常的, 跳过即可, 不影响其他标的.

Step 7 注册任务计划时如果遇到权限问题, 改用 `RunAsUser` 模式或用 `interactive` LogonType.

部署完成后, 请告诉用户:
1. ✓ 部署完成
2. 新信号会自动推 Discord
3. 任何代码更新后跑 git pull + 重启 daemon
4. 给出查看任务状态的命令
