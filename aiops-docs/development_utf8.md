# AIOps 开发环境 UTF-8 说明

当前服务端编码链路已经统一，无需修改业务代码：

- MySQL：PyMySQL连接使用 `charset="utf8mb4"`，AIOps表使用 `utf8mb4`。
- 日志：Loguru文件输出及session日志sink均使用 `encoding="utf-8"`。
- SSE：响应头为 `Content-Type: text/event-stream; charset=utf-8`。

如果中文在终端中显示为乱码，通常是读取或展示环境的编码与UTF-8不一致，而不是数据库或日志写入错误。

## Windows PowerShell

建议先切换控制台代码页并设置管道输出编码：

```powershell
chcp 65001
$OutputEncoding = [System.Text.UTF8Encoding]::new()
```

读取日志时显式指定UTF-8：

```powershell
Get-Content .\logs\app.log -Encoding UTF8
Get-Content .\logs\aiops_2026-07-25.log -Encoding UTF8
```

## MySQL命令行

查询中文报告时显式指定客户端字符集：

```powershell
docker exec smartlife-mysql mysql --default-character-set=utf8mb4 `
  -uroot -p1234 -e "SELECT id,session_id,report FROM aiops.diagnosis_report ORDER BY id DESC LIMIT 1;"
```

可使用以下SQL确认连接字符集：

```sql
SHOW VARIABLES LIKE 'character_set_client';
SHOW VARIABLES LIKE 'character_set_connection';
SHOW VARIABLES LIKE 'character_set_results';
```

三项均应为 `utf8mb4`。

## 编辑器和浏览器

- 编辑器应以UTF-8打开 `.py`、`.md` 和 `.log` 文件，不要使用GBK或ANSI重新保存。
- 浏览器开发者工具中可检查SSE响应头是否包含 `charset=utf-8`。
- 前端使用 `TextDecoder()` 时默认按UTF-8解码；如自行增加读取代码，应继续使用UTF-8。

## 快速判断乱码阶段

- 数据库和日志文件内容正常，仅终端异常：检查终端代码页和读取命令。
- MySQL CLI异常，浏览器正常：增加 `--default-character-set=utf8mb4`。
- 浏览器异常：检查反向代理是否覆盖SSE的 `Content-Type` 响应头。
- 文件中已经出现 `�`：说明内容在写入前或解码时已经发生不可逆替换，需要从上游原始数据重新生成。
