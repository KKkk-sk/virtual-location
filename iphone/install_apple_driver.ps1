# 补装 Apple Mobile Device Support（usbmuxd 驱动）
#
# 背景：iTunes 主体装上了，但 Apple Mobile Device Support 这个组件没装 —— 而它才是
# 提供 usbmuxd 的部分，缺了它 pymobiledevice3 就连不上手机。
# 本脚本做的事：等域名可解析 → 下载 iTunes 安装包 → 用 7-Zip 拆出组件 MSI → 提权安装 → 验证服务。
#
# 用法（在项目目录下，普通 PowerShell 即可，脚本会自己请求管理员权限）：
#   powershell -ExecutionPolicy Bypass -File install_apple_driver.ps1
#   powershell -ExecutionPolicy Bypass -File install_apple_driver.ps1 -WaitMinutes 30
#
# 前置条件：secure-appldnld.apple.com 能解析。如果一直失败，先换个网络
# （关掉 iPhone 热点 / 暂停 Pangolin VPN / 换普通 Wi-Fi）。

param(
    [int]$WaitMinutes = 0,   # 0 = 立刻检查一次就退出；>0 = 最多等这么多分钟
    [string]$Url = 'https://secure-appldnld.apple.com/itunes12/140-75773-20260908-6e5e0165-99cb-4b30-b541-1b615fccfc1a/iTunes64Setup.exe'
)

$ErrorActionPreference = 'Stop'
$work = Join-Path $env:LOCALAPPDATA 'dsh-apple-driver'
New-Item -ItemType Directory -Force -Path $work | Out-Null
$setup = Join-Path $work 'iTunes64Setup.exe'
$extract = Join-Path $work 'extracted'

function Write-Step($message) { Write-Host "==> $message" -ForegroundColor Cyan }

# ---------------------------------------------------------------- 1. 等域名
Write-Step '检查 secure-appldnld.apple.com 是否可解析'
$deadline = (Get-Date).AddMinutes($WaitMinutes)
while ($true) {
    $ip = (Resolve-DnsName 'secure-appldnld.apple.com' -Type A -ErrorAction SilentlyContinue |
        Where-Object IPAddress | Select-Object -First 1).IPAddress
    if ($ip) { Write-Host "    DNS 正常：$ip"; break }
    if ((Get-Date) -ge $deadline) {
        Write-Host '    域名无法解析：当前网络把苹果的下载域名拦住了。' -ForegroundColor Yellow
        Write-Host '    请换网络（关掉 iPhone 热点 / 暂停 Pangolin / 换普通 Wi-Fi）后重跑本脚本。' -ForegroundColor Yellow
        exit 2
    }
    Write-Host '    仍无法解析，15 秒后重试…'
    Start-Sleep -Seconds 15
}

# ---------------------------------------------------------------- 2. 下载
if ((Test-Path $setup) -and (Get-Item $setup).Length -gt 100MB) {
    Write-Step "安装包已存在，跳过下载：$setup"
} else {
    Write-Step '下载 iTunes 安装包（约 193 MB）'
    & curl.exe -L --connect-timeout 30 --max-time 1800 -o $setup $Url --show-error
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $setup)) {
        Write-Host '    下载失败。' -ForegroundColor Red
        exit 3
    }
    Write-Host ("    完成：{0} MB" -f [math]::Round((Get-Item $setup).Length / 1MB, 1))
}

# ---------------------------------------------------------------- 3. 解包
Write-Step '查找 7-Zip'
$sevenZip = $null
foreach ($candidate in @(
    'C:\Program Files\7-Zip\7z.exe',
    'C:\Program Files (x86)\7-Zip\7z.exe',
    'C:\Program Files\5EClient\resources\7z.exe',
    'C:\Program Files\NVIDIA Corporation\NVIDIA app\7z.exe'
)) {
    if (Test-Path $candidate) { $sevenZip = $candidate; break }
}
if (-not $sevenZip) {
    $cmd = Get-Command 7z -ErrorAction SilentlyContinue
    if ($cmd) { $sevenZip = $cmd.Source }
}
if (-not $sevenZip) {
    Write-Host '    找不到 7z.exe，无法拆包。装个 7-Zip 再重试。' -ForegroundColor Red
    exit 4
}
Write-Host "    使用：$sevenZip"

if (Test-Path $extract) { Remove-Item $extract -Recurse -Force }
Write-Step '解包安装器'
& $sevenZip x $setup "-o$extract" -y | Out-Null
if (-not (Test-Path $extract)) {
    Write-Host '    解包失败。' -ForegroundColor Red
    exit 5
}

# ---------------------------------------------------------------- 4. 找 MSI
Write-Step '查找 Apple Mobile Device Support 组件'
$msi = Get-ChildItem $extract -Recurse -Filter '*.msi' -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -match 'MobileDevice' } | Select-Object -First 1

if (-not $msi) {
    Write-Host '    没找到 MobileDevice 相关 MSI。解包内容如下，请以此为准调整筛选条件：' -ForegroundColor Yellow
    Get-ChildItem $extract -Recurse -Filter '*.msi' | Select-Object -ExpandProperty FullName
    exit 6
}
Write-Host "    找到：$($msi.Name)"

# 如果已经在高权限会话里就直接装，否则提权
function Test-Admin {
    (New-Object Security.Principal.WindowsPrincipal(
        [Security.Principal.WindowsIdentity]::GetCurrent())).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}

$log = Join-Path $work 'amds_install.log'
if (Test-Path $log) { Remove-Item $log -Force }

Write-Step '安装组件（会弹 UAC，请点「是」）'
$msiArgs = "/i `"$($msi.FullName)`" /qn /norestart /l*v `"$log`""
if (Test-Admin) {
    $p = Start-Process msiexec.exe -ArgumentList $msiArgs -Wait -PassThru
    $code = $p.ExitCode
} else {
    $p = Start-Process msiexec.exe -ArgumentList $msiArgs -Verb RunAs -Wait -PassThru
    $code = $p.ExitCode
}
Write-Host "    msiexec 退出码：$code（0 表示成功，3010 表示需要重启）"

# ---------------------------------------------------------------- 5. 验证
Write-Step '验证服务'
Start-Sleep -Seconds 2
$svc = Get-Service -Name 'Apple Mobile Device Service' -ErrorAction SilentlyContinue
if ($svc) {
    Write-Host "    Apple Mobile Device Service：$($svc.Status)" -ForegroundColor Green
    try { Start-Service 'Apple Mobile Device Service' -ErrorAction Stop; Write-Host '    服务已启动' }
    catch { Write-Host "    启动服务需要管理员权限，或需要重启后生效" -ForegroundColor Yellow }
} else {
    Write-Host '    服务未注册。若退出码是 3010，请重启后再检查。' -ForegroundColor Yellow
    Write-Host "    安装日志：$log"
    exit 7
}

Write-Step '完成。下一步：插上 iPhone，在项目目录跑  .venv\Scripts\python.exe fakegps.py devices'
