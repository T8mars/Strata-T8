param(
    [ValidateSet('cpu','cuda')][string]$Device = 'cpu',
    [string]$CudaRoot = 'C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.8',
    [ValidateRange(50,130)][int]$CudaArch = 89,
    [string]$WorkDir,
    [string]$Target
)
$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
if (!$WorkDir) { $WorkDir = Join-Path $projectRoot '.portable-build\asr-runtime' }
if (!$Target) { $Target = Join-Path $projectRoot 'runtime\asr' }
$WorkDir = [IO.Path]::GetFullPath($WorkDir)
$Target = [IO.Path]::GetFullPath($Target)
if ($WorkDir -eq [IO.Path]::GetPathRoot($WorkDir) -or $Target -eq [IO.Path]::GetPathRoot($Target)) { throw 'A drive root cannot be a build target' }
New-Item -ItemType Directory -Path $WorkDir -Force | Out-Null
$archive = Join-Path $WorkDir 'python-3.12.10.nupkg'
if (!(Test-Path -LiteralPath $archive)) { Invoke-WebRequest 'https://www.nuget.org/api/v2/package/python/3.12.10' -OutFile $archive }
if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash -ne '0EB85C2DFCCCCF1B17352DE4C397F69194035B7D37149EACC16F1147D93DE3B8') { throw 'Build Python checksum mismatch' }
$pythonDir = Join-Path $WorkDir 'python-nuget\tools'
if (!(Test-Path -LiteralPath (Join-Path $pythonDir 'python.exe'))) {
    [IO.Compression.ZipFile]::ExtractToDirectory($archive,(Join-Path $WorkDir 'python-nuget'))
}
$pythonExe = Join-Path $pythonDir 'python.exe'
& $pythonExe -m pip install -r (Join-Path $projectRoot 'requirements-asr.txt') pybind11==3.0.1 cmake==4.1.2 ninja==1.13.2
if ($LASTEXITCODE) { throw 'ASR build dependencies failed' }
$llama = Join-Path $WorkDir 'llama.cpp'
$llamaCommit = 'ad6c66839af3c5646fba8c6c2e2087a1e4e38948'
if (!(Test-Path -LiteralPath (Join-Path $llama '.git'))) {
    & git clone --filter=blob:none https://github.com/ggml-org/llama.cpp.git $llama
    if ($LASTEXITCODE) { throw 'llama.cpp clone failed' }
}
& git -C $llama checkout --detach $llamaCommit
if ($LASTEXITCODE -or (& git -C $llama rev-parse HEAD) -ne $llamaCommit) { throw 'Pinned llama.cpp checkout failed' }
if (& git -C $llama status --porcelain) { throw 'llama.cpp has local modifications' }
$vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
$vsInstall = & $vswhere -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
if (!$vsInstall -or $vsInstall -match '[&|<>]') { throw 'Visual Studio 2022 C++ Build Tools required' }
$vsCmd = Join-Path $vsInstall 'Common7\Tools\VsDevCmd.bat'
$environmentLines = & cmd.exe /d /c ('"'+$vsCmd+'" -arch=amd64 -host_arch=amd64 >nul && set')
if ($LASTEXITCODE) { throw 'MSVC environment initialization failed' }
foreach ($line in $environmentLines) {
    if ($line -match '^([^=]+)=(.*)$') { [Environment]::SetEnvironmentVariable($matches[1],$matches[2],'Process') }
}
$cmake = Join-Path $pythonDir 'Lib\site-packages\cmake\data\bin\cmake.exe'
$ninja = Join-Path $pythonDir 'Scripts\ninja.exe'
$pybind = & $pythonExe -m pybind11 --cmakedir
$build = Join-Path $WorkDir ('native-'+$Device)
$argsNative = @('-S',(Join-Path $projectRoot 'tools\asr-native'),'-B',$build,'-G','Ninja',
    '-DCMAKE_BUILD_TYPE=Release',('-DCMAKE_MAKE_PROGRAM='+$ninja),('-DPython_EXECUTABLE='+$pythonExe),
    ('-Dpybind11_DIR='+$pybind),('-DLLAMA_CPP_DIR='+$llama),'-DGGML_NATIVE=OFF','-DGGML_BACKEND_DL=ON','-DGGML_CPU_ALL_VARIANTS=ON')
if ($Device -eq 'cuda') {
    if (!(Test-Path -LiteralPath (Join-Path $CudaRoot 'bin\nvcc.exe'))) { throw 'CUDA Toolkit 12.8 required' }
    $argsNative += @('-DGGML_CUDA=ON',('-DCUDAToolkit_ROOT='+$CudaRoot),('-DCMAKE_CUDA_COMPILER='+(Join-Path $CudaRoot 'bin\nvcc.exe')),('-DCMAKE_CUDA_ARCHITECTURES='+$CudaArch),'-DCMAKE_CUDA_FLAGS=--allow-unsupported-compiler')
} else { $argsNative += '-DGGML_CUDA=OFF' }
& $cmake @argsNative
if ($LASTEXITCODE) { throw 'ASR native configuration failed' }
& $cmake --build $build --target qwen3asr_native --parallel 2
if ($LASTEXITCODE) { throw 'ASR native build failed' }
$redist = Join-Path $env:VCToolsRedistDir 'x64'
$stageArgs = @((Join-Path $projectRoot 'tools\stage_asr_runtime.py'),'--python-dir',$pythonDir,'--build',$build,'--target',$Target,'--device',$Device,'--redist',$redist)
if ($Device -eq 'cuda') { $stageArgs += @('--cuda-bin',(Join-Path $CudaRoot 'bin'),'--cuda-arch',$CudaArch) }
& $pythonExe -X utf8 @stageArgs
if ($LASTEXITCODE) { throw 'ASR component staging failed' }
Write-Output 'ASR runtime prepared. Model weights remain external; normal startup never runs this build.'
