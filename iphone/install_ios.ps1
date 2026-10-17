# =============================================================================
#  iPhone 虚拟定位 —— 一键安装脚本
#  用法：双击同目录下的「安装.bat」，不要在 PowerShell 里手动改参数。
#
#  它会依次做这些事（每步都可以重复运行，已经装好会自动跳过）：
#    1. 找一个可用且带 tkinter 的 Python，没有就自动装一个（不需要管理员）
#    2. 在程序目录里建独立环境 .venv，装上 pymobiledevice3
#    3. 检查苹果手机驱动（Apple Mobile Device Service），缺了就装上
#    4. 自检：能不能看到手机
# =============================================================================

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$Root = $PSScriptRoot
$VenvPython = Join-Path $Root '.venv\Scripts\python.exe'
$Mirror = 'https://pypi.tuna.tsinghua.edu.cn/simple'
$PyMirror = 'https://mirrors.huaweicloud.com/python'
$PyVersion = '3.12.7'

function Write-Step($text) { Write-Host "`n==> $text" -ForegroundColor Cyan }
function Write-Ok($text) { Write-Host "    [OK] $text" -ForegroundColor Green }
function Write-Warn($text) { Write-Host "    [!] $text" -ForegroundColor Yellow }
function Write-Err($text) { Write-Host "    [X] $text" -ForegroundColor Red }

function Test-PythonCandidate {
    param([string[]]$Command)
    # pymobiledevice3 在 3.10~3.13 上最稳；3.14+ 部分依赖可能还没有预编译包，
    # 所以这里显式要求 < 3.14，不满足就自动装 3.12。
    try {
        $code = Invoke-Python -Command $Command -Arguments @('-c', 'import sys, tkinter; sys.exit(0 if (3,10) <= sys.version_info < (3,14) else 1)')
        return ($code -eq 0)
    } catch {
        return $false
    }
}

function Invoke-Python {
    # 正确处理 @('py','-3.12') 和 @('python') 两种形式；
    # 不能用 $arr[1..($arr.Length-1)]：长度为 1 时 1..0 是倒序范围，会取到空值导致参数错位。
    param([string[]]$Command, [string[]]$Arguments)
    $exe = $Command[0]
    $rest = @()
    if ($Command.Length -gt 1) {
        $rest = $Command[1..($Command.Length - 1)]
    }
    $null = & $exe @rest @Arguments 2>&1   # 屏蔽 py 启动器找不到版本时的噪音输出
    return $LASTEXITCODE
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
Write-Host '      iPhone 虚拟定位 · 安装程序' -ForegroundColor White
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
            Write-Host '    请手动安装 Python 3.12（勾选 Add to PATH）后重新运行本脚本：https://www.python.org/downloads/'
            exit 1
        }

        Write-Host '    正在静默安装（只装给当前用户）…'
        $arguments = @(
            '/quiet', 'InstallAllUsers=0', 'PrependPath=0', 'Include_tcltk=1',
            'Include_pip=1', 'Include_test=0', 'SimpleInstall=1'
        )
        Start-Process -FilePath $installer -ArgumentList $arguments -Wait
        Write-Ok 'Python 安装完成'

        $pyExe = Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"
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
    Write-Ok '.venv 创建完成'
}

# ------------------------------------------------------------ 2. 依赖安装 --
Write-Step '第 2 步：安装依赖 pymobiledevice3（约 30 MB）'
& $VenvPython -m pip install --upgrade pip --quiet -i $Mirror 2>$null
& $VenvPython -m pip install pymobiledevice3 -i $Mirror
if ($LASTEXITCODE -ne 0) {
    Write-Warn '国内镜像失败，改用官方源重试…'
    & $VenvPython -m pip install pymobiledevice3
}
if ($LASTEXITCODE -ne 0) {
    Write-Err '依赖安装失败，请检查网络后重新运行本脚本'
    exit 1
}
Write-Ok '依赖安装完成'

# ------------------------------------------------------------ 3. 苹果驱动 --
Write-Step '第 3 步：检查苹果手机驱动'
$service = Get-Service -Name 'Apple Mobile Device Service' -ErrorAction SilentlyContinue
if ($service) {
    Write-Ok "驱动已就绪（服务状态：$($service.Status)）"
} else {
    Write-Warn '没检测到苹果驱动，需要安装才能连上 iPhone'
    Write-Host '    正在调用驱动安装脚本（会弹出 UAC，请点「是」）…'
    $driverScript = Join-Path $Root 'install_apple_driver.ps1'
    if (Test-Path $driverScript) {
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $driverScript
        Start-Sleep -Seconds 2
        $service = Get-Service -Name 'Apple Mobile Device Service' -ErrorAction SilentlyContinue
        if ($service) {
            Write-Ok "驱动安装成功（服务状态：$($service.Status)）"
        } else {
            Write-Warn '驱动仍未就绪，可能需要重启电脑后再次运行本脚本'
        }
    } else {
        Write-Err '缺少 install_apple_driver.ps1，无法自动安装驱动'
    }
}

# ---------------------------------------------------------------- 4. 自检 --
Write-Step '第 4 步：自检'
& $VenvPython (Join-Path $Root 'fakegps.py') doctor 2>&1 | Select-String -Pattern 'usbmux|Tunnel \(userspace\)|no device' | ForEach-Object { Write-Host "    $($_.Line)" }

Write-Host ''
Write-Host '  ============================================' -ForegroundColor White
Write-Host '   安装完成！' -ForegroundColor Green
Write-Host '  ============================================' -ForegroundColor White
Write-Host ''
Write-Host '  接下来：' -ForegroundColor Yellow
Write-Host '    1. 用数据线把 iPhone 连到电脑，解锁手机，弹窗点「信任」'
Write-Host '    2. 双击「启动.bat」打开界面'
Write-Host '    3. 手机开启开发者模式：设置 → 隐私与安全性 → 开发者模式（需重启手机）'
Write-Host '    4. 界面里点「挂载镜像」，再点「设置虚拟位置」'
Write-Host ''
Write-Host '  详细步骤见同目录《使用说明.md》。' -ForegroundColor Yellow
Write-Host ''
