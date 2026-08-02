# Linux 服务启动失败排查

## 1. 问题概述

Linux 服务启动失败是指应用程序、系统组件、数据库、中间件或后台进程无法正常启动，或者启动后立即退出、反复重启，无法对外提供服务。

在使用 systemd 的 Linux 系统中，服务通常通过以下命令启动：

```bash
systemctl start <服务名称>
```

如果启动失败，可能出现：

```text
Job for example.service failed because the control process exited with error code.
```

服务启动失败可能由配置文件错误、端口冲突、权限不足、依赖服务不可用、环境变量缺失、资源不足、程序文件损坏等原因引起。

排查的核心目标包括：

1. 确认服务是否真正启动失败。
2. 获取服务启动失败的具体错误信息。
3. 确认失败发生在 systemd、应用程序还是依赖组件。
4. 检查配置、权限、端口、依赖和系统资源。
5. 根据根本原因修复问题。
6. 验证服务可以稳定运行，而不是启动后再次退出。

## 2. 常见问题现象

Linux 服务启动失败时，通常会出现以下现象：

1. `systemctl start` 返回失败。
2. 服务状态显示 `failed`。
3. 服务启动后立即变为 `inactive`。
4. 服务反复启动和退出。
5. 服务进程不存在。
6. 服务端口没有监听。
7. 应用日志中出现异常堆栈。
8. systemd 日志中显示退出码不为 0。
9. 服务因超时被 systemd 终止。
10. 服务因为内存不足被 OOM Killer 终止。
11. 服务启动时提示端口已被占用。
12. 服务启动时提示配置文件格式错误。
13. 服务启动时提示文件或目录不存在。
14. 服务启动时提示权限不足。
15. 服务启动时提示无法连接数据库、Redis 或其他依赖。
16. 服务状态显示已启动，但实际无法访问。

常见错误信息如下：

```text
Failed to start example.service
```

```text
Main process exited, code=exited, status=1/FAILURE
```

```text
Start request repeated too quickly
```

```text
Address already in use
```

```text
Permission denied
```

```text
No such file or directory
```

```text
Failed to connect to database
```

```text
Cannot allocate memory
```

```text
No space left on device
```

这些信息只能说明大致方向，仍然需要结合服务状态、systemd 日志和应用日志进一步定位。

## 3. 服务启动失败的常见原因

### 3.1 配置文件错误

配置文件语法错误、字段名称错误、格式不正确或配置值不合法，都可能导致服务启动失败。

常见问题包括：

- YAML 缩进错误。
- JSON 缺少逗号、引号或括号。
- XML 标签没有闭合。
- Nginx 配置指令错误。
- 数据库配置参数拼写错误。
- 配置文件中存在不可识别的字符。
- 配置文件编码不正确。
- 配置项使用了错误的数据类型。
- 配置文件引用了不存在的文件或目录。

### 3.2 端口被占用

服务需要监听的端口已经被其他进程占用，导致绑定失败。

常见错误如下：

```text
Address already in use
```

可能原因包括：

- 旧服务进程没有退出。
- 同一个服务被重复启动。
- 其他程序使用了相同端口。
- 服务配置了错误的端口。
- 容器端口映射发生冲突。

### 3.3 文件或目录权限不足

服务运行用户没有权限读取程序文件、配置文件或证书，也没有权限写入日志、数据和临时目录。

常见错误如下：

```text
Permission denied
```

常见场景包括：

- 配置文件属于其他用户。
- 日志目录不可写。
- 数据目录权限错误。
- 启动脚本没有执行权限。
- 服务使用非 root 用户运行。
- SELinux 阻止服务访问文件或端口。
- 父目录缺少执行权限。

### 3.4 文件或目录不存在

服务配置引用的文件、目录、证书、密钥或运行文件不存在，可能导致服务启动失败。

常见错误如下：

```text
No such file or directory
```

需要注意，该错误不仅可能表示程序文件不存在，也可能表示动态链接器、脚本解释器或依赖库不存在。

### 3.5 依赖服务不可用

应用启动时可能依赖：

- MySQL。
- PostgreSQL。
- Redis。
- Kafka。
- RabbitMQ。
- Elasticsearch。
- 配置中心。
- 注册中心。
- 对象存储。
- 外部 API。
- 网络文件系统。

如果依赖服务未启动、网络不通、认证失败或响应超时，应用可能无法完成初始化。

### 3.6 环境变量缺失

服务可能依赖数据库地址、密码、运行环境、Java 路径或其他环境变量。如果 systemd 启动时没有加载这些变量，服务可能启动失败。

常见问题包括：

- 在终端手动运行正常，但通过 systemd 启动失败。
- 环境变量只配置在用户的 `.bashrc` 中。
- `PATH` 中缺少程序目录。
- `JAVA_HOME`、`PYTHONPATH` 等变量缺失。
- systemd 的 `EnvironmentFile` 路径错误。
- 环境变量值包含特殊字符但没有正确转义。

### 3.7 程序依赖缺失

服务运行所需的软件包、动态库、Python 模块、Java 运行环境或 Node.js 依赖没有安装。

常见错误包括：

```text
ModuleNotFoundError
```

```text
ClassNotFoundException
```

```text
error while loading shared libraries
```

```text
command not found
```

### 3.8 系统资源不足

服务启动需要分配内存、线程、文件描述符、磁盘空间和网络端口。如果系统资源不足，服务可能无法启动。

常见原因包括：

- 物理内存不足。
- Swap 耗尽。
- 磁盘空间不足。
- inode 耗尽。
- 文件描述符达到上限。
- 进程数或线程数达到限制。
- 容器内存限制过小。
- 临时端口耗尽。

### 3.9 systemd 服务文件配置错误

systemd Unit 文件中的启动命令、用户、工作目录、环境变量或服务类型配置错误，也可能导致启动失败。

常见问题包括：

- `ExecStart` 路径错误。
- `WorkingDirectory` 不存在。
- `User` 或 `Group` 不存在。
- `EnvironmentFile` 不存在。
- `Type` 配置与程序运行方式不匹配。
- `PIDFile` 路径错误。
- `Restart` 配置导致服务反复重启。
- 修改 Unit 文件后没有执行 `daemon-reload`。
- 命令中使用了 systemd 不支持的 Shell 语法。

### 3.10 程序版本或配置不兼容

服务升级后，旧配置、旧数据或旧插件可能与新版本不兼容，导致启动失败。

常见场景包括：

- Java 版本不兼容。
- Python 依赖版本冲突。
- 数据库版本升级后参数被废弃。
- 插件与主程序版本不一致。
- 配置格式发生变化。
- 数据文件格式不兼容。
- 动态库版本不一致。

### 3.11 安全机制限制

SELinux、AppArmor、systemd 沙箱、安全策略或容器权限可能阻止服务访问文件、端口或系统资源。

### 3.12 服务启动超时

服务启动过程过长，超过 systemd 设置的启动超时时间，可能被 systemd 判定为失败并终止。

常见原因包括：

- 数据库初始化时间过长。
- 网络依赖连接超时。
- 大量数据加载。
- 索引恢复。
- 磁盘 IO 性能较差。
- 应用启动时执行耗时任务。
- `TimeoutStartSec` 配置过小。

## 4. 排查前的注意事项

排查服务启动失败时，应注意以下事项：

1. 不要连续、频繁地重复启动服务。
2. 不要在没有查看日志前直接重装软件。
3. 不要随意修改文件权限为 `777`。
4. 不要直接关闭 SELinux 或防火墙作为长期方案。
5. 不要删除不清楚用途的 PID、Lock 或数据文件。
6. 不要直接清空数据库或应用数据目录。
7. 不要在生产环境中盲目回滚配置。
8. 修改配置前应保留原始版本。
9. 操作前应确认服务名称、实例和环境。
10. 检查问题发生前是否有发布、升级或配置变更。

建议先记录以下信息：

```bash
date
hostname
uptime
systemctl status <服务名称> --no-pager -l
journalctl -u <服务名称> -n 200 --no-pager
systemctl cat <服务名称>
systemctl show <服务名称>
df -h
df -i
free -h
```

## 5. 第一步：确认服务当前状态

执行：

```bash
systemctl status <服务名称>
```

例如：

```bash
systemctl status nginx
```

显示完整内容：

```bash
systemctl status <服务名称> --no-pager -l
```

重点关注：

- `Loaded`：Unit 文件是否成功加载。
- `Active`：服务当前状态。
- `Main PID`：主进程 PID。
- `Result`：服务运行结果。
- `ExecStart`：实际执行的启动命令。
- `status`：进程退出状态。
- 最后几行启动日志。

常见状态包括：

```text
active (running)
```

表示服务正在运行。

```text
inactive (dead)
```

表示服务当前没有运行，但不一定代表启动失败。一次性任务执行完成后，也可能正常显示为 `inactive`。

```text
failed
```

表示服务启动或运行失败。

```text
activating
```

表示服务仍在启动过程中。

```text
deactivating
```

表示服务正在停止。

## 6. 第二步：查看 systemd 日志

查看服务最近日志：

```bash
journalctl -u <服务名称> -n 200
```

查看本次启动以来的日志：

```bash
journalctl -u <服务名称> -b
```

查看指定时间范围：

```bash
journalctl -u <服务名称> --since "2026-07-21 10:00:00" --until "2026-07-21 11:00:00"
```

实时查看：

```bash
journalctl -u <服务名称> -f
```

显示更详细的信息：

```bash
journalctl -xeu <服务名称>
```

重点查找以下关键词：

```text
error
failed
fatal
exception
denied
timeout
killed
invalid
cannot
refused
not found
```

过滤示例：

```bash
journalctl -u <服务名称> -b | grep -iE "error|failed|fatal|exception|denied|timeout|killed"
```

systemd 日志中最后一行通常只是启动失败的汇总信息，真正原因往往出现在前面的应用输出中，因此不能只查看最后一行。

## 7. 第三步：查看应用自身日志

systemd 日志可能只记录服务退出状态，详细错误通常位于应用自己的日志文件中。

常见日志目录包括：

```text
/var/log
/var/log/<服务名称>
/opt/<应用名称>/logs
/data/logs
```

查看最近日志：

```bash
tail -n 200 /path/to/application.log
```

实时查看：

```bash
tail -f /path/to/application.log
```

搜索错误信息：

```bash
grep -iE "error|failed|fatal|exception|denied|timeout" /path/to/application.log | tail -100
```

如果应用没有生成日志，应检查：

- 日志目录是否存在。
- 服务用户是否有写入权限。
- 日志配置是否正确。
- 程序是否在初始化日志组件前就已经退出。
- 标准输出是否被 systemd 接管。
- 磁盘空间和 inode 是否耗尽。

## 8. 第四步：检查服务启动命令

查看完整 Unit 文件：

```bash
systemctl cat <服务名称>
```

查看关键配置：

```bash
systemctl show <服务名称> -p ExecStart -p User -p Group -p WorkingDirectory -p EnvironmentFiles
```

重点检查：

- `ExecStart` 指向的程序是否存在。
- 命令参数是否正确。
- `WorkingDirectory` 是否存在。
- 服务用户和用户组是否存在。
- 环境变量文件是否存在。
- 启动命令是否需要 Shell 解释。
- 引用路径是否使用了绝对路径。

systemd 的 `ExecStart` 默认不会像交互式 Shell 一样处理所有 Shell 语法。例如，管道、输出重定向、通配符和环境变量展开可能无法按预期工作。

如果确实需要 Shell 语法，可以明确使用：

```ini
ExecStart=/bin/bash -c '实际命令'
```

但应尽量保持启动命令简单、明确，并注意参数转义和安全性。

## 9. 第五步：手动执行启动命令

从 Unit 文件中找到实际的 `ExecStart` 命令，然后在合适的用户和工作目录下手动执行。

切换到服务用户：

```bash
sudo -u <服务用户> -H bash
```

进入工作目录：

```bash
cd <WorkingDirectory>
```

执行实际命令：

```bash
/完整路径/程序 参数
```

手动执行通常可以直接看到程序输出的错误信息。

需要注意，使用 root 用户手动执行成功，并不能说明 systemd 服务配置正常。服务实际可能使用普通用户运行，因此应尽量使用 Unit 文件中配置的 `User` 执行测试。

如果手动运行正常，而 systemd 启动失败，应重点检查：

- systemd 环境变量。
- 工作目录。
- 运行用户。
- 文件权限。
- systemd 沙箱限制。
- Unit 文件配置。
- 启动超时时间。

## 10. 第六步：检查配置文件语法

不同服务通常提供配置检查命令。

### 10.1 Nginx

```bash
nginx -t
```

### 10.2 Apache HTTP Server

```bash
apachectl configtest
```

### 10.3 SSH 服务

```bash
sshd -t
```

### 10.4 HAProxy

```bash
haproxy -c -f /etc/haproxy/haproxy.cfg
```

### 10.5 MySQL

MySQL 应重点查看错误日志和配置项是否被当前版本支持。

### 10.6 YAML 文件

如果应用配置为 YAML，应重点检查：

- 缩进是否使用空格。
- 层级关系是否正确。
- 冒号后是否存在空格。
- 字符串是否需要引号。
- 是否混入 Tab。
- 特殊字符是否正确转义。

### 10.7 JSON 文件

可以使用：

```bash
jq . /path/to/config.json
```

配置文件检查时还需要确认：

- 配置文件路径是否正确。
- 服务实际加载的是哪个配置文件。
- 配置中引用的文件和目录是否存在。
- 配置是否属于当前环境。
- 敏感配置是否通过环境变量注入。
- 配置文件编码和换行符是否正常。

## 11. 第七步：检查端口是否被占用

查看监听端口：

```bash
ss -lntp
```

查看指定端口：

```bash
ss -lntp | grep ':8080'
```

也可以使用：

```bash
lsof -i :8080
```

如果端口已被其他进程占用，应确认：

- 是否为旧服务进程。
- 是否启动了重复实例。
- 是否有其他服务使用相同端口。
- 是否修改过端口配置。
- 是否存在容器端口映射冲突。

不要在不确认进程用途的情况下直接执行 `kill -9`。应先查看进程信息：

```bash
ps -fp <PID>
```

查看进程完整命令：

```bash
tr '\0' ' ' < /proc/<PID>/cmdline
```

如果确认是旧服务残留，应优先使用对应的服务管理命令正常停止。

## 12. 第八步：检查程序文件和路径

确认启动程序存在：

```bash
ls -l /path/to/program
```

确认文件类型：

```bash
file /path/to/program
```

确认启动脚本首行：

```bash
head -n 1 /path/to/script
```

脚本常见首行为：

```bash
#!/bin/bash
```

或：

```bash
#!/usr/bin/env python3
```

如果脚本解释器路径不存在，即使脚本文件存在，也可能出现：

```text
No such file or directory
```

如果文件来自 Windows，还应检查是否包含 CRLF 换行符：

```bash
file /path/to/script
```

可以在确认内容后转换：

```bash
dos2unix /path/to/script
```

确认程序具有执行权限：

```bash
test -x /path/to/program && echo executable
```

## 13. 第九步：检查文件和目录权限

查看程序文件权限：

```bash
ls -l /path/to/program
```

查看目录逐级权限：

```bash
namei -l /path/to/program
```

检查服务用户是否可以读取文件：

```bash
sudo -u <服务用户> test -r /path/to/config && echo readable
```

检查是否可以写入目录：

```bash
sudo -u <服务用户> test -w /path/to/directory && echo writable
```

重点检查：

- 程序文件是否可执行。
- 配置文件是否可读。
- 日志目录是否可写。
- 数据目录是否可写。
- 临时目录是否可写。
- PID 文件目录是否可写。
- Unix Socket 目录是否可写。
- 父目录是否具有执行权限。
- 文件所有者和用户组是否正确。

不要为了解决权限问题直接执行：

```bash
chmod -R 777 /目标目录
```

应根据最小权限原则设置正确的所有者、用户组和权限。

## 14. 第十步：检查服务用户和用户组

查看 Unit 文件配置：

```bash
systemctl show <服务名称> -p User -p Group
```

检查用户是否存在：

```bash
id <用户名>
```

检查用户组：

```bash
getent group <用户组>
```

如果服务用户不存在，systemd 可能出现类似错误：

```text
Failed at step USER spawning
```

还应检查服务用户是否有权访问：

- 程序目录。
- 配置目录。
- 日志目录。
- 数据目录。
- 证书和密钥。
- Unix Socket。
- 外部挂载目录。

## 15. 第十一步：检查 SELinux

查看 SELinux 状态：

```bash
getenforce
```

查看详细状态：

```bash
sestatus
```

搜索近期拒绝记录：

```bash
ausearch -m AVC -ts recent
```

查看审计日志：

```bash
grep -i denied /var/log/audit/audit.log | tail -100
```

SELinux 可能阻止：

- 服务读取配置文件。
- 服务写入日志或数据目录。
- 服务绑定特定端口。
- 服务访问网络。
- 服务执行某个文件。

不建议将永久关闭 SELinux 作为常规解决方案。应根据审计日志确认具体拒绝原因，然后修正文件上下文、布尔值或策略。

查看文件安全上下文：

```bash
ls -Z /path/to/file
```

恢复默认上下文：

```bash
restorecon -Rv /path/to/directory
```

修改前应确认目录的预期 SELinux 类型。

## 16. 第十二步：检查环境变量

查看 Unit 文件中的环境变量：

```bash
systemctl show <服务名称> -p Environment -p EnvironmentFiles
```

查看服务配置：

```bash
systemctl cat <服务名称>
```

常见写法如下：

```ini
[Service]
Environment="JAVA_HOME=/usr/lib/jvm/java-17"
Environment="APP_ENV=production"
EnvironmentFile=/etc/example/example.env
```

需要注意，systemd 启动服务时不会自动加载普通用户的：

```text
~/.bashrc
~/.bash_profile
/etc/profile
```

因此，在终端中执行正常、通过 systemd 启动失败时，应重点检查：

- `PATH`。
- `JAVA_HOME`。
- `PYTHONPATH`。
- 数据库连接变量。
- 密钥和 Token。
- 代理变量。
- 应用运行环境变量。
- `EnvironmentFile` 是否存在。
- 环境变量格式是否正确。

不要通过日志直接输出密码、Token 或私钥内容。

## 17. 第十三步：检查程序依赖

### 17.1 检查动态链接库

```bash
ldd /path/to/program
```

如果输出中出现：

```text
not found
```

说明程序依赖的动态库缺失或无法找到。

查看动态库缓存：

```bash
ldconfig -p
```

### 17.2 检查命令路径

```bash
command -v <命令>
```

```bash
which <命令>
```

在 systemd 中建议使用完整路径，例如：

```ini
ExecStart=/usr/bin/python3 /opt/app/main.py
```

而不是：

```ini
ExecStart=python3 /opt/app/main.py
```

### 17.3 检查 Python 依赖

```bash
python3 -m pip check
```

检查模块：

```bash
python3 -c "import 模块名称"
```

如果使用虚拟环境，应确认 Unit 文件使用的是虚拟环境中的 Python：

```ini
ExecStart=/opt/app/.venv/bin/python /opt/app/main.py
```

### 17.4 检查 Java 环境

```bash
java -version
```

查看 JAR 文件：

```bash
ls -lh /path/to/application.jar
```

测试启动：

```bash
java -jar /path/to/application.jar
```

应确认 Java 版本、JVM 参数、配置文件和应用版本之间兼容。

## 18. 第十四步：检查依赖服务

如果应用依赖数据库、Redis、消息队列或外部接口，应逐项测试。

### 18.1 检查 DNS

```bash
getent hosts <依赖服务域名>
```

### 18.2 检查端口

```bash
nc -vz -w 3 <依赖服务地址> <端口>
```

### 18.3 检查 HTTP 接口

```bash
curl -v --connect-timeout 3 --max-time 10 http://<依赖服务地址>/
```

### 18.4 检查本机依赖服务状态

```bash
systemctl status <依赖服务名称>
```

重点检查：

- 地址和端口是否正确。
- 域名能否解析。
- 网络是否可达。
- 用户名和密码是否正确。
- 数据库是否允许远程连接。
- 防火墙和安全组是否放行。
- 依赖服务是否达到连接上限。
- 依赖服务响应是否超时。
- TLS 证书是否有效。

如果服务依赖其他 systemd 服务，可以在 Unit 中配置：

```ini
After=network-online.target mysql.service
Wants=network-online.target
Requires=mysql.service
```

但需要注意，`After` 只表示启动顺序，不表示依赖服务已经完成业务层初始化。应用仍应具备合理的连接重试和降级能力。

## 19. 第十五步：检查磁盘空间和 inode

查看磁盘空间：

```bash
df -hT
```

查看 inode：

```bash
df -i
```

磁盘空间或 inode 耗尽时，服务可能无法：

- 创建日志文件。
- 写入 PID 文件。
- 创建 Unix Socket。
- 写入数据库数据。
- 创建临时文件。
- 解压依赖文件。
- 生成缓存。
- 写入应用状态。

常见错误如下：

```text
No space left on device
```

还应检查应用使用的具体挂载点，而不是只检查根分区。

## 20. 第十六步：检查内存和 OOM

查看内存：

```bash
free -h
```

查看系统 OOM 日志：

```bash
dmesg -T | grep -iE "out of memory|oom|killed process"
```

查看 systemd 日志中的终止信息：

```bash
journalctl -u <服务名称> | grep -iE "killed|oom|memory"
```

如果服务启动后很快消失，可能是启动阶段内存峰值过高，被系统或容器 OOM Killer 终止。

还需要检查：

- Java `-Xms`、`-Xmx` 是否过大。
- 容器内存限制是否过小。
- 是否存在其他高内存进程。
- Swap 是否耗尽。
- 服务是否一次性加载大量数据。

## 21. 第十七步：检查文件描述符和进程限制

查看当前 Shell 限制：

```bash
ulimit -a
```

查看 Unit 限制：

```bash
systemctl show <服务名称> -p LimitNOFILE -p LimitNPROC
```

查看进程限制：

```bash
cat /proc/<PID>/limits
```

常见错误包括：

```text
Too many open files
```

```text
Resource temporarily unavailable
```

Unit 文件中可以配置：

```ini
[Service]
LimitNOFILE=65535
LimitNPROC=4096
```

修改限制前，应先确认服务是否存在文件描述符泄漏、线程泄漏或进程泄漏。单纯提高上限可能只会延迟问题发生。

## 22. 第十八步：检查 PID 文件和旧进程

部分服务通过 PID 文件判断是否已经运行。服务异常退出后，如果旧 PID 文件没有清理，可能导致再次启动失败。

查看 Unit 配置：

```bash
systemctl show <服务名称> -p PIDFile
```

查找 PID 文件：

```bash
find /run /var/run -maxdepth 2 -name '*服务名称*.pid' 2>/dev/null
```

读取 PID：

```bash
cat /path/to/service.pid
```

确认对应进程：

```bash
ps -fp <PID>
```

只有在确认旧进程不存在、PID 文件确实已经失效时，才可以清理残留 PID 文件。

不能在未确认的情况下直接删除 PID 文件，否则可能造成同一服务启动多个实例。

## 23. 第十九步：检查 systemd Unit 文件

查看 Unit 文件及覆盖配置：

```bash
systemctl cat <服务名称>
```

检查 Unit 文件语法：

```bash
systemd-analyze verify /path/to/example.service
```

常见 Unit 文件如下：

```ini
[Unit]
Description=Example Application
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=example
Group=example
WorkingDirectory=/opt/example
EnvironmentFile=/etc/example/example.env
ExecStart=/opt/example/bin/start
Restart=on-failure
RestartSec=5
LimitNOFILE=65535

[Install]
WantedBy=multi-user.target
```

重点检查以下配置。

### 23.1 Type

常见值包括：

- `simple`：启动命令执行后，systemd 认为服务已经启动。
- `exec`：程序成功执行后，systemd 认为服务已经启动。
- `forking`：程序会 Fork 到后台运行。
- `oneshot`：执行一次性任务。
- `notify`：服务主动通知 systemd 启动完成。
- `idle`：等待其他任务完成后执行。

如果程序实际在前台运行，却配置为 `forking`，或者程序会后台化却配置了不合适的类型，可能导致 systemd 错误判断服务状态。

### 23.2 ExecStart

`ExecStart` 应使用正确的绝对路径，并确保参数格式正确。

### 23.3 WorkingDirectory

工作目录不存在或服务用户无权访问时，可能出现：

```text
Failed at step CHDIR
```

### 23.4 User 和 Group

用户或用户组不存在时，可能出现：

```text
Failed at step USER
```

### 23.5 EnvironmentFile

如果环境文件不是可选项，而文件又不存在，服务可能启动失败。

可选环境文件可以写成：

```ini
EnvironmentFile=-/etc/example/example.env
```

前面的 `-` 表示文件不存在时不直接导致失败，但是否适合使用应根据服务要求判断。

## 24. 第二十步：重新加载 systemd 配置

修改 Unit 文件后，需要执行：

```bash
systemctl daemon-reload
```

然后重新启动服务：

```bash
systemctl restart <服务名称>
```

查看状态：

```bash
systemctl status <服务名称> --no-pager -l
```

如果忘记执行 `daemon-reload`，systemd 可能仍然使用旧配置。

如果服务通过覆盖文件修改，推荐使用：

```bash
systemctl edit <服务名称>
```

查看合并后的最终配置：

```bash
systemctl cat <服务名称>
```

## 25. Start request repeated too quickly 排查

如果日志中出现：

```text
Start request repeated too quickly
```

说明服务在短时间内连续启动失败，触发了 systemd 的启动频率限制。

查看相关配置：

```bash
systemctl show <服务名称> -p Restart -p RestartUSec -p StartLimitBurst -p StartLimitIntervalUSec
```

清除失败状态：

```bash
systemctl reset-failed <服务名称>
```

然后修复真正的启动错误，再重新启动：

```bash
systemctl start <服务名称>
```

不能只通过 `reset-failed` 或提高重启次数解决问题。必须先查看前几次失败日志，找到服务反复退出的根本原因。

## 26. 启动超时排查

如果日志中出现：

```text
start operation timed out
```

需要检查：

- 应用初始化是否耗时过长。
- 数据库和下游服务连接是否超时。
- 服务是否等待一个永远不会完成的任务。
- 存储是否存在 IO 性能问题。
- systemd 的服务类型是否正确。
- `TimeoutStartSec` 是否过小。
- 服务是否正确发送启动完成通知。

查看启动超时配置：

```bash
systemctl show <服务名称> -p TimeoutStartUSec
```

Unit 文件中可以设置：

```ini
[Service]
TimeoutStartSec=120
```

增加超时时间只能解决合理的长时间启动问题。如果服务因为依赖异常或死锁而一直卡住，增加超时时间不能解决根本问题。

## 27. 启动后立即退出排查

服务启动命令执行成功，但主进程立即退出时，systemd 可能显示服务已经停止或启动失败。

常见原因包括：

- 应用启动后发生未捕获异常。
- 配置初始化失败。
- 主线程执行结束。
- 服务错误地进入后台。
- `Type` 配置不匹配。
- 启动脚本使用 `&` 将程序放到后台。
- PID 文件配置错误。
- 服务收到终止信号。
- 应用健康检查失败后主动退出。
- 服务被 OOM Killer 终止。

可以查看进程退出状态：

```bash
systemctl show <服务名称> -p ExecMainCode -p ExecMainStatus -p Result
```

常见退出方式包括：

- `code=exited`：程序主动退出并返回退出码。
- `code=killed`：进程被信号终止。
- `status=1/FAILURE`：程序返回通用失败状态。
- `status=9/KILL`：进程收到 `SIGKILL`。
- `status=15/TERM`：进程收到 `SIGTERM`。
- `status=203/EXEC`：systemd 无法执行启动程序。
- `status=217/USER`：服务用户配置存在问题。

## 28. 常见 systemd 退出码说明

### 28.1 203/EXEC

```text
status=203/EXEC
```

通常表示：

- `ExecStart` 路径错误。
- 程序不存在。
- 文件没有执行权限。
- 脚本解释器不存在。
- 文件系统禁止执行。
- SELinux 阻止执行。

### 28.2 217/USER

```text
status=217/USER
```

通常表示：

- `User` 配置错误。
- 用户不存在。
- 用户信息无法读取。
- systemd 无法切换到指定用户。

### 28.3 200/CHDIR

```text
status=200/CHDIR
```

通常表示：

- `WorkingDirectory` 不存在。
- 服务用户无权访问工作目录。

### 28.4 1/FAILURE

```text
status=1/FAILURE
```

表示应用程序主动返回退出码 1。需要查看应用日志确定具体原因。

### 28.5 9/KILL

```text
status=9/KILL
```

表示进程收到 `SIGKILL`，可能由以下情况造成：

- OOM Killer。
- 管理员执行 `kill -9`。
- 监控脚本强制终止。
- 容器达到资源限制。
- systemd 在停止超时后强制终止。

## 29. 检查内核日志

查看最近内核日志：

```bash
dmesg -T | tail -200
```

查看当前启动周期的内核日志：

```bash
journalctl -k -b
```

重点检查：

- OOM。
- 磁盘 IO 错误。
- 文件系统错误。
- 网卡异常。
- SELinux 拒绝。
- Segmentation Fault。
- 动态库或程序崩溃。
- 硬件故障。

查看程序崩溃：

```bash
dmesg -T | grep -iE "segfault|general protection|core dumped"
```

如果服务因为段错误退出，应进一步检查程序版本、动态库、Core Dump 和最近代码变更。

## 30. 检查 Core Dump

查看 Core Dump 列表：

```bash
coredumpctl list
```

查看指定服务：

```bash
coredumpctl list <程序名称>
```

查看详细信息：

```bash
coredumpctl info <PID或程序名称>
```

如果需要调试：

```bash
coredumpctl debug <PID或程序名称>
```

Core Dump 可能包含敏感数据，占用较大磁盘空间。保存和分析时应遵守系统的安全要求。

## 31. 检查服务版本和最近变更

查看软件版本：

```bash
<程序命令> --version
```

查看软件包信息：

```bash
rpm -qi <软件包名称>
```

或：

```bash
dpkg -l | grep <软件包名称>
```

重点确认服务启动失败前是否发生过：

- 应用发布。
- 配置修改。
- 操作系统升级。
- 软件包升级。
- Java、Python 或 Node.js 版本升级。
- 数据库升级。
- 证书更换。
- 密码修改。
- 防火墙调整。
- 目录迁移。
- 磁盘挂载变更。
- 用户和权限变更。

如果问题与变更时间高度一致，应优先对比变更前后的配置、程序包和依赖版本。

## 32. 检查证书问题

使用 HTTPS、数据库 TLS 或双向认证的服务，可能因为证书问题无法启动。

检查证书有效期：

```bash
openssl x509 -in /path/to/certificate.crt -noout -dates
```

查看证书信息：

```bash
openssl x509 -in /path/to/certificate.crt -noout -text
```

检查私钥：

```bash
openssl pkey -in /path/to/private.key -check
```

常见问题包括：

- 证书已过期。
- 证书和私钥不匹配。
- 服务用户没有读取权限。
- 证书路径错误。
- 证书格式不正确。
- 中间证书链不完整。
- 私钥设置了密码，但服务无法交互输入。
- 系统时间错误导致证书校验失败。

## 33. 检查系统时间

查看系统时间：

```bash
date
```

查看时间同步状态：

```bash
timedatectl
```

时间错误可能导致：

- TLS 证书验证失败。
- Token 被认为已过期或尚未生效。
- Kerberos 认证失败。
- 分布式系统节点无法正常通信。
- 日志时间混乱，增加排查难度。

查看时间同步服务：

```bash
systemctl status chronyd
```

或：

```bash
systemctl status systemd-timesyncd
```

## 34. Docker 容器服务启动失败排查

查看容器状态：

```bash
docker ps -a
```

查看容器日志：

```bash
docker logs --tail 200 <容器名称或ID>
```

实时查看：

```bash
docker logs -f <容器名称或ID>
```

查看容器完整配置：

```bash
docker inspect <容器名称或ID>
```

查看退出码：

```bash
docker inspect <容器名称或ID> --format '{{.State.ExitCode}}'
```

查看是否发生 OOM：

```bash
docker inspect <容器名称或ID> --format '{{.State.OOMKilled}}'
```

常见原因包括：

- 容器启动命令错误。
- 镜像中缺少程序或依赖。
- 环境变量缺失。
- 挂载文件不存在。
- 挂载目录权限错误。
- 容器端口冲突。
- 容器内存限制过小。
- 数据库等依赖服务不可用。
- 镜像架构与服务器 CPU 架构不匹配。
- 容器主进程执行结束。
- 健康检查持续失败。

Docker 容器必须有前台运行的主进程。如果启动脚本将应用放入后台后自身退出，容器也会随之退出。

## 35. Kubernetes Pod 启动失败排查

查看 Pod 状态：

```bash
kubectl get pods -n <namespace>
```

查看详细事件：

```bash
kubectl describe pod <pod-name> -n <namespace>
```

查看容器日志：

```bash
kubectl logs <pod-name> -n <namespace>
```

查看上一次退出前的日志：

```bash
kubectl logs <pod-name> -n <namespace> --previous
```

如果 Pod 中有多个容器：

```bash
kubectl logs <pod-name> -n <namespace> -c <容器名称>
```

常见状态包括：

### 35.1 CrashLoopBackOff

表示容器启动后反复退出。需要查看：

- 容器日志。
- 上一次退出日志。
- 启动命令。
- 环境变量。
- 配置文件。
- 依赖服务。
- 退出码。
- 健康检查。

### 35.2 ImagePullBackOff

表示镜像拉取失败，常见原因包括：

- 镜像名称或标签错误。
- 镜像不存在。
- 镜像仓库认证失败。
- 网络无法访问镜像仓库。
- `imagePullSecrets` 配置错误。

### 35.3 CreateContainerConfigError

常见原因包括：

- ConfigMap 不存在。
- Secret 不存在。
- 环境变量引用错误。
- 数据卷配置错误。
- 容器配置不完整。

### 35.4 OOMKilled

表示容器超过内存限制，需要检查应用内存使用和 Pod 的资源限制。

### 35.5 Readiness Probe Failed

容器可能已经启动，但尚未通过就绪检查，因此不会接收流量。

### 35.6 Liveness Probe Failed

存活检查持续失败后，容器会被 Kubernetes 重启。需要检查探针路径、端口、超时时间以及应用启动速度。

## 36. 常见处理措施

### 36.1 配置错误

处理建议：

- 使用服务自带命令检查配置语法。
- 对比变更前后的配置。
- 修复配置格式和错误参数。
- 确认服务实际加载的配置文件。
- 修复后重新启动并查看日志。

### 36.2 端口冲突

处理建议：

- 确认占用端口的进程。
- 正常停止旧进程。
- 避免重复启动服务。
- 修改服务监听端口。
- 修正容器端口映射。
- 增加端口冲突监控。

### 36.3 权限不足

处理建议：

- 设置正确的文件所有者和用户组。
- 按最小权限原则增加读写或执行权限。
- 检查父目录访问权限。
- 检查 SELinux 或 AppArmor 日志。
- 不要使用 `chmod -R 777` 作为通用解决方案。

### 36.4 依赖服务不可用

处理建议：

- 恢复依赖服务。
- 修正连接地址和认证信息。
- 检查网络、防火墙和 DNS。
- 为应用增加合理的连接重试。
- 对非关键依赖提供降级能力。
- 配置正确的服务启动顺序。

### 36.5 系统资源不足

处理建议：

- 清理安全可删除的磁盘文件。
- 增加内存或调整应用内存参数。
- 修复文件描述符泄漏。
- 调整合理的资源限制。
- 扩容磁盘、容器或服务器。
- 降低启动阶段的并发和数据加载量。

### 36.6 环境变量缺失

处理建议：

- 在 Unit 文件或 `EnvironmentFile` 中明确配置。
- 使用绝对路径。
- 确认服务运行用户可以读取环境文件。
- 检查变量中是否有特殊字符。
- 修改后执行 `systemctl daemon-reload`。

## 37. 不建议直接执行的操作

### 37.1 不建议直接执行 chmod 777

`chmod 777` 会赋予所有用户读、写和执行权限，可能带来严重安全风险。应定位具体缺少的权限并进行最小化调整。

### 37.2 不建议永久关闭 SELinux

关闭 SELinux 可能暂时绕过权限问题，但会降低系统安全性。应根据审计日志修正安全上下文或策略。

### 37.3 不建议直接删除 PID 文件

只有确认旧进程已经不存在、PID 文件确实失效时，才可以清理。

### 37.4 不建议连续重启服务

连续重启可能覆盖重要日志、加重依赖服务压力，甚至触发数据恢复或重复任务。

### 37.5 不建议直接重装服务

重装可能覆盖配置文件，但未必能解决环境变量、依赖服务、权限和资源问题，还可能破坏现有数据。

### 37.6 不建议只处理最后一条错误

最后一条日志通常只是失败结果，真正的根本原因可能出现在之前的日志中。

## 38. 监控与预防建议

建议持续监控以下指标：

- 服务运行状态。
- 服务重启次数。
- 服务启动耗时。
- 进程退出码。
- 端口监听状态。
- 健康检查结果。
- 服务日志错误数量。
- CPU 和内存使用率。
- 磁盘空间和 inode。
- 文件描述符使用率。
- 依赖服务可用性。
- 依赖接口响应时间。
- 证书有效期。
- 配置文件变更。
- OOM 和 Core Dump 事件。

建议为关键服务建立：

1. 自动健康检查。
2. 服务异常退出告警。
3. 端口不可用告警。
4. 依赖服务连通性监控。
5. 磁盘和内存容量告警。
6. 证书过期预警。
7. 发布后自动验证。
8. 配置文件版本管理。
9. 启动失败日志留存。
10. 规范的回滚方案。

## 39. 推荐排查流程

Linux 服务启动失败时，可以按照以下顺序排查：

1. 使用 `systemctl status` 确认服务状态和退出码。
2. 使用 `journalctl -u` 查看完整启动日志。
3. 查看应用自身日志。
4. 使用 `systemctl cat` 检查 Unit 文件。
5. 检查 `ExecStart`、`WorkingDirectory`、`User` 和环境变量。
6. 使用服务运行用户手动执行启动命令。
7. 使用服务自带命令检查配置文件语法。
8. 使用 `ss` 或 `lsof` 检查端口冲突。
9. 检查程序、脚本、配置和目录是否存在。
10. 检查文件所有者、用户组和权限。
11. 检查 SELinux 或 AppArmor 拒绝日志。
12. 检查动态库、运行环境和程序依赖。
13. 检查数据库、Redis、消息队列等依赖服务。
14. 检查 DNS、网络和防火墙。
15. 检查磁盘空间、inode、内存和 OOM。
16. 检查文件描述符、线程和进程限制。
17. 检查 PID 文件和旧进程。
18. 检查服务版本和最近变更。
19. 修改 Unit 文件后执行 `systemctl daemon-reload`。
20. 重新启动服务并验证端口、日志和业务接口。
21. 持续观察服务是否稳定运行。

## 40. 常用排查命令汇总

```bash
# 查看服务状态
systemctl status <服务名称> --no-pager -l

# 查看服务日志
journalctl -u <服务名称> -n 200 --no-pager

# 查看本次开机后的服务日志
journalctl -u <服务名称> -b

# 查看详细错误
journalctl -xeu <服务名称>

# 实时查看服务日志
journalctl -u <服务名称> -f

# 查看 Unit 文件
systemctl cat <服务名称>

# 查看服务完整属性
systemctl show <服务名称>

# 查看启动命令和运行用户
systemctl show <服务名称> -p ExecStart -p User -p Group -p WorkingDirectory

# 检查 Unit 文件语法
systemd-analyze verify /path/to/example.service

# 修改 Unit 后重新加载
systemctl daemon-reload

# 清除服务失败状态
systemctl reset-failed <服务名称>

# 查看服务进程
ps -ef | grep <进程名称>

# 查看端口监听
ss -lntp

# 查看指定端口
ss -lntp | grep ':<端口>'

# 查看端口占用进程
lsof -i :<端口>

# 查看文件权限
ls -l /path/to/file

# 查看完整路径各级权限
namei -l /path/to/file

# 使用服务用户测试读取
sudo -u <服务用户> test -r /path/to/file && echo readable

# 使用服务用户测试写入
sudo -u <服务用户> test -w /path/to/directory && echo writable

# 查看 SELinux 状态
getenforce
sestatus

# 查看 SELinux 拒绝日志
ausearch -m AVC -ts recent

# 查看程序动态库
ldd /path/to/program

# 查看 Java 版本
java -version

# 检查 Python 依赖
python3 -m pip check

# 测试依赖端口
nc -vz -w 3 <依赖地址> <端口>

# 测试依赖 HTTP 接口
curl -v --connect-timeout 3 --max-time 10 http://<依赖地址>/

# 查看磁盘空间
df -hT

# 查看 inode
df -i

# 查看内存
free -h

# 查看 OOM 日志
dmesg -T | grep -iE "out of memory|oom|killed process"

# 查看系统资源限制
ulimit -a

# 查看 Unit 文件描述符限制
systemctl show <服务名称> -p LimitNOFILE -p LimitNPROC

# 查看内核日志
dmesg -T | tail -200

# 查看 Core Dump
coredumpctl list

# 检查证书有效期
openssl x509 -in /path/to/certificate.crt -noout -dates

# 查看系统时间
date
timedatectl

# 查看 Docker 容器状态
docker ps -a

# 查看 Docker 容器日志
docker logs --tail 200 <容器名称或ID>

# 查看 Kubernetes Pod 状态
kubectl get pods -n <namespace>

# 查看 Kubernetes Pod 事件
kubectl describe pod <pod-name> -n <namespace>

# 查看 Kubernetes 上一次退出日志
kubectl logs <pod-name> -n <namespace> --previous
```

## 41. 排查结论模板

### 故障现象

应用服务执行启动命令后立即失败，systemd 状态显示 `failed`，服务端口没有正常监听。

### 故障确认

通过 `systemctl status` 和 `journalctl -u` 查看日志，发现服务启动过程中提示无法写入日志目录。使用服务运行用户测试后，确认该用户对日志目录没有写入权限。

### 根本原因

应用发布过程中重新创建了日志目录，新目录的所有者被设置为 root，而 systemd Unit 文件配置服务使用普通应用用户运行。应用初始化日志组件时无法创建日志文件，因此主进程返回退出码 1。

### 临时处理

将日志目录所有者和用户组调整为正确的应用用户，并按照最小权限原则增加必要的目录写入权限，随后重新启动服务。

### 永久修复

修改应用发布脚本，在创建日志、数据和临时目录时统一设置正确的所有者和权限。同时在发布检查中增加服务用户读写权限验证，避免目录被重新创建后权限发生变化。

### 验证结果

服务重新启动后状态显示为 `active (running)`，目标端口正常监听，应用日志可以正常写入，业务健康检查和接口访问均恢复正常。持续观察期间服务未再次退出。

## 42. 总结

Linux 服务启动失败的原因通常集中在配置、端口、权限、环境变量、程序依赖、外部依赖和系统资源等方面。排查时最重要的是先查看完整日志和退出状态，而不是反复执行启动命令。

`systemctl status` 可以帮助确认服务状态和退出码，`journalctl` 可以查看 systemd 收集的启动日志，应用自身日志通常包含更加具体的异常信息。定位问题后，应继续检查 Unit 文件、实际启动命令、运行用户、工作目录、配置文件和端口监听情况。

如果应用本身配置正常，还需要排查数据库、Redis、消息队列等依赖服务，以及磁盘空间、内存、文件描述符和 SELinux 等系统层问题。对于 Docker 和 Kubernetes 环境，还应检查容器退出码、OOM 状态、配置挂载和健康检查。

修复后不能只确认 `systemctl start` 没有报错，还需要确认服务进程持续存在、端口正常监听、日志没有新增异常、依赖连接正常，并通过真实业务接口或健康检查验证服务已经恢复。