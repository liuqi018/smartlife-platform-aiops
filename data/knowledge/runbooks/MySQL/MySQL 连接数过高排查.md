# MySQL 连接数过高排查

## 1. 问题概述

MySQL 连接数过高是指客户端与 MySQL 建立的连接数量持续增加，接近或达到数据库允许的最大连接数，导致新的连接无法建立，应用请求失败或数据库整体性能下降。

MySQL 连接数包括正在执行 SQL 的活跃连接，也包括已经建立但暂时没有执行任务的空闲连接。连接数高并不一定代表数据库正在处理大量请求，还可能是连接池配置过大、连接没有释放、慢 SQL、锁等待或空闲连接过多造成的。

当连接数达到 `max_connections` 后，新客户端可能收到以下错误：

```text
Too many connections
```

连接数过高还可能引发：

1. 应用无法获取数据库连接。
2. 数据库连接池等待线程增加。
3. 接口响应时间明显上升。
4. 大量请求超时。
5. MySQL 内存占用升高。
6. 线程调度和上下文切换增加。
7. 数据库 CPU 和磁盘 IO 压力升高。
8. 慢 SQL和锁等待问题进一步放大。
9. 监控、运维账号也无法正常连接数据库。

排查的核心目标包括：

1. 确认当前连接数和最大连接数。
2. 区分活跃连接与空闲连接。
3. 确认连接来自哪些主机、用户和应用。
4. 定位连接持续增长的原因。
5. 判断是否存在连接泄漏、慢 SQL 或锁等待。
6. 采取临时限流、终止异常连接或扩容措施。
7. 从应用连接池、SQL、事务和数据库配置上解决根本问题。

## 2. 常见问题现象

MySQL 连接数过高时，通常会出现以下现象：

1. 应用日志出现 `Too many connections`。
2. 应用无法获取数据库连接。
3. 数据库连接池获取连接超时。
4. 接口请求大量失败或超时。
5. MySQL `Threads_connected` 接近 `max_connections`。
6. `Threads_running` 持续升高。
7. `SHOW PROCESSLIST` 中存在大量连接。
8. 大量连接处于 `Sleep` 状态。
9. 大量连接执行相同 SQL。
10. 大量连接处于锁等待状态。
11. 慢 SQL 执行时间明显增加。
12. MySQL CPU 和内存使用率升高。
13. Java 线程池中大量线程等待数据库连接。
14. 多个应用实例同时出现连接池耗尽。
15. 数据库重启后暂时恢复，随后连接数再次增长。

常见应用异常包括：

```text
Too many connections
```

```text
Connection is not available, request timed out
```

```text
Timeout waiting for connection from pool
```

```text
Unable to acquire JDBC Connection
```

```text
Could not open JDBC Connection for transaction
```

这些异常可能来自 MySQL 达到连接上限，也可能来自应用自身连接池已经耗尽，需要分别检查。

## 3. MySQL 连接相关指标

### 3.1 max_connections

表示 MySQL 允许同时建立的最大客户端连接数。

查看：

```sql
SHOW VARIABLES LIKE 'max_connections';
```

### 3.2 Threads_connected

表示当前已经建立的连接数量，包括活跃连接和空闲连接。

查看：

```sql
SHOW GLOBAL STATUS LIKE 'Threads_connected';
```

### 3.3 Threads_running

表示当前正在运行，而不是处于 Sleep 状态的线程数量。

查看：

```sql
SHOW GLOBAL STATUS LIKE 'Threads_running';
```

如果 `Threads_connected` 很高，但 `Threads_running` 较低，通常说明存在大量空闲连接。

如果两者都很高，说明数据库正在同时处理大量请求，或者大量线程被慢 SQL 和锁等待占用。

### 3.4 Max_used_connections

表示 MySQL 启动以来曾经达到的最大连接数。

查看：

```sql
SHOW GLOBAL STATUS LIKE 'Max_used_connections';
```

该指标可以用于评估历史连接峰值，但 MySQL 重启后会重新统计。

### 3.5 Connections

表示 MySQL 启动以来接收的连接尝试总数。

```sql
SHOW GLOBAL STATUS LIKE 'Connections';
```

### 3.6 Aborted_connects

表示连接建立失败的累计次数。

```sql
SHOW GLOBAL STATUS LIKE 'Aborted_connects';
```

如果该指标快速增长，应检查：

- 用户名或密码错误。
- 权限不足。
- 网络异常。
- MySQL 达到连接上限。
- TLS 或认证问题。
- 连接握手超时。

## 4. 连接数过高的常见原因

### 4.1 应用连接池配置过大

每个应用实例都可能维护独立连接池。如果单个实例最大连接数为 100，部署 20 个实例，理论上可能建立 2000 个连接。

```text
数据库总连接需求
≈ 应用实例数 × 单实例连接池最大连接数
+ 运维连接
+ 监控连接
+ 定时任务连接
+ 其他系统连接
```

如果没有进行整体容量规划，很容易超过 MySQL 最大连接数。

### 4.2 数据库连接泄漏

应用获取连接后没有正确关闭或归还连接池，会导致连接池中的可用连接逐渐减少。

常见原因包括：

- 异常路径没有关闭连接。
- 手动使用 JDBC 后没有执行 `close()`。
- ResultSet、Statement 或 Connection 未关闭。
- 事务没有正确结束。
- 自定义数据库组件管理错误。
- 连接对象被长期保存。

使用连接池时，`Connection.close()` 通常表示将连接归还连接池，而不一定是真正断开数据库连接。

### 4.3 慢 SQL 占用连接

SQL 执行时间过长时，连接无法及时归还连接池。请求量不变的情况下，慢 SQL 也可能导致活跃连接不断增加。

### 4.4 锁等待

SQL 等待其他事务释放锁时，会持续占用数据库连接。大量锁等待可能快速耗尽应用连接池和 MySQL 连接数。

### 4.5 长事务

事务长时间不提交会持续占用连接，并可能持有大量锁。

常见原因包括：

- 事务中调用远程接口。
- 事务中执行文件处理。
- 批量任务事务范围过大。
- 手动事务忘记提交。
- 异常后未正确回滚。

### 4.6 空闲连接过多

连接池通常会保留一定数量的空闲连接，以避免频繁建立连接。如果多个应用实例都保留大量空闲连接，数据库连接数可能长期偏高。

### 4.7 突发流量

业务请求量突然增加，连接池可能快速扩展到最大值。如果数据库处理能力不足，连接会长期占用并形成堆积。

### 4.8 连接建立和关闭过于频繁

应用没有使用连接池，或者连接池失效，导致每次请求都重新连接数据库。虽然连接可能不会长期保持，但会增加连接创建、认证和线程管理开销。

### 4.9 应用实例数量增加

应用扩容后，如果没有同步调整数据库连接容量，每个新实例都会建立新的连接池连接。

### 4.10 数据库参数配置不合理

`max_connections` 设置过小可能无法满足正常业务需求；设置过大则可能让过多请求同时进入数据库，导致内存、CPU 和 IO 被耗尽。

## 5. 排查前的注意事项

生产环境排查连接数过高时，应注意：

1. 不要立即执行大范围 `KILL`。
2. 不要只提高 `max_connections`。
3. 不要重启 MySQL 作为首选方案。
4. 不要只查看连接总数，还要区分连接状态。
5. 不要只处理数据库端，还要检查应用连接池。
6. 终止事务连接前应确认回滚影响。
7. 应记录连接来源、用户和执行 SQL。
8. 应保存连接数变化和问题发生时间。
9. 应检查最近是否扩容了应用实例。
10. 应预留运维和监控连接。

建议先保存：

```sql
SHOW VARIABLES LIKE 'max_connections';
SHOW GLOBAL STATUS LIKE 'Threads_connected';
SHOW GLOBAL STATUS LIKE 'Threads_running';
SHOW GLOBAL STATUS LIKE 'Max_used_connections';
SHOW FULL PROCESSLIST;
SELECT * FROM information_schema.INNODB_TRX;
```

## 6. 第一步：查看连接数和上限

执行：

```sql
SHOW VARIABLES LIKE 'max_connections';
```

查看当前连接数：

```sql
SHOW GLOBAL STATUS LIKE 'Threads_connected';
```

查看活跃连接数：

```sql
SHOW GLOBAL STATUS LIKE 'Threads_running';
```

查看历史最大连接数：

```sql
SHOW GLOBAL STATUS LIKE 'Max_used_connections';
```

可以计算连接使用率：

```text
连接使用率 = Threads_connected / max_connections × 100%
```

如果当前连接数已经接近上限，应尽快分析连接状态和来源。

但连接使用率不能单独反映数据库压力。例如：

- 500 个 Sleep 连接可能对 CPU 影响不大，但会占用连接额度和部分内存。
- 100 个同时运行复杂 SQL 的连接可能已经让数据库严重过载。

## 7. 第二步：查看当前连接状态

执行：

```sql
SHOW FULL PROCESSLIST;
```

也可以查询：

```sql
SELECT
    ID,
    USER,
    HOST,
    DB,
    COMMAND,
    TIME,
    STATE,
    INFO
FROM information_schema.PROCESSLIST
ORDER BY TIME DESC;
```

重点关注：

- `COMMAND` 是否为 `Sleep` 或 `Query`。
- `TIME` 是否过长。
- `STATE` 是否为锁等待。
- 是否存在大量相同 SQL。
- 是否有异常来源主机。
- 是否有某个用户建立大量连接。
- 是否存在长时间空闲事务。
- 是否存在长时间运行的查询。

## 8. 第三步：统计连接状态

按照命令状态统计：

```sql
SELECT
    COMMAND,
    COUNT(*) AS connection_count
FROM information_schema.PROCESSLIST
GROUP BY COMMAND
ORDER BY connection_count DESC;
```

按照运行状态统计：

```sql
SELECT
    STATE,
    COUNT(*) AS connection_count
FROM information_schema.PROCESSLIST
GROUP BY STATE
ORDER BY connection_count DESC;
```

如果大量连接为：

```text
Sleep
```

应重点检查连接池的最小空闲连接数、最大连接数和空闲回收策略。

如果大量连接为：

```text
Query
```

或者处于同一种执行状态，应继续检查 SQL、锁等待和数据库资源。

## 9. 第四步：统计连接来源

按照数据库用户统计：

```sql
SELECT
    USER,
    COUNT(*) AS connection_count
FROM information_schema.PROCESSLIST
GROUP BY USER
ORDER BY connection_count DESC;
```

按照客户端主机统计：

```sql
SELECT
    SUBSTRING_INDEX(HOST, ':', 1) AS client_host,
    COUNT(*) AS connection_count
FROM information_schema.PROCESSLIST
GROUP BY client_host
ORDER BY connection_count DESC;
```

按照用户和主机统计：

```sql
SELECT
    USER,
    SUBSTRING_INDEX(HOST, ':', 1) AS client_host,
    COUNT(*) AS connection_count
FROM information_schema.PROCESSLIST
GROUP BY USER, client_host
ORDER BY connection_count DESC;
```

通过这些信息可以确认：

- 哪个应用占用了最多连接。
- 是否某个实例连接数异常。
- 是否有测试程序误连生产数据库。
- 是否存在异常脚本或任务。
- 是否有连接来源突然增加。
- 应用扩容后连接总量是否同步增加。

## 10. 第五步：统计空闲连接

查看长时间 Sleep 的连接：

```sql
SELECT
    ID,
    USER,
    HOST,
    DB,
    COMMAND,
    TIME
FROM information_schema.PROCESSLIST
WHERE COMMAND = 'Sleep'
ORDER BY TIME DESC;
```

统计空闲连接：

```sql
SELECT
    USER,
    SUBSTRING_INDEX(HOST, ':', 1) AS client_host,
    COUNT(*) AS sleep_count,
    MAX(TIME) AS max_sleep_seconds
FROM information_schema.PROCESSLIST
WHERE COMMAND = 'Sleep'
GROUP BY USER, client_host
ORDER BY sleep_count DESC;
```

大量 Sleep 连接可能来自正常连接池，也可能由以下问题造成：

- 连接池最大连接数过大。
- 最小空闲连接数过大。
- 空闲连接回收时间过长。
- 应用实例数量过多。
- 连接池没有正常关闭。
- 临时脚本建立连接后长期不退出。

不能看到 Sleep 连接就全部终止。正常连接池需要保留一定空闲连接，以降低频繁建连成本。

## 11. 第六步：检查 wait_timeout

查看非交互连接空闲超时：

```sql
SHOW VARIABLES LIKE 'wait_timeout';
```

查看交互连接空闲超时：

```sql
SHOW VARIABLES LIKE 'interactive_timeout';
```

`wait_timeout` 表示非交互连接在空闲多长时间后由 MySQL 关闭。

如果设置过大，异常空闲连接可能长时间保留；如果设置过小，连接池中的连接可能被数据库关闭，应用复用时出现失效连接。

数据库超时应与连接池的以下参数协调：

- 最大连接生命周期。
- 空闲连接回收时间。
- 连接保活时间。
- 连接校验机制。
- 最小空闲连接数。

通常应让连接池主动淘汰连接，而不是长期依赖 MySQL 强制关闭。

## 12. 第七步：检查慢 SQL

查看当前长时间执行的 SQL：

```sql
SELECT
    ID,
    USER,
    HOST,
    DB,
    TIME,
    STATE,
    INFO
FROM information_schema.PROCESSLIST
WHERE COMMAND <> 'Sleep'
ORDER BY TIME DESC;
```

查看慢查询日志配置：

```sql
SHOW VARIABLES LIKE 'slow_query_log';
SHOW VARIABLES LIKE 'long_query_time';
SHOW VARIABLES LIKE 'slow_query_log_file';
```

慢 SQL 会延长单个连接的使用时间。根据近似关系：

```text
并发连接需求 ≈ 请求速率 × 单次数据库操作耗时
```

例如，每秒 100 个数据库请求，平均执行 0.1 秒，理论并发约为 10；如果 SQL 变慢到 2 秒，理论并发可能上升到 200。

因此，连接数突然升高时，应检查 SQL 平均耗时是否同步增加。

## 13. 第八步：检查锁等待和长事务

查看当前事务：

```sql
SELECT
    trx_id,
    trx_state,
    trx_started,
    trx_wait_started,
    trx_mysql_thread_id,
    trx_query,
    trx_rows_locked,
    trx_rows_modified
FROM information_schema.INNODB_TRX
ORDER BY trx_started;
```

查看锁等待：

```sql
SELECT *
FROM performance_schema.data_lock_waits;
```

如果存在 `sys` Schema：

```sql
SELECT *
FROM sys.innodb_lock_waits;
```

锁等待会使连接长期无法归还。应找出阻塞链最上游的持锁事务，而不是只终止等待事务。

长事务还可能表现为连接处于 Sleep 状态，但事务仍未提交。此时该连接仍然持有事务资源和锁。

## 14. 第九步：检查应用连接池

Java 应用常见连接池包括：

- HikariCP。
- Druid。
- DBCP。
- Tomcat JDBC Pool。

需要查看：

- 最大连接数。
- 最小空闲连接数。
- 当前活跃连接数。
- 当前空闲连接数。
- 等待连接线程数。
- 连接获取时间。
- 连接超时次数。
- 连接创建数量。
- 连接关闭数量。
- 连接泄漏告警。

以 HikariCP 为例，常见配置包括：

```properties
spring.datasource.hikari.maximum-pool-size=20
spring.datasource.hikari.minimum-idle=5
spring.datasource.hikari.connection-timeout=3000
spring.datasource.hikari.idle-timeout=600000
spring.datasource.hikari.max-lifetime=1800000
spring.datasource.hikari.keepalive-time=300000
spring.datasource.hikari.leak-detection-threshold=60000
```

参数值应根据业务、数据库容量和环境实际情况设置，不能直接照搬。

## 15. 第十步：排查连接泄漏

连接泄漏常见特征包括：

1. 连接池活跃连接数持续上升。
2. 业务流量下降后活跃连接数不下降。
3. 数据库连接数随时间不断增长。
4. 应用最终无法获取新连接。
5. 重启应用后暂时恢复。
6. 连接池出现 Leak Detection 告警。
7. 线程 Dump 中存在长期持有连接的线程。

错误示例：

```java
Connection connection = dataSource.getConnection();
PreparedStatement statement = connection.prepareStatement(sql);
ResultSet resultSet = statement.executeQuery();
// 异常时没有关闭资源
```

建议使用 try-with-resources：

```java
try (
    Connection connection = dataSource.getConnection();
    PreparedStatement statement = connection.prepareStatement(sql);
    ResultSet resultSet = statement.executeQuery()
) {
    while (resultSet.next()) {
        // 处理数据
    }
}
```

使用 Spring、MyBatis 或 JPA 时，也应检查：

- 是否手动获取连接。
- 事务是否正确结束。
- 异步线程是否错误复用事务资源。
- 流式查询结果是否正确关闭。
- 数据库游标是否及时释放。
- 自定义插件是否持有连接。

## 16. 第十一步：检查连接池总容量

数据库连接池不能只按照单个应用实例配置，应计算所有应用的总连接上限。

例如：

```text
订单服务：10 个实例 × 30 = 300
用户服务：8 个实例 × 20 = 160
库存服务：6 个实例 × 30 = 180
报表服务：4 个实例 × 50 = 200
其他连接和预留：100
总需求上限：940
```

如果 MySQL：

```text
max_connections = 800
```

那么所有连接池同时扩展时就可能超过数据库上限。

需要为以下连接预留容量：

- 运维连接。
- 监控连接。
- 数据迁移。
- 备份任务。
- 定时任务。
- 管理平台。
- 故障排查连接。
- 主从和系统线程。

## 17. 第十二步：检查 MySQL 资源使用

连接数提高后，每个连接都可能使用线程栈、会话缓冲区和执行内存。

查看系统资源：

```bash
top
```

```bash
free -h
```

```bash
iostat -x 1 10
```

重点关注：

- MySQL CPU。
- 系统可用内存。
- Swap。
- 磁盘 IO。
- Load Average。
- 上下文切换。
- MySQL 进程线程数。

不能因为物理内存看起来充足，就无限提高 `max_connections`。许多 MySQL 内存缓冲区是按连接或按执行操作分配的，高并发连接可能造成较大的瞬时内存消耗。

## 18. 第十三步：检查连接建立频率

查看累计连接数：

```sql
SHOW GLOBAL STATUS LIKE 'Connections';
```

查看线程缓存命中相关指标：

```sql
SHOW GLOBAL STATUS LIKE 'Threads_created';
```

查看线程缓存配置：

```sql
SHOW VARIABLES LIKE 'thread_cache_size';
```

如果 `Connections` 和 `Threads_created` 增长很快，可能说明应用频繁建立新连接。

常见原因包括：

- 未使用连接池。
- 连接池不断销毁和创建连接。
- `maxLifetime` 设置过短。
- MySQL 空闲超时小于连接池生命周期。
- 网络不稳定导致连接频繁重建。
- 应用健康检查频繁新建数据库连接。
- 大量短生命周期脚本访问数据库。

## 19. 临时处理措施

MySQL 连接数已经接近上限时，可以采取：

1. 保存当前连接和事务信息。
2. 对高流量接口临时限流。
3. 暂停非核心定时任务和批处理任务。
4. 暂停异常脚本或测试程序。
5. 处理慢 SQL 和锁阻塞。
6. 终止确认无用的长期空闲连接。
7. 终止确认可以安全终止的异常查询。
8. 降低应用连接池最大值。
9. 将只读流量切换到从库。
10. 必要时临时提高 `max_connections`。
11. 增加数据库只读实例或应用实例。
12. 为核心业务预留连接。

临时修改最大连接数：

```sql
SET GLOBAL max_connections = 1000;
```

该操作只对新连接生效，并且数据库重启后可能恢复配置文件中的值。修改前必须评估内存、CPU、磁盘和数据库处理能力。

## 20. 终止异常连接

终止当前查询：

```sql
KILL QUERY <连接ID>;
```

终止整个连接：

```sql
KILL CONNECTION <连接ID>;
```

生成终止长时间 Sleep 连接的语句：

```sql
SELECT CONCAT('KILL CONNECTION ', ID, ';')
FROM information_schema.PROCESSLIST
WHERE COMMAND = 'Sleep'
  AND TIME > 3600;
```

不要直接执行生成结果，应先人工确认：

- 连接来自哪个应用。
- 是否处于未提交事务中。
- 是否会触发事务回滚。
- 是否属于核心业务。
- 连接池是否会立即重建连接。

如果根本原因是连接池配置过大，终止连接后应用可能马上重新建立，无法长期解决问题。

## 21. 永久解决方案

### 21.1 优化连接池配置

- 合理设置最大连接数。
- 减少不必要的最小空闲连接。
- 设置连接获取超时。
- 配置合理的空闲回收时间。
- 让连接生命周期短于数据库强制空闲关闭时间。
- 启用连接泄漏检测。
- 监控连接池状态。
- 避免每个业务模块创建独立连接池。

### 21.2 修复连接泄漏

- 使用 try-with-resources。
- 正确关闭 ResultSet、Statement 和 Connection。
- 确保异常路径释放连接。
- 正确提交或回滚事务。
- 关闭流式查询和游标。
- 检查自定义数据库组件。
- 增加连接泄漏测试。

### 21.3 优化慢 SQL

通过索引、分页、SQL 改写和数据归档缩短连接占用时间。

### 21.4 缩短事务

事务中不执行远程调用、文件处理、用户交互和长时间计算，尽快提交或回滚。

### 21.5 增加限流和降级

应用流量超过数据库承载能力时，应快速拒绝或降级部分请求，而不是让所有请求同时争抢连接。

### 21.6 做好整体容量规划

连接池配置需要与以下因素匹配：

- MySQL 最大连接数。
- 应用实例数量。
- 数据库 CPU 核心数。
- SQL 平均执行时间。
- 峰值 QPS。
- 数据库磁盘能力。
- 事务和锁竞争情况。
- 其他系统的连接需求。

## 22. max_connections 调整原则

提高 `max_connections` 前应确认：

1. 当前高连接是否属于正常业务需求。
2. 是否存在连接泄漏。
3. 是否存在慢 SQL 和锁等待。
4. 数据库 CPU 和 IO 是否仍有余量。
5. 内存是否可以支撑更多连接。
6. 应用连接池总上限是多少。
7. 是否为运维连接预留空间。
8. 增加连接后数据库吞吐量是否真的提高。

过低的 `max_connections` 会导致正常请求无法连接；过高则可能让大量请求同时进入数据库，造成数据库雪崩。

## 23. 不建议直接执行的操作

### 23.1 不建议只提高 max_connections

如果连接数高是慢 SQL、锁等待或连接泄漏造成的，提高上限只能延迟问题发生，并可能让数据库负载进一步恶化。

### 23.2 不建议直接重启 MySQL

重启会中断所有连接和事务，并可能触发恢复过程。应先定位连接来源和阻塞原因。

### 23.3 不建议批量终止所有 Sleep 连接

大量 Sleep 连接可能属于正常连接池。终止后连接池可能立即重新建立连接，反而增加数据库握手压力。

### 23.4 不建议无限增大应用连接池

连接池越大不代表吞吐量越高。数据库并行能力有限，过多连接会增加资源竞争和上下文切换。

### 23.5 不建议只增加连接获取超时

延长超时时间只会让应用线程等待更久，可能进一步导致线程池耗尽。

## 24. 监控与预防建议

建议持续监控以下指标：

- `Threads_connected`。
- `Threads_running`。
- `Max_used_connections`。
- `Connections`。
- `Aborted_connects`。
- 连接使用率。
- 各用户连接数。
- 各客户端主机连接数。
- Sleep 连接数量。
- 长时间 Sleep 连接数量。
- 长事务数量。
- 锁等待数量。
- 慢 SQL 数量。
- MySQL CPU 和内存。
- 数据库连接池活跃连接数。
- 数据库连接池空闲连接数。
- 等待连接线程数。
- 连接获取超时次数。
- 连接泄漏告警。

推荐告警条件包括：

- 连接使用率持续超过 80%。
- `Threads_running` 突然升高。
- 等待连接线程数持续增加。
- 连接获取超时开始出现。
- 某个客户端连接数异常增长。
- 长时间 Sleep 连接数量过多。
- 长事务和锁等待同步增加。
- `Aborted_connects` 快速增长。
- 连接数与应用实例数增长不匹配。

## 25. 推荐排查流程

MySQL 连接数过高可以按照以下顺序排查：

1. 查看 `max_connections`。
2. 查看 `Threads_connected` 和连接使用率。
3. 查看 `Threads_running`，区分活跃与空闲连接。
4. 使用 `SHOW FULL PROCESSLIST` 查看连接详情。
5. 按用户、客户端主机和状态统计连接。
6. 检查是否存在大量 Sleep 连接。
7. 检查慢 SQL、锁等待和长事务。
8. 检查应用连接池的活跃、空闲和等待连接。
9. 检查是否存在连接泄漏。
10. 计算所有应用实例的连接池总上限。
11. 检查最近是否进行应用扩容。
12. 检查 MySQL CPU、内存和磁盘 IO。
13. 采取限流、暂停任务或处理阻塞事务等临时措施。
14. 必要时安全终止异常连接。
15. 评估是否临时调整 `max_connections`。
16. 优化连接池、SQL、事务和容量规划。
17. 通过压力测试验证最大连接需求。
18. 建立连接数和连接池监控。

## 26. 常用排查命令汇总

```sql
-- 查看最大连接数
SHOW VARIABLES LIKE 'max_connections';

-- 查看当前连接数
SHOW GLOBAL STATUS LIKE 'Threads_connected';

-- 查看活跃线程数
SHOW GLOBAL STATUS LIKE 'Threads_running';

-- 查看历史最大连接数
SHOW GLOBAL STATUS LIKE 'Max_used_connections';

-- 查看累计连接次数
SHOW GLOBAL STATUS LIKE 'Connections';

-- 查看连接失败次数
SHOW GLOBAL STATUS LIKE 'Aborted_connects';

-- 查看当前所有连接
SHOW FULL PROCESSLIST;

-- 按命令状态统计
SELECT
    COMMAND,
    COUNT(*) AS connection_count
FROM information_schema.PROCESSLIST
GROUP BY COMMAND
ORDER BY connection_count DESC;

-- 按数据库用户统计
SELECT
    USER,
    COUNT(*) AS connection_count
FROM information_schema.PROCESSLIST
GROUP BY USER
ORDER BY connection_count DESC;

-- 按客户端主机统计
SELECT
    SUBSTRING_INDEX(HOST, ':', 1) AS client_host,
    COUNT(*) AS connection_count
FROM information_schema.PROCESSLIST
GROUP BY client_host
ORDER BY connection_count DESC;

-- 查看长时间 Sleep 连接
SELECT
    ID,
    USER,
    HOST,
    DB,
    TIME
FROM information_schema.PROCESSLIST
WHERE COMMAND = 'Sleep'
ORDER BY TIME DESC;

-- 查看非 Sleep 连接
SELECT
    ID,
    USER,
    HOST,
    DB,
    TIME,
    STATE,
    INFO
FROM information_schema.PROCESSLIST
WHERE COMMAND <> 'Sleep'
ORDER BY TIME DESC;

-- 查看当前事务
SELECT *
FROM information_schema.INNODB_TRX
ORDER BY trx_started;

-- 查看锁等待
SELECT *
FROM performance_schema.data_lock_waits;

-- 查看简化锁等待
SELECT *
FROM sys.innodb_lock_waits;

-- 查看空闲连接超时
SHOW VARIABLES LIKE 'wait_timeout';
SHOW VARIABLES LIKE 'interactive_timeout';

-- 查看线程缓存
SHOW VARIABLES LIKE 'thread_cache_size';
SHOW GLOBAL STATUS LIKE 'Threads_created';

-- 临时调整最大连接数
SET GLOBAL max_connections = 1000;

-- 终止查询
KILL QUERY <连接ID>;

-- 终止连接
KILL CONNECTION <连接ID>;
```

```bash
# 查看 MySQL CPU 和内存
top

# 查看系统内存
free -h

# 查看磁盘 IO
iostat -x 1 10

# 查看 MySQL 网络连接
ss -antp | grep mysqld

# 查看 MySQL 进程线程数
ps -o nlwp= -p <MySQL进程PID>
```

## 27. 排查结论模板

### 故障现象

应用接口大量超时，多个服务实例出现数据库连接获取失败，MySQL 日志和应用日志中出现 `Too many connections`。

### 故障确认

检查发现 MySQL 的 `max_connections` 为 500，当前 `Threads_connected` 已达到 498。按客户端主机统计后，发现报表服务的多个实例占用了大部分连接，其中多数连接处于长时间 Query 状态。

### 根本原因

报表服务新增批量导出任务后，一次并发提交大量查询。相关 SQL 缺少合适索引，单次执行时间超过一分钟，连接长期无法归还连接池。同时，报表服务每个实例的连接池最大值配置为 100，多个实例共同占满了数据库连接。

### 临时处理

暂停报表导出任务，对报表接口进行限流，并终止确认可以安全终止的异常查询。释放连接后，核心业务逐步恢复。

### 永久修复

为报表查询增加合适索引并改为分页查询；将报表任务调整为异步串行或低并发执行；降低报表服务单实例连接池上限，并为核心在线服务预留数据库连接。

### 验证结果

修复后进行相同规模的导出测试，报表 SQL 执行时间明显下降，数据库连接数保持在安全范围内，未再次出现连接获取超时或 `Too many connections`。

## 28. 总结

MySQL 连接数过高不一定是 `max_connections` 设置过小，更常见的原因是慢 SQL、锁等待、长事务、连接泄漏和连接池配置不合理。

排查时应先查看 `Threads_connected`、`Threads_running` 和 `Max_used_connections`，再通过 `SHOW FULL PROCESSLIST` 按用户、客户端主机和状态统计连接。如果大量连接为 Sleep，应检查连接池空闲策略；如果大量连接正在执行 SQL，应继续排查慢 SQL、锁等待和数据库资源。

数据库连接池必须按照所有应用实例的总量进行容量规划。单实例看似合理的连接池配置，在应用扩容后可能远远超过数据库承载能力。提高 `max_connections` 只能作为经过资源评估后的临时或容量调整措施，不能代替连接泄漏和慢 SQL 修复。

永久解决方案应包括优化 SQL、缩短事务、修复连接泄漏、合理配置连接池，以及建立数据库连接数、连接池等待线程和连接获取超时监控。