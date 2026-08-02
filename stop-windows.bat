@echo off
chcp 65001 >nul
echo ====================================
echo 停止 SmartLife AIOps 服务
echo ====================================
echo.

REM 停止 FastAPI 服务
echo [1/4] 停止 FastAPI 服务...
taskkill /FI "WINDOWTITLE eq SmartLife AIOps API*" /F >nul 2>&1
if errorlevel 1 (
    echo [信息] FastAPI 服务未运行或已停止
) else (
    echo [成功] FastAPI 服务已停止
)
echo.

REM 停止 CLS MCP 服务
echo [2/4] 停止 CLS MCP 服务...
taskkill /FI "WINDOWTITLE eq CLS MCP Server*" /F >nul 2>&1
if errorlevel 1 (
    echo [信息] CLS MCP 服务未运行或已停止
) else (
    echo [成功] CLS MCP 服务已停止
)
echo.

REM 停止 Monitor MCP 服务
echo [3/4] 停止 Monitor MCP 服务...
taskkill /FI "WINDOWTITLE eq Monitor MCP Server*" /F >nul 2>&1
if errorlevel 1 (
    echo [信息] Monitor MCP 服务未运行或已停止
) else (
    echo [成功] Monitor MCP 服务已停止
)
echo.

REM 仅停止本项目管理的 AIOps 基础设施，不影响业务 Prometheus/Alertmanager
echo [4/4] 停止 AIOps 基础设施（Redis + Milvus）...
docker ps --format "{{.Names}}" | findstr "milvus" >nul 2>&1
if not errorlevel 1 (
    docker compose -f vector-database.yml down
    if errorlevel 1 (
        echo [错误] Docker 容器停止失败
    ) else (
        echo [成功] AIOps 基础设施已停止
    )
) else (
    echo [信息] AIOps 基础设施未运行
)
echo.

echo ====================================
echo 所有服务已停止！
echo ====================================
echo.
pause
