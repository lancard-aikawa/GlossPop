@echo off
rem ===========================================================================
rem  GlossPop - local check helper
rem
rem  ASCII ONLY, on purpose. cmd.exe reads .cmd files in the console code page
rem  (CP932 on Japanese Windows), so UTF-8 Japanese turns into mojibake, and
rem  CP932 characters whose trail byte is 0x5C ("\") break parsing outright.
rem  Keep every line in this file 7-bit ASCII.
rem
rem  Usage:  check.cmd [command]
rem          check.cmd            show the menu
rem          check.cmd test       run the test suite
rem          check.cmd app        run from source, opens the app window
rem          check.cmd serve      run from source, no window
rem          check.cmd build      build the exe, reusing the cache (fast)
rem          check.cmd rebuild    build the exe from scratch (--clean)
rem          check.cmd exe        start the built exe and check it answers
rem          check.cmd kill       free the port (kills the owner, not the parent)
rem          check.cmd all        test + build + exe
rem
rem  Set PORT to use another port:  set PORT=9000 && check.cmd serve
rem ===========================================================================
setlocal
cd /d "%~dp0"
if "%PORT%"=="" set PORT=8765
set EXE=dist\GlossPop\glosspop.exe

rem Prefer PowerShell 7 (that is what the release workflow uses, and its console
rem is UTF-8). Windows PowerShell 5.1 works too: the .ps1 files carry a UTF-8
rem BOM, without which 5.1 reads them as CP932 and Japanese strings break the
rem parser. Do not strip those BOMs.
set PS=pwsh -NoProfile -ExecutionPolicy Bypass -Command
where pwsh >nul 2>&1 || set PS=powershell -NoProfile -ExecutionPolicy Bypass -Command

set CMD=%~1
if "%CMD%"=="" goto :menu
goto :run

:menu
echo.
echo   GlossPop check   (port %PORT%)
echo.
echo    1  test      run the test suite
echo    2  app       run from source, opens the app window
echo    3  serve     run from source, no window
echo    4  build     build the exe, reusing the cache (fast)
echo    5  rebuild   build the exe from scratch
echo    6  exe       start the built exe and check it answers
echo    7  kill      free port %PORT%
echo    8  all       test + build + exe
echo.
set /p CMD=" select (or q to quit): "
if /i "%CMD%"=="q" goto :eof
if "%CMD%"=="1" set CMD=test
if "%CMD%"=="2" set CMD=app
if "%CMD%"=="3" set CMD=serve
if "%CMD%"=="4" set CMD=build
if "%CMD%"=="5" set CMD=rebuild
if "%CMD%"=="6" set CMD=exe
if "%CMD%"=="7" set CMD=kill
if "%CMD%"=="8" set CMD=all

:run
if /i "%CMD%"=="test"    goto :do_test
if /i "%CMD%"=="app"     goto :do_app
if /i "%CMD%"=="serve"   goto :do_serve
if /i "%CMD%"=="build"   goto :do_build
if /i "%CMD%"=="rebuild" goto :do_rebuild
if /i "%CMD%"=="exe"     goto :do_exe
if /i "%CMD%"=="kill"    goto :do_kill
if /i "%CMD%"=="all"     goto :do_all
echo unknown command: %CMD%
exit /b 2

rem ---------------------------------------------------------------- test
:do_test
echo [test] uv run pytest -q
uv run pytest -q
exit /b %ERRORLEVEL%

rem ---------------------------------------------------------------- source
:do_app
call :free_port
echo [app] uv run glosspop app --port %PORT%
uv run glosspop app --port %PORT%
exit /b %ERRORLEVEL%

:do_serve
call :free_port
echo [serve] uv run glosspop serve --port %PORT%
uv run glosspop serve --port %PORT%
exit /b %ERRORLEVEL%

rem ---------------------------------------------------------------- build
rem A running server holds files under dist and .venv, so free the port first.
:do_build
call :free_port
echo [build] packaging\build.ps1 -Fast
%PS% "& .\packaging\build.ps1 -Fast"
exit /b %ERRORLEVEL%

:do_rebuild
call :free_port
echo [rebuild] packaging\build.ps1
%PS% "& .\packaging\build.ps1"
exit /b %ERRORLEVEL%

rem ---------------------------------------------------------------- exe
rem The real work lives in packaging\check-exe.ps1. Embedding PowerShell in a
rem .cmd means fighting two layers of quoting for no gain.
:do_exe
echo [exe] packaging\check-exe.ps1 -Port %PORT%
%PS% "& .\packaging\check-exe.ps1 -Port %PORT%"
exit /b %ERRORLEVEL%

rem ---------------------------------------------------------------- all
:do_all
call :do_test  || exit /b %ERRORLEVEL%
call :do_build || exit /b %ERRORLEVEL%
call :do_exe   || exit /b %ERRORLEVEL%
echo [all] ok
exit /b 0

rem ---------------------------------------------------------------- kill
:do_kill
call :free_port
echo [kill] port %PORT% is free
exit /b 0

rem Killing the parent (uv) leaves the child python holding the port. The new
rem server then fails to bind and dies quietly, so the OLD code keeps running
rem and you misdiagnose it as "my change did not take effect". Kill the owner.
:free_port
%PS% "Get-NetTCPConnection -LocalPort %PORT% -State Listen -ErrorAction SilentlyContinue ^| ForEach-Object { Write-Host ('  freeing port %PORT% (pid ' + $_.OwningProcess + ')'); Stop-Process -Id $_.OwningProcess -Force }"
exit /b 0
