@echo off

set "QWENTTS_BUILD_PATH=%PATH%"
set "PATH="
set "Path="
set "PATH=%QWENTTS_BUILD_PATH%"
set "QWENTTS_BUILD_PATH="

call "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat"
if errorlevel 1 exit /b %errorlevel%

if not defined CUDA_PATH_V13_0 (
    echo CUDA 13.0 was not found in CUDA_PATH_V13_0.
    exit /b 1
)

rem rd /s /q build 2>nul
mkdir build 2>nul
cd build

cmake .. -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=120a-real "-DCUDAToolkit_ROOT=%CUDA_PATH_V13_0%"
if errorlevel 1 exit /b %errorlevel%
cmake --build . --config Release -j %NUMBER_OF_PROCESSORS%
if errorlevel 1 exit /b %errorlevel%

cd ..
