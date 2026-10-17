# =============================================================================
#  Android 虚拟定位 —— 一键安装脚本
#  用法：双击同目录下的「安装.bat」。
#
#  它会依次做这些事（可重复运行，已装好的会自动跳过）：
#    1. 找一个可用且带 tkinter 的 Python，没有就自动装（不需要管理员）
#    2. 建独立环境 .venv（本工具不依赖第三方 Python 包）
#    3. 下载 Android 平台工具 adb（约 10 MB）
#    4. 检查随包附带的 ADB Mock GPS 安装包
# =============================================================================

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$Root = $PSScriptRoot
$VenvPython = Join-Path $Root '.venv\Scripts\python.exe'
$AdbExe = Join-Path $Root 'runtime\platform-tools\adb.exe'
$PyMirror = 'https://mirrors.huaweicloud.com/python'
$PyVersion = '3.12.7'
$PlatformTools = 'https://dl.google.com/android/repository/platform-tools-latest-windows.zip'

function Write-Step($text) { Write-Host "`n==> $text" -ForegroundColor Cyan }
function Write-Ok($text) { Write-Host "    [OK] $text" -ForegroundColor Green }
function Write-Warn($text) { Write-Host "    [!] $text" -ForegroundColor Yellow }
function Write-Err($text) { Write-Host "    [X] $text" -ForegroundColor Red }

# 统一调用 Python：既支持 @('py','-3.12') 这种带启动器的形式，也支持 @('python')。
# 注意不能用 $arr[1..($arr.Length-1)]：长度为 1 时 1..0 是倒序范围，会取到空值导致参数错位。
function Invoke-Python {
    param([string[]]$Command, [string[]]$Arguments)
    $exe = $Command[0]
    $rest = @()
    if ($Command.Length -gt 1) {
        $rest = $Command[1..($Command.Length - 1)]
    }
    $null = & $exe @rest @Arguments 2>&1   # 屏蔽 py 启动器找不到版本时的噪音输出
    return $LASTEXITCODE
}

function Test-PythonCandidate {
    param([string[]]$Command)
    try {
        $code = Invoke-Python -Command $Command -Arguments @('-c', 'import sys, tkinter; sys.exit(0 if sys.version_info >= (3,10) else 1)')
        return ($code -eq 0)
    } catch {
        return $false
    }
}

function Resolve-Python {
    foreach ($candidate in @(@('py', '-3.13'), @('py', '-3.12'), @('python'), @('python3'))) {
        if (Test-PythonCandidate -Command $candidate) {
            return $candidate
        }
    }
    return $null
}

Write-Host ''
Write-Host '  ============================================' -ForegroundColor White
Write-Host '      Android 虚拟定位 · 安装程序' -ForegroundColor White
Write-Host '  ============================================' -ForegroundColor White

# ---------------------------------------------------------------- 1. Python --
Write-Step '第 1 步：检查 Python 环境'

if (Test-Path $VenvPython) {
    Write-Ok '已存在独立环境 .venv，跳过 Python 安装'
} else {
    $python = Resolve-Python
    if ($null -eq $python) {
        Write-Warn '电脑上没有可用的 Python，正在自动下载安装（约 25 MB，不需要管理员权限）'
        $installer = Join-Path $env:TEMP "python-$PyVersion-amd64.exe"
        $url = "$PyMirror/$PyVersion/python-$PyVersion-amd64.exe"
        Write-Host "    下载 $url"
        try {
            Invoke-WebRequest -Uri $url -OutFile $installer -TimeoutSec 600
        } catch {
            Write-Err "下载失败：$($_.Exception.Message)"
            Write-Host '    请手动安装 Python 3.12（勾选 Add to PATH）后重新运行：https://www.python.org/downloads/'
            exit 1
        }
        Write-Host '    正在静默安装（只装给当前用户）…'
        $installArgs = @('/quiet', 'InstallAllUsers=0', 'PrependPath=0', 'Include_tcltk=1',
                         'Include_pip=1', 'Include_test=0', 'SimpleInstall=1')
        Start-Process -FilePath $installer -ArgumentList $installArgs -Wait
        Write-Ok 'Python 安装完成'

        $pyExe = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'
        if (-not (Test-Path $pyExe)) {
            Write-Err "没找到安装好的 Python：$pyExe"
            exit 1
        }
        $python = @($pyExe)
    }

    Write-Ok ("使用 Python：{0}" -f ($python -join ' '))
    Write-Host '    正在创建独立环境 .venv …'
    Invoke-Python -Command $python -Arguments @('-m', 'venv', (Join-Path $Root '.venv')) | Out-Null
    if (-not (Test-Path $VenvPython)) {
        Write-Err '创建 .venv 失败'
        exit 1
    }
    Write-Ok '.venv 创建完成（本工具不需要额外 Python 包）'
}

# ------------------------------------------------------------------ 2. adb --
Write-Step '第 2 步：下载 Android 平台工具（adb）'

if (Test-Path $AdbExe) {
    Write-Ok 'adb 已存在，跳过下载'
} else {
    $zip = Join-Path $env:TEMP 'platform-tools.zip'
    $runtimeDir = Join-Path $Root 'runtime'
    New-Item -ItemType Directory -Force -Path $runtimeDir | Out-Null

    Write-Host "    下载 $PlatformTools"
    try {
        Invoke-WebRequest -Uri $PlatformTools -OutFile $zip -TimeoutSec 900
    } catch {
        Write-Err "下载失败：$($_.Exception.Message)"
        Write-Host '    请检查网络后重新运行本脚本。'
        exit 1
    }

    Write-Host '    正在解压…'
    Expand-Archive -Path $zip -DestinationPath $runtimeDir -Force
    Remove-Item $zip -Force -ErrorAction SilentlyContinue

    if (-not (Test-Path $AdbExe)) {
        Write-Err "解压后没找到 adb：$AdbExe"
        exit 1
    }
    Write-Ok 'adb 就绪'
}

& $AdbExe version | Select-Object -First 2 | ForEach-Object { Write-Host "    $_" }

# ------------------------------------------------------------------ 3. APK --
Write-Step '第 3 步：准备 ADB Mock GPS 安装包（手机端 App）'
$apk = Join-Path $Root 'vendor\ADB.Mock.GPS-release.apk'
if (-not (Test-Path $apk)) {
    New-Item -ItemType Directory -Force -Path (Join-Path $Root 'vendor') | Out-Null
    $apkUrls = @(
        'https://github.com/lesurJ/ADB-Mock-GPS/releases/download/v0.8/ADB.Mock.GPS-release.apk',
        'https://ghfast.top/https://github.com/lesurJ/ADB-Mock-GPS/releases/download/v0.8/ADB.Mock.GPS-release.apk'
    )
    foreach ($url in $apkUrls) {
        Write-Host "    下载 $url"
        try {
            Invoke-WebRequest -Uri $url -OutFile $apk -TimeoutSec 300
        } catch {
            Write-Warn "这个源失败：$($_.Exception.Message)"
        }
        if ((Test-Path $apk) -and (Get-Item $apk).Length -gt 1000KB) { break }
    }
}

if ((Test-Path $apk) -and (Get-Item $apk).Length -gt 1000KB) {
    $size = [math]::Round((Get-Item $apk).Length / 1KB, 0)
    Write-Ok "安装包就绪（$size KB）"
} else {
    Write-Err 'App 安装包下载失败'
    Write-Host '    可以手动下载后放到 vendor 目录，再重新运行本脚本：'
    Write-Host '    https://github.com/lesurJ/ADB-Mock-GPS/releases'
}

Write-Host ''
Write-Host '  ============================================' -ForegroundColor White
Write-Host '   安装完成！' -ForegroundColor Green
Write-Host '  ============================================' -ForegroundColor White
Write-Host ''
Write-Host '  接下来（手机端的准备工作）：' -ForegroundColor Yellow
Write-Host '    1. 手机「设置 → 关于手机」连续点 7 次「版本号」，开启开发者选项'
Write-Host '    2. 「设置 → 系统 → 开发者选项」里打开「USB 调试」'
Write-Host '    3. 用数据线连电脑，手机上弹出「允许 USB 调试」时点「允许」'
Write-Host '    4. 双击「启动.bat」，点界面上的「一键准备」'
Write-Host '       （它会自动安装 App、授权、并设为模拟位置应用）'
Write-Host ''
Write-Host '  详细步骤见同目录《使用说明.md》。' -ForegroundColor Yellow
Write-Host ''
