# MySQL 服务不可用排查

## 1. 问题概述

MySQL 服务不可用是企业应用运行过程中常见的数据库故障类型，通常表现为应用无法连接数据库、数据库健康检查失败、接口请求大量报错、服务启动失败等问题。

在微服务或分布式系统中，MySQL 通常作为核心数据存储组件，一旦数据库服务不可用，上层应用往往无法正常完成数据查询、写入以及事务处理，进而导致业务功能异常。

常见故障表现包括：

- 应用启动失败，提示数据库连接失败。
- Spring Boot 应用健康检查中 MySQL 状态为 DOWN。
- 接口请求大量返回数据库连接异常。
- HikariCP 等数据库连接池无法获取连接。
- Prometheus 监控指标显示数据库探测失败。
- 日志中出现以下异常：

```text
Communications link failure

Connection refused

Too many connections

Access denied for user

Cannot connect to MySQL server
```

MySQL 服务不可用问题通常涉及多个层面，包括 MySQL 进程状态、网络连接、配置错误、权限问题、连接资源耗尽以及磁盘资源不足等，需要按照由外到内的方式进行系统排查。

## 2. 常见原因分析

### 2.1 MySQL 服务进程停止

MySQL 服务停止是最直接的导致数据库不可用的原因。

可能原因：

- 手动停止 MySQL 服务。
- Docker 容器异常退出。
- 服务器重启后 MySQL 未自动启动。
- MySQL 启动过程中发生异常。
- 数据目录损坏导致启动失败。

常见现象：

应用连接数据库时报错：

```text
Connection refused
```

检查 MySQL 服务状态。

Linux 环境：

```bash
systemctl status mysql
```

或者：

```bash
systemctl status mysqld
```

如果服务未启动：

```bash
systemctl start mysql
```

Docker 环境下查看容器状态：

```bash
docker ps -a
```

如果 MySQL 容器停止：

```bash
docker start mysql-container
```

查看启动日志：

```bash
docker logs mysql-container
```

重点关注：

- InnoDB 初始化失败。
- 数据目录权限错误。
- 配置文件解析失败。
- 端口绑定失败。

## 3. 网络连接异常排查

如果 MySQL 服务正常运行，但应用无法访问，通常需要检查网络连接。

### 3.1 检查 MySQL 端口监听

MySQL 默认监听端口：

```text
3306
```

Linux 环境执行：

```bash
netstat -tunlp | grep 3306
```

或者：

```bash
ss -tunlp | grep 3306
```

正常情况下应该看到：

```text
LISTEN 0 128 *:3306
```

如果没有监听，说明 MySQL 没有正常启动或者端口配置异常。

### 3.2 测试端口连通性

在客户端机器执行：

```bash
telnet mysql_host 3306
```

或者：

```bash
nc -vz mysql_host 3306
```

如果返回：

```text
Connection refused
```

说明目标端口没有服务监听。

如果返回：

```text
Connection timeout
```

说明可能存在以下问题：

- 防火墙限制。
- 网络不可达。
- 安全组规则限制。

### 3.3 检查 MySQL 绑定地址

查看 MySQL 配置：

```ini
[mysqld]
bind-address=0.0.0.0
```

如果配置为：

```ini
bind-address=127.0.0.1
```

表示只允许本机访问，远程应用连接时会失败。

修改后需要重启 MySQL：

```bash
systemctl restart mysql
```

## 4. 数据库连接配置检查

应用连接数据库通常依赖配置文件。

例如 Spring Boot：

```yaml
spring:
  datasource:
    url: jdbc:mysql://localhost:3306/database
    username: root
    password: password
```

需要重点检查以下配置。

### 4.1 数据库地址

确认：

- `host` 是否正确。
- `port` 是否正确。
- 数据库名称是否存在。

错误示例：

```text
jdbc:mysql://localhost:3307/test
```

实际地址：

```text
jdbc:mysql://localhost:3306/test
```

端口配置错误会导致应用连接失败。

### 4.2 用户名或密码错误

密码错误时通常会出现：

```text
Access denied for user
```

检查数据库用户：

```sql
SELECT user, host FROM mysql.user;
```

确认应用使用的账号是否存在。

例如，创建用户：

```sql
CREATE USER 'app'@'%' IDENTIFIED BY 'password';
```

为用户授权：

```sql
GRANT ALL PRIVILEGES ON database.* TO 'app'@'%';
```

刷新权限：

```sql
FLUSH PRIVILEGES;
```

## 5. 最大连接数耗尽排查

MySQL 可用连接数有限，当大量请求同时访问数据库时，可能导致连接耗尽。

典型错误：

```text
Too many connections
```

查看最大连接数：

```sql
SHOW VARIABLES LIKE 'max_connections';
```

查看当前连接数量：

```sql
SHOW STATUS LIKE 'Threads_connected';
```

查看当前连接：

```sql
SHOW PROCESSLIST;
```

重点关注：

- 长时间执行的 SQL。
- `Sleep` 状态连接过多。
- 大量异常客户端连接。

临时增加最大连接数：

```sql
SET GLOBAL max_connections = 500;
```

永久修改需要编辑 MySQL 配置文件：

```ini
[mysqld]
max_connections=500
```

修改完成后重启 MySQL。

## 6. 数据库异常导致服务不可用

除了连接问题，MySQL 内部异常也可能导致服务不可用。

### 6.1 InnoDB 恢复失败

MySQL 启动日志中可能出现：

```text
InnoDB: Unable to lock ./ibdata1
```

或者：

```text
InnoDB corruption
```

可能原因：

- MySQL 非正常关闭。
- 磁盘损坏。
- 数据文件损坏。

查看错误日志：

```bash
cat /var/log/mysql/error.log
```

Docker 环境查看日志：

```bash
docker logs mysql-container
```

### 6.2 磁盘空间不足

MySQL 写入数据需要足够的磁盘空间。

查看磁盘使用情况：

```bash
df -h
```

如果磁盘使用率达到 100%，例如：

```text
/dev/sda 100%
```

可能导致：

- Binary Log 无法写入。
- 临时表创建失败。
- 数据更新失败。
- MySQL 无法正常启动。

可以清理不再需要的 Binary Log：

```sql
PURGE BINARY LOGS BEFORE '2026-01-01';
```

如果清理后空间仍然不足，应扩容磁盘空间。

## 7. MySQL 服务不可用完整排查流程

```text
                MySQL 不可用告警
                        |
                        ↓
               检查 MySQL 进程状态
                        |
               +--------+--------+
               |                 |
             正常               异常
               |                 |
               ↓                 ↓
        检查网络连接          启动服务
               |
               ↓
        检查 3306 端口
               |
               ↓
        检查账号权限
               |
               ↓
        检查连接数量
               |
               ↓
        检查 MySQL 错误日志
               |
               ↓
        恢复服务并验证
```

## 8. 监控指标建议

为了及时发现 MySQL 服务异常，可以重点关注以下指标。

### 8.1 服务状态

```text
mysql_up
```

用于判断 MySQL 是否正常运行。

### 8.2 当前连接数量

```text
Threads_connected
```

用于观察数据库连接压力。

### 8.3 最大连接使用率

计算方式：

```text
Threads_connected / max_connections
```

当连接使用率持续升高时，应检查连接池配置、慢 SQL 和异常连接释放情况。

### 8.4 查询错误数量

重点关注：

- 查询失败次数。
- 数据库连接失败次数。
- 事务回滚次数。
- SQL 执行异常次数。

### 8.5 慢查询数量

重点监控：

- 慢查询增长速度。
- SQL 执行耗时。
- 长事务数量。
- 扫描行数异常的查询。

## 9. 故障恢复建议

### 9.1 MySQL 服务停止

启动服务：

```bash
systemctl start mysql
```

Docker 环境启动容器：

```bash
docker start mysql-container
```

### 9.2 连接数量耗尽

查找并终止异常连接：

```sql
KILL connection_id;
```

同时检查应用连接池是否正确释放连接。

### 9.3 配置错误

修正 MySQL 配置或应用数据库连接配置，然后重启相关服务。

### 9.4 磁盘空间不足

清理无用日志、临时文件和过期备份，释放磁盘空间后重新启动 MySQL。

## 10. 长期优化建议

### 10.1 增加数据库健康检查

可以在应用中增加数据库健康检查接口：

```text
/health/mysql
```

通过 Prometheus 定期检测数据库连接状态和服务可用性。

### 10.2 完善数据库监控

建议接入：

- MySQL Exporter。
- Prometheus。
- Grafana Dashboard。

重点监控：

- QPS。
- TPS。
- 当前连接数。
- 最大连接使用率。
- 锁等待。
- InnoDB Buffer Pool。
- 慢查询。
- 磁盘使用率。
- MySQL 服务状态。

### 10.3 建立自动化故障诊断能力

在 AIOps 场景中，MySQL 服务不可用诊断应结合：

- Prometheus 指标。
- MySQL 错误日志。
- 应用异常日志。
- 数据库连接池状态。
- Runbook 知识库。

形成完整证据链：

```text
MySQL 不可用告警
        |
        ↓
检查 probe_success
        |
        ↓
查询数据库连接状态
        |
        ↓
分析错误日志
        |
        ↓
定位服务停止、网络、权限或资源问题
        |
        ↓
生成诊断报告
```

通过以上流程，可以快速定位 MySQL 服务不可用原因，并降低数据库故障对业务系统造成的影响。