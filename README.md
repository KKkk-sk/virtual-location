# 虚拟定位工具（iOS + Android）

用 Python 在 Windows 上给 iPhone / Android 手机设置模拟定位，带图形界面，支持沿 400 米跑道按配速"跑步"。

> **仅供开发测试、演示与隐私保护使用。** 请勿用于伪造考勤、欺骗平台风控等用途，详见文末[免责声明](#免责声明)。

## 功能

- **图形界面**：选城市、填经纬度、收藏常用位置，点一下就切
- **iPhone 无需越狱**：走苹果官方开发者通道（DVT 定位模拟），手机上不装任何程序
- **Android 自动化配置**：自动安装开源模拟定位 App、授权、设为模拟位置应用，无需手动点手机菜单
- **毫秒级切换**：iPhone 端用常驻守护进程保持隧道，切换坐标只要 50~70 ms，且中间不掉线
- **安卓端可回读校验**：设置后自动读回设备实际坐标，确认真的生效
- **校园跑模式**：沿真实跑道几何按配速移动，默认 6:00/km
- **一键安装**：没有 Python 也能装（脚本自动下载安装，不需要管理员权限）

## 两种方案的区别

| | iPhone | Android |
|---|---|---|
| 手机端需要装东西 | ❌ 不需要 | ✅ 需要装一个开源 App |
| 能否脱离电脑 | ❌ 必须连着数据线 | ✅ 设好后可以拔线 |
| 需要额外驱动 | ✅ 苹果驱动（脚本自动装） | ❌ 不需要 |
| 需要管理员权限 | 安装驱动时需要 | ❌ 不需要 |
| 前置条件 | 开启「开发者模式」并重启手机 | 开启「USB 调试」 |

## 快速开始

### 方式一：下载现成压缩包（推荐给普通用户）

| 平台 | 直接下载 |
|---|---|
| 苹果手机 | [virtual-location-iphone.zip](https://github.com/KKkk-sk/virtual-location/releases/latest/download/virtual-location-iphone.zip) |
| 安卓手机 | [virtual-location-android.zip](https://github.com/KKkk-sk/virtual-location/releases/latest/download/virtual-location-android.zip) |

也可以点 **Code → Download ZIP** 下载源码包，解压后进入 `iphone/` 或 `android/` 目录。

1. 下载并解压到硬盘
2. 进入对应目录（苹果 → `iphone/`，安卓 → `android/`）
3. 双击 **`安装.bat`**（会联网下载 Python、依赖、平台工具）
4. 双击 **`启动.bat`** 打开界面
5. 手机端按 [使用说明.md](使用说明.md) 开启开发者选项 / 开发者模式

> ⚠️ 必须先把 ZIP **完整解压到硬盘**再双击 `.bat`，不要在压缩包里直接运行。

### 方式二：从源码运行（开发者）

需要 Python 3.10 ~ 3.13（带 tkinter）。

```powershell
# iPhone
cd iphone
python -m venv .venv
.venv\Scripts\python.exe -m pip install pymobiledevice3
.venv\Scripts\python.exe fakegps_gui.py

# Android（无第三方依赖）
cd android
python -m venv .venv
.venv\Scripts\python.exe android_gui.py
```

## 校园跑模式

沿电子科技大学清水河校区田径场按固定配速循环移动，跑道参数取自 OpenStreetMap 实测轮廓
（`leisure=track`，way 808264789）：

| 项 | 值 |
|---|---|
| 跑道中心 | `30.7462577, 103.9292042` |
| 长轴朝向 | -77.82°（近似南北向） |
| 标准参数 | 直道 84.39 m + 弯道半径 36.80 m |
| 单圈长度 | 400.00 m（第 1 道） |
| 6:00/km 配速 | 2.778 m/s，单圈 144 秒 |

实测校验：沿跑道移动速度 `2.7758 m/s = 6.004 分/公里`，与设定值偏差 -0.07%。

改配速：`--pace 5:30`；改圈数：`--laps 5`；反向跑：`--reverse`。
没有界面时也可用命令行：

```powershell
.venv\Scripts\python.exe fakegps.py run --pace 6:00 --laps 10   # iPhone
.venv\Scripts\python.exe fakegps_android.py run --pace 6:00     # Android
```

## 工作原理

### iPhone：开发者通道

iOS 17+ 的定位模拟走 DVT 服务，需要一条到手机的 RSD 隧道。
本项目用 [pymobiledevice3](https://github.com/doronz88/pymobiledevice3) 的 `--userspace` 模式——
纯 Python 用户态网络栈，官方说明 *"NO root/admin is required"*，所以**不需要管理员、不需要 Xcode**。

三条关键实现细节：

1. **常驻守护进程**（`loc_daemon.py`）：隧道和 DVT 会话只建立一次，之后切换坐标只是一次 DTX 调用
   （50~70 ms）。早期版本每次切换都重建隧道（10~20 秒），那段空窗期手机就掉回真实定位——
   这是"定位不稳"的根因。
2. **会话保持**：`simulate-location set` 设完会停在 `input("Press ENTER to exit>")`，
   所以用常驻进程 + 本地 socket 协议（`127.0.0.1:47823`，一行一个 JSON）来驱动。
3. **保活**：每 20 秒重发一次当前坐标，抵消设备侧偶发的模拟状态丢失。

### Android：模拟位置提供者

Android 从 6.0 起规定：改定位必须由一个 App 扮演"模拟位置提供者"，普通程序无法直接改。
本项目通过 `adb` 自动完成：

- 安装 [ADB Mock GPS](https://github.com/lesurJ/ADB-Mock-GPS)（Apache-2.0，minSdk 31）
- `pm grant` 授予定位与通知权限
- `appops set ... android:mock_location allow` 设为模拟位置应用（等效于手动点菜单）
- `am broadcast -a com.adbmockgps.SET_LOCATION --es lat .. --es lon .. -f 0x01000000` 下发坐标

`-f 0x01000000`（`FLAG_INCLUDE_STOPPED_PACKAGES`）是关键，少了它广播到不了未运行的 App。

## 项目结构

```
├── iphone/                   iPhone 端
│   ├── core.py               核心：设备、开发者模式、镜像挂载、守护进程客户端
│   ├── loc_daemon.py         常驻守护进程（隧道 + DVT + 轨迹循环）
│   ├── fakegps.py            命令行
│   ├── fakegps_gui.py        图形界面
│   ├── track.py              400 米跑道几何
│   ├── install_ios.ps1       一键安装
│   └── install_apple_driver.ps1  苹果驱动补装
├── android/                  Android 端
│   ├── core_android.py       adb 封装
│   ├── fakegps_android.py    命令行
│   ├── android_gui.py        图形界面
│   ├── track.py              400 米跑道几何
│   └── install_android.ps1   一键安装
├── 使用说明.md                给普通用户的完整图文步骤
└── LICENSE                   MIT
```

## 常见问题

**iPhone：设备栏一直显示未检测到**
数据线问题最常见（很多 USB-C 线只能充电不能传数据）。其次确认手机已点「信任」、
驱动已装好（运行 `安装.bat` 会检查）。

**iPhone：报"开发者模式未开启"**
手机「设置 → 隐私与安全性 → 开发者模式」打开后**必须重启手机**。

**iPhone：报镜像未挂载**
界面点「挂载镜像」。若一直卡住，通常是 `raw.githubusercontent.com` 被网络拦截，
可参考 [使用说明.md](使用说明.md) 里的镜像下载方案。

**Android：adb 提示未授权**
手机解锁后点「允许 USB 调试」，或在开发者选项里撤销授权后重插。

**位置设了但地图没变**
地图类 App 有缓存，杀掉重开；地图视野也不会自动跳到蓝点，需要点右下角定位按钮。

## 致谢

- [pymobiledevice3](https://github.com/doronz88/pymobiledevice3) — iOS 设备通信，MIT
- [ADB Mock GPS](https://github.com/lesurJ/ADB-Mock-GPS) — Android 模拟位置提供者，Apache-2.0
- [OpenStreetMap](https://www.openstreetmap.org/) — 跑道几何数据，ODbL

## 免责声明

- 本项目仅供**学习、开发测试、演示、隐私保护**等正当用途。
- 用于伪造考勤打卡、突破地域限制、欺骗平台风控等行为，通常违反相关服务的使用条款，
  部分场景可能涉及法律责任，**后果由使用者自行承担**。
- iOS 与 Android 都提供了"位置是否由软件模拟"的检测接口，风控严格的应用可以识别并拒绝服务，
  本项目不保证"隐身"。
- 本项目依赖第三方库与第三方 App，其安全性由其各自维护者负责。

## License

[MIT](LICENSE)
