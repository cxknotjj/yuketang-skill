@echo off
REM start_cdp_browser.cmd - 双击启动「带调试端口的浏览器」，供 yuketang-auto-answer 使用
REM
REM 支持 Chrome / Edge / Brave / Chromium（自动探测已安装的那个）。
REM 想指定浏览器：在本文件末尾的 %* 前加参数，或在命令行执行
REM     start_cdp_browser.cmd --browser edge
REM
REM 注意：会先关闭该浏览器再重启（未保存的标签页会丢）。

setlocal
set SCRIPTDIR=%~dp0
set PORT=9333
set BROWSER=%1

REM 找一个能用的 Python
set PY=
where python >nul 2>&1 && set PY=python
if "%PY%"=="" ( where py >nul 2>&1 && set PY=py -3 )
if "%PY%"=="" ( if exist "D:\env\conda\python.exe" set PY=D:\env\conda\python.exe )
if "%PY%"=="" (
  echo [X] 未找到 Python，请先安装 Python 3.8+ 并加入 PATH
  pause
  exit /b 1
)

echo == 启动带调试端口的浏览器 ==
%PY% "%SCRIPTDIR%bootstrap.py" --port %PORT% %BROWSER%
echo.
pause
