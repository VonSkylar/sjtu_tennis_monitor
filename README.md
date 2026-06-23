# 交我办网球场工具集

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Playwright 1.44+](https://img.shields.io/badge/playwright-1.44+-green.svg)](https://playwright.dev/python/)

`sjtu_tennis_toolkit` 是面向上海交通大学“交我办”网球场预约页面的本地工具集，包含网页版空位监控器、PC/ADB 版空位监控器和定时抢场器。

## 工具一览

| 工具 | 适用场景 | 主要行为 | 启动方式 |
| --- | --- | --- | --- |
| 网页版监控器 | 通过浏览器持续检查空场 | 声光报警；仅有一个符合条件的空场时可自动下单 | `启动网页版监控器.bat` |
| PC 版监控器 | 通过桌面版“交我办”的 ADB 接口检查空场 | 声光报警，不自动下单 | `启动PC版监控器.bat` |
| 抢场器 | 在指定放场时刻尝试预约七天后的场地 | 优先尝试指定场地，再尝试其他场地并自动提交 | `启动抢场器.bat` |

网页版监控器和抢场器使用同一个本地浏览器配置目录 `browser_profile/`，登录成功后可以复用会话。程序不会要求或保存 jAccount 密码。

## 安装

要求：

- Windows
- Python 3.11 或更高版本
- Chromium（由 Playwright 安装）

在项目根目录运行：

```powershell
python -m pip install -e .
python -m playwright install chromium
```

也可以使用 `python -m pip install -r requirements.txt` 安装运行依赖。

## 启动

Windows 用户可以直接双击根目录下对应的批处理文件，也可以使用模块入口：

```powershell
# 网页版监控器
python -m sjtu_tennis_toolkit

# PC/ADB 版监控器
python -m sjtu_tennis_toolkit --pc

# 定时抢场器
python -m sjtu_tennis_toolkit --rush
```

通过 `pip install -e .` 安装后，也可以使用命令行脚本：

```powershell
sjtu-tennis
sjtu-tennis --pc
sjtu-tennis --rush
```

`--pc` 与 `--rush` 不能同时使用。

## 使用说明

### 网页版监控器

1. 选择东区网球场、胡晓明网球场或两者。胡晓明网球场可进一步选择“只要室外场”（场地1-5、8）、“只要室内场”（场地6、7）或“全部都要”；东区网球场全部为室外场，不受该选项影响。
2. 设置监控日期和时间段。日期支持单日、逗号分隔的多日或日期范围，一次最多 14 天。
3. 设置刷新间隔，最小为 10 秒。
4. 点击“开始监控”，按页面提示完成首次登录。

发现符合条件的空场后，程序会置顶窗口并循环报警。启用“唯一符合条件空场自动下单”时，只有本轮检查恰好找到一个符合条件的空场才会自动提交；出现多个候选时只报警。

### PC/ADB 版监控器

PC 版提供与网页版相同的日期、时间、场馆及胡晓明室内/室外筛选，但只报警，不自动下单。启动前请确认：

- 桌面版“交我办”可以正常运行。
- 桌面快捷方式 `交我办.lnk` 可用。
- 模拟器中的开发者选项和 USB 调试已开启。
- ADB 连接未被其他工具占用。

该模式通过 ADB 操作模拟器界面，不占用真实鼠标和键盘。

### 抢场器

抢场器用于定时尝试预约七天后的场地：

1. 选择一个一小时时段、场馆和优先场地号（1–8）。
2. 设置开始抢场时间，格式为 `HH:MM:SS`，默认为 `12:00:00`。
3. 提前点击“开始抢场”并完成登录。程序会保持预约页就绪，在指定时刻开始刷新和尝试。
4. 程序先尝试优先场地，再按场地号顺序尝试其他场地；尝试窗口为开始时间后一分钟。

提交成功或尝试结束后，浏览器会保留打开，便于检查订单和页面状态。需要关闭时点击“停止抢场”。

## 项目结构

```text
sjtu_tennis_toolkit/
├── sjtu_tennis_toolkit/
│   ├── browser/              # 网页监控与抢场自动化
│   ├── pc/                   # PC/ADB 连接和界面驱动
│   ├── gui/                  # 三种工具的 Tkinter 界面
│   ├── alarm.py              # 报警器
│   ├── config.py             # 参数解析、日期和限流状态
│   ├── constants.py          # 共享常量
│   ├── models.py             # 数据模型
│   └── __main__.py           # 统一命令入口
├── tests/                    # 单元测试
├── pyproject.toml            # Python 项目元数据
├── requirements.txt          # 运行依赖
├── 启动网页版监控器.bat
├── 启动PC版监控器.bat
└── 启动抢场器.bat
```

## 注意事项

- 请合理设置刷新频率。学校系统提示请求次数超过限制后，工具会记录本地冷却时间并阻止继续请求。
- `browser_profile/` 包含本机浏览器会话，请勿提交、公开或与他人共享。
- `rate_limit_until.txt` 和 `pc_rate_limit_until.txt` 是运行时生成的本地限流记录，不影响项目代码。
- 页面结构或桌面版“交我办”更新后，自动化操作可能需要同步适配。

## 免责声明

本项目仅供学习和校友交流。请遵守学校平台规则并控制请求频率。因使用本工具产生的账号、预约或其他后果由使用者自行承担。
