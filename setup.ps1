# One-time setup for Capri v2. Run from this folder in PowerShell:
#   powershell -ExecutionPolicy Bypass -File setup.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$ProgressPreference = "SilentlyContinue"   # the progress bar makes big downloads 10x slower in Windows PowerShell

function Find-Python {
    # Returns a command (array) that runs a usable Python 3.11-3.13, or $null. Never throws.
    $old = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    try {
        if (Get-Command py -ErrorAction SilentlyContinue) {
            foreach ($v in "3.12", "3.13", "3.11") {
                $out = & py "-$v" -c "import sys; print(sys.version_info[:2])" 2>&1
                if ($LASTEXITCODE -eq 0 -and "$out" -match "\(3, 1[123]\)") { return @("py", "-$v") }
            }
        }
        foreach ($cmd in "python", "python3") {
            if (Get-Command $cmd -ErrorAction SilentlyContinue) {
                $out = & $cmd -c "import sys; print(sys.version_info[:2])" 2>&1
                if ($LASTEXITCODE -eq 0 -and "$out" -match "\(3, 1[123]\)") { return @($cmd) }
            }
        }
    } finally { $ErrorActionPreference = $old }
    return $null
}

if (-not (Test-Path .venv\Scripts\python.exe)) {
    $py = Find-Python
    if (-not $py) {
        Write-Host "No Python 3.11-3.13 found. Installing Python 3.12 (one time, a few minutes)..." -ForegroundColor Yellow
        $ErrorActionPreference = "Continue"
        if (Get-Command py -ErrorAction SilentlyContinue) {
            & py install 3.12          # new Python Install Manager
        }
        if (-not (Find-Python) -and (Get-Command winget -ErrorAction SilentlyContinue)) {
            & winget install -e --id Python.Python.3.12 --scope user --accept-source-agreements --accept-package-agreements
            $env:Path = [Environment]::GetEnvironmentVariable("Path", "User") + ";" + [Environment]::GetEnvironmentVariable("Path", "Machine")
        }
        $ErrorActionPreference = "Stop"
        $py = Find-Python
    }
    if (-not $py) {
        Write-Host "Couldn't install Python automatically." -ForegroundColor Red
        Write-Host "Install Python 3.12 from https://www.python.org/downloads/ (tick 'Add python.exe to PATH'), close this window, and run setup.ps1 again."
        exit 1
    }
    Write-Host "Using: $($py -join ' ')"
    $exe = $py[0]; $rest = @($py | Select-Object -Skip 1)
    & $exe @rest -m venv .venv
    if (-not (Test-Path .venv\Scripts\python.exe)) { Write-Host "Creating .venv failed." -ForegroundColor Red; exit 1 }
}

& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt
# GPU voice: replace the CPU onnxruntime that kokoro-onnx pulls in with the CUDA build.
# Pinned below 1.26: newer builds need CUDA 13 (cublas64_13.dll). 1.22-1.25 use CUDA 12, which the
# nvidia-*-cu12 wheels provide and which works with any recent NVIDIA driver.
& .\.venv\Scripts\python.exe -m pip uninstall -y onnxruntime onnxruntime-gpu
& .\.venv\Scripts\python.exe -m pip install "onnxruntime-gpu[cuda,cudnn]>=1.22,<1.26" "nvidia-cublas-cu12"

# Voice activity detection model for hands-free mode. --no-deps: we only need its ONNX file, not PyTorch.
& .\.venv\Scripts\python.exe -m pip install --no-deps "silero-vad>=5"

New-Item -ItemType Directory -Force models | Out-Null
$base = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
foreach ($f in "kokoro-v1.0.onnx", "voices-v1.0.bin") {
    if (-not (Test-Path "models\$f")) {
        Write-Host "Downloading $f ..."
        Invoke-WebRequest "$base/$f" -OutFile "models\$f"
    }
}

Write-Host "`nChecking everything..." -ForegroundColor Cyan
& .\.venv\Scripts\python.exe -u -m capri doctor
Write-Host "`nSetup done. Next:" -ForegroundColor Green
Write-Host "  .\capri.bat say Hey, it's Capri.     (voice check)"
Write-Host "  .\capri.bat bench-stt               (pick the speech engine)"
Write-Host "  .\capri.bat                         (run her)"
