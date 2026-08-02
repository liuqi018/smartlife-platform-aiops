# MySQL 慢 SQL 排查

## 1. 问题概述

慢 SQL 是指执行时间超过业务预期，或者消耗大量 CPU、磁盘 IO、内存、锁资源的 SQL 语句。

SQL 是否属于慢 SQL，不能只根据固定的执行时间判断。对于普通接口查询，执行几百毫秒可能已经影响用户体验；对于离线统计或批处理任务，执行数秒可能仍然可以接受。因此，需要结合接口响应要求、访问频率、扫描行数和数据库资源消耗综合判断。

慢 SQL 不仅会影响当前请求，还可能引发一系列连锁问题：

1. 数据库连接长期不释放。
2. 数据库连接池逐渐耗尽。
3. 应用线程池出现任务堆积。
4. CPU 和磁盘 IO 使用率升高。
5. 锁等待和事务阻塞增加。
6. 主从复制延迟。
7. 接口大面积超时。
8. 数据库整体吞吐量下降。

慢 SQL 排查的核心目标包括：

1. 定位执行缓慢的 SQL。
2. 确认 SQL 的执行频率和影响范围。
3. 分析 SQL 执行计划。
4. 判断是否正确使用索引。
5. 检查扫描行数、排序、临时表和回表情况。
6. 区分 SQL 本身慢、锁等待和数据库资源不足。
7. 通过索引、SQL、表结构或架构优化解决问题。

## 2. 常见问题现象

MySQL 存在慢 SQL 时，通常会出现以下现象：

1. 某些接口响应时间明显增加。
2. 应用日志中出现数据库查询超时。
3. 数据库连接池活跃连接数持续升高。
4. 获取数据库连接时发生超时。
5. MySQL CPU 使用率持续较高。
6. MySQL 磁盘 IO 明显升高。
7. `SHOW PROCESSLIST` 中存在长时间执行的 SQL。
8. 大量会话处于 `Sending data` 状态。
9. 大量事务处于锁等待状态。
10. 慢查询日志中出现大量记录。
11. 主从复制延迟升高。
12. 临时表数量快速增加。
13. 排序和分组操作占用大量资源。
14. 数据库 QPS 没有明显增加，但响应时间升高。
15. 业务高峰期数据库连接数达到上限。

常见应用错误包括：

```text
Query timeout
```

```text
Lock wait timeout exceeded
```

```text
Communications link failure
```

```text
Connection is not available, request timed out
```

应用侧的超时不一定表示 SQL 执行逻辑本身缓慢，也可能是连接池等待、数据库锁等待、网络异常或数据库负载过高造成的。

## 3. 慢 SQL 的常见原因

### 3.1 缺少合适的索引

查询条件、关联条件或排序字段没有合适索引，可能导致全表扫描。

例如：

```sql
SELECT *
FROM orders
WHERE user_id = 10001;
```

如果 `user_id` 没有索引，MySQL 可能扫描整张订单表。

### 3.2 索引存在但没有被使用

常见原因包括：

- 对索引列使用函数。
- 对索引列进行计算。
- 发生隐式类型转换。
- 使用不符合最左前缀原则的联合索引。
- `LIKE` 以通配符开头。
- 查询条件选择性过低。
- `OR` 条件部分字段没有索引。
- MySQL 优化器估算全表扫描成本更低。
- 表统计信息不准确。

### 3.3 返回数据量过大

即使 SQL 使用了索引，如果一次返回几十万行数据，数据库扫描、网络传输、对象创建和应用处理仍然会消耗大量资源。

常见问题包括：

- 没有分页。
- `SELECT *` 返回大量无用字段。
- 导出任务一次查询全部数据。
- 查询大字段。
- 接口没有限制最大结果数。

### 3.4 联合索引设计不合理

联合索引字段顺序与实际查询条件不匹配，可能导致索引只能部分生效，甚至完全无法使用。

### 3.5 排序和分组开销过大

`ORDER BY`、`GROUP BY`、`DISTINCT` 和聚合操作可能产生额外排序、临时表和磁盘 IO。

执行计划中可能出现：

```text
Using filesort
```

```text
Using temporary
```

### 3.6 深分页

以下查询在页码较大时可能扫描和丢弃大量数据：

```sql
SELECT *
FROM orders
ORDER BY id
LIMIT 1000000, 20;
```

偏移量越大，查询成本通常越高。

### 3.7 多表关联不合理

关联字段没有索引、表连接顺序不合理或中间结果集过大，可能导致 SQL 执行时间明显增加。

### 3.8 锁等待

SQL 本身可能很简单，但由于其他事务持有行锁、间隙锁、元数据锁或表锁，导致 SQL 长时间等待。

### 3.9 数据库资源不足

MySQL CPU、内存、磁盘 IO 或连接数达到瓶颈时，即使原本正常的 SQL 也可能变慢。

### 3.10 数据量快速增长

SQL 在数据量较小时执行正常，但随着表数据增长，扫描行数和排序成本持续增加，最终成为慢 SQL。

## 4. 排查前的注意事项

生产环境排查慢 SQL 时，应注意：

1. 不要直接执行可能返回大量数据的 SQL。
2. 不要在高峰期对大表随意执行 `SELECT COUNT(*)`。
3. 不要直接对生产大表执行高风险 DDL。
4. 不要只根据单次执行时间判断。
5. 不要看到全表扫描就立即强制使用索引。
6. 使用 `EXPLAIN ANALYZE` 前应评估 SQL 的执行成本。
7. 终止会话前应确认事务和业务影响。
8. 优化索引前应检查已有索引，避免重复索引。
9. 应记录 SQL、参数、执行时间和发生时间。
10. 应结合应用、数据库和操作系统监控分析。

建议先保存：

```sql
SHOW FULL PROCESSLIST;
SHOW GLOBAL STATUS LIKE 'Threads_connected';
SHOW GLOBAL STATUS LIKE 'Threads_running';
SHOW GLOBAL STATUS LIKE 'Slow_queries';
SHOW ENGINE INNODB STATUS;
```

同时查看：

```bash
top
iostat -x 1 10
free -h
```

## 5. 第一步：确认慢 SQL 的影响范围

排查前应明确：

- 哪个接口或任务出现问题。
- 问题是持续发生还是偶发发生。
- 所有请求都慢，还是特定参数慢。
- SQL 执行时间是多少。
- SQL 每秒执行多少次。
- 数据库 CPU 和 IO 是否同时升高。
- 是否存在锁等待。
- 是否与业务高峰或定时任务相关。
- 是否在发布、数据导入或表结构变更后发生。

一条执行 3 秒、每天只运行一次的统计 SQL，可能影响有限；一条执行 200 毫秒、每秒执行上千次的 SQL，可能对数据库造成更大压力。

因此，应同时关注：

```text
单次执行时间 × 执行频率
```

## 6. 第二步：查看当前正在执行的 SQL

执行：

```sql
SHOW FULL PROCESSLIST;
```

重点关注以下字段：

- `Id`：连接 ID。
- `User`：数据库用户。
- `Host`：客户端地址。
- `db`：当前数据库。
- `Command`：当前命令。
- `Time`：当前状态持续时间。
- `State`：执行状态。
- `Info`：正在执行的 SQL。

也可以查询：

```sql
SELECT *
FROM information_schema.PROCESSLIST
ORDER BY TIME DESC;
```

重点关注：

- 执行时间较长的 SQL。
- 大量相同 SQL。
- `Locked` 或等待锁的会话。
- 长时间处于 `Sending data` 的会话。
- 长时间处于 `Creating sort index` 的会话。
- 长时间处于 `Copying to tmp table` 的会话。
- 大量 `Sleep` 连接。
- 来源异常的客户端。

`Sending data` 不只是向客户端发送数据，也可能包括读取、处理和返回数据的整个阶段，不能简单认为只是网络传输慢。

## 7. 第三步：查看慢查询日志配置

查看慢查询是否启用：

```sql
SHOW VARIABLES LIKE 'slow_query_log';
```

查看日志路径：

```sql
SHOW VARIABLES LIKE 'slow_query_log_file';
```

查看慢查询阈值：

```sql
SHOW VARIABLES LIKE 'long_query_time';
```

查看是否记录未使用索引的查询：

```sql
SHOW VARIABLES LIKE 'log_queries_not_using_indexes';
```

查看管理语句记录配置：

```sql
SHOW VARIABLES LIKE 'log_slow_admin_statements';
```

临时启用慢查询日志：

```sql
SET GLOBAL slow_query_log = ON;
```

设置慢查询阈值：

```sql
SET GLOBAL long_query_time = 1;
```

生产环境启用 `log_queries_not_using_indexes` 前应谨慎评估，因为可能产生大量日志。

长期配置应写入 MySQL 配置文件，避免数据库重启后失效。

## 8. 第四步：分析慢查询日志

查看慢查询日志：

```bash
tail -n 200 /path/to/mysql-slow.log
```

使用 `mysqldumpslow` 汇总：

```bash
mysqldumpslow -s t -t 20 /path/to/mysql-slow.log
```

常见排序方式包括：

- `-s t`：按照总查询时间排序。
- `-s at`：按照平均查询时间排序。
- `-s c`：按照执行次数排序。
- `-s r`：按照返回行数排序。
- `-t 20`：显示前 20 条。

如果安装了 Percona Toolkit，可以使用：

```bash
pt-query-digest /path/to/mysql-slow.log
```

重点关注：

- 总耗时最高的 SQL。
- 平均耗时最高的 SQL。
- 执行次数最多的 SQL。
- 扫描行数最多的 SQL。
- 返回行数较少但扫描行数很大的 SQL。
- 锁等待时间较长的 SQL。
- 在业务高峰集中出现的 SQL。

## 9. 第五步：使用 Performance Schema 定位 SQL

如果启用了 Performance Schema，可以查看 SQL 摘要：

```sql
SELECT
    DIGEST_TEXT,
    COUNT_STAR,
    ROUND(SUM_TIMER_WAIT / 1000000000000, 2) AS total_seconds,
    ROUND(AVG_TIMER_WAIT / 1000000000, 2) AS avg_ms,
    SUM_ROWS_EXAMINED,
    SUM_ROWS_SENT
FROM performance_schema.events_statements_summary_by_digest
WHERE DIGEST_TEXT IS NOT NULL
ORDER BY SUM_TIMER_WAIT DESC
LIMIT 20;
```

该查询可以帮助定位：

- 累计耗时最高的 SQL。
- 平均执行时间较高的 SQL。
- 执行次数较多的 SQL。
- 扫描行数较大的 SQL。

还可以按照平均耗时排序：

```sql
SELECT
    DIGEST_TEXT,
    COUNT_STAR,
    ROUND(AVG_TIMER_WAIT / 1000000000, 2) AS avg_ms,
    SUM_ROWS_EXAMINED,
    SUM_ROWS_SENT
FROM performance_schema.events_statements_summary_by_digest
WHERE DIGEST_TEXT IS NOT NULL
ORDER BY AVG_TIMER_WAIT DESC
LIMIT 20;
```

查询 Performance Schema 时，应结合当前 MySQL 版本和采集配置确认字段含义与时间单位。

## 10. 第六步：使用 EXPLAIN 分析执行计划

对目标 SQL 执行：

```sql
EXPLAIN
SELECT ...
```

MySQL 8.0 可以使用：

```sql
EXPLAIN FORMAT=TREE
SELECT ...
```

`EXPLAIN` 主要关注以下字段：

- `id`。
- `select_type`。
- `table`。
- `type`。
- `possible_keys`。
- `key`。
- `key_len`。
- `ref`。
- `rows`。
- `filtered`。
- `Extra`。

## 11. type 字段说明

常见访问类型从较优到较差大致包括：

```text
system
const
eq_ref
ref
range
index
ALL
```

### 11.1 const

通过主键或唯一索引定位单条记录，通常效率较高。

### 11.2 eq_ref

多表关联时，被驱动表通过主键或唯一索引最多匹配一行。

### 11.3 ref

通过普通索引进行等值查询，可能匹配多行。

### 11.4 range

使用索引范围查询，例如：

```sql
WHERE id > 100
```

### 11.5 index

扫描整个索引。虽然不一定回表扫描全部数据，但仍可能读取大量索引记录。

### 11.6 ALL

全表扫描。对于大表需要重点关注，但小表全表扫描不一定比走索引更差。

不能只根据 `type=ALL` 判断 SQL 必须优化，还需要结合表大小、扫描行数、执行频率和过滤比例分析。

## 12. possible_keys、key 与 rows

### 12.1 possible_keys

表示优化器认为可能使用的索引。

### 12.2 key

表示实际选择使用的索引。

如果 `possible_keys` 有值，但 `key` 为 `NULL`，说明优化器最终没有选择索引。

可能原因包括：

- 数据量较小。
- 条件选择性较差。
- 返回数据比例过高。
- 统计信息不准确。
- 使用索引成本高于全表扫描。
- 查询写法使索引失效。

### 12.3 rows

表示优化器估算需要扫描的行数，并不一定等于实际扫描行数。

`rows` 很大通常需要重点关注，尤其是最终只返回少量记录时。

## 13. Extra 字段说明

### 13.1 Using index

表示使用覆盖索引，可以直接从索引获得所需数据，不需要回表。

### 13.2 Using index condition

表示使用了索引条件下推，可以减少回表数据量。

### 13.3 Using where

表示存储引擎返回记录后，还需要通过 `WHERE` 条件进一步过滤。

### 13.4 Using temporary

表示查询使用了临时表，常见于复杂的 `GROUP BY`、`DISTINCT` 或排序操作。

### 13.5 Using filesort

表示排序不能直接利用索引顺序，需要执行额外排序。

`Using filesort` 并不一定表示使用磁盘文件，也可能在内存中排序，但仍需要关注排序数据量和执行频率。

### 13.6 Using join buffer

表示连接过程中使用了 Join Buffer，可能说明关联字段缺少合适索引。

## 14. 使用 EXPLAIN ANALYZE

MySQL 8.0 支持：

```sql
EXPLAIN ANALYZE
SELECT ...
```

它会实际执行 SQL，并返回：

- 实际执行时间。
- 实际返回行数。
- 循环次数。
- 各执行节点的成本。

`EXPLAIN ANALYZE` 比普通 `EXPLAIN` 更接近真实情况，但它会实际执行 SQL。

对于更新、删除、超大查询或生产环境高风险 SQL，不应在没有评估影响的情况下直接执行。

## 15. 索引失效的常见情况

### 15.1 对索引列使用函数

```sql
SELECT *
FROM users
WHERE DATE(create_time) = '2026-07-21';
```

可以改为范围查询：

```sql
SELECT *
FROM users
WHERE create_time >= '2026-07-21 00:00:00'
  AND create_time < '2026-07-22 00:00:00';
```

### 15.2 对索引列进行计算

```sql
WHERE amount + 10 > 100
```

应尽量改为：

```sql
WHERE amount > 90
```

### 15.3 隐式类型转换

如果字段是字符串，却使用数字比较：

```sql
WHERE phone = 13800138000
```

应使用：

```sql
WHERE phone = '13800138000'
```

字段类型和参数类型不一致可能导致索引无法有效使用。

### 15.4 LIKE 以通配符开头

```sql
WHERE name LIKE '%keyword'
```

普通 B+Tree 索引通常无法利用左侧前缀定位。

以下写法通常可以使用前缀索引范围：

```sql
WHERE name LIKE 'keyword%'
```

### 15.5 不符合最左前缀原则

假设联合索引为：

```sql
INDEX idx_a_b_c(a, b, c)
```

以下条件通常可以有效使用：

```sql
WHERE a = ? AND b = ?
```

而只查询：

```sql
WHERE b = ?
```

通常无法利用该联合索引的最左列进行快速定位。

### 15.6 OR 条件

```sql
WHERE user_id = ? OR status = ?
```

如果部分条件缺少索引，优化器可能选择全表扫描。可以根据业务和执行计划评估拆分查询、补充索引或使用 `UNION ALL`。

## 16. 联合索引设计

联合索引设计需要结合实际查询模式，而不是简单把所有条件字段放进一个索引。

需要考虑：

- 等值查询字段。
- 范围查询字段。
- 排序字段。
- 分组字段。
- 字段选择性。
- 查询频率。
- 是否需要覆盖索引。
- 索引维护成本。

例如，常见查询为：

```sql
SELECT id, amount
FROM orders
WHERE user_id = ?
  AND status = ?
ORDER BY create_time DESC
LIMIT 20;
```

可以根据数据分布和执行计划评估：

```sql
INDEX idx_user_status_time(user_id, status, create_time)
```

索引并不是越多越好。每增加一个索引都会增加：

- 插入成本。
- 更新成本。
- 删除成本。
- 磁盘空间占用。
- Buffer Pool 压力。
- 优化器选择复杂度。

## 17. 覆盖索引与回表

InnoDB 普通索引的叶子节点通常保存主键值。如果查询需要的字段不在普通索引中，MySQL 需要根据主键回到聚簇索引获取完整记录，这个过程称为回表。

例如：

```sql
SELECT id, user_id
FROM orders
WHERE user_id = ?;
```

如果索引包含查询需要的全部字段，就可能形成覆盖索引，减少回表次数。

不应为了覆盖所有查询而创建字段过多的超宽索引，需要平衡查询收益、索引大小和写入成本。

## 18. 深分页优化

低效深分页：

```sql
SELECT *
FROM orders
ORDER BY id
LIMIT 1000000, 20;
```

可以根据连续主键或排序字段改为游标方式：

```sql
SELECT *
FROM orders
WHERE id > ?
ORDER BY id
LIMIT 20;
```

如果必须按照复杂条件分页，可以先通过覆盖索引获取主键，再回表查询：

```sql
SELECT o.*
FROM orders o
JOIN (
    SELECT id
    FROM orders
    ORDER BY create_time DESC
    LIMIT 1000000, 20
) t ON o.id = t.id;
```

是否更快需要通过实际执行计划和数据规模验证。

## 19. ORDER BY 与 GROUP BY 优化

排序和分组优化方向包括：

- 使用与过滤条件、排序字段匹配的联合索引。
- 减少参与排序的数据量。
- 避免返回不需要的列。
- 在排序前先过滤。
- 限制结果数量。
- 避免对超大结果集执行实时统计。
- 将复杂统计迁移到离线任务。
- 对频繁统计结果进行缓存或预计算。

如果执行计划出现：

```text
Using temporary
Using filesort
```

应检查排序字段、分组字段、索引顺序和扫描行数，但不要求机械地消除所有这些提示。

## 20. 多表关联优化

多表关联需要重点检查：

1. 关联字段类型是否一致。
2. 被驱动表关联字段是否有索引。
3. 字符集和排序规则是否一致。
4. 是否提前过滤数据。
5. 是否返回过多字段。
6. 中间结果集是否过大。
7. 是否存在不必要的关联。
8. 是否出现一对多数据膨胀。

错误示例：

```sql
SELECT *
FROM orders o
JOIN users u ON o.user_id = u.id
WHERE o.status = 1;
```

如果 `orders.status` 和 `orders.user_id` 缺少合适索引，可能扫描大量订单后再关联用户表。

应根据执行计划和数据分布设计索引，而不是只根据 SQL 表面顺序判断驱动表。

## 21. 检查锁等待

查看 InnoDB 状态：

```sql
SHOW ENGINE INNODB STATUS;
```

MySQL 8.0 可以查看数据锁：

```sql
SELECT *
FROM performance_schema.data_locks;
```

查看锁等待：

```sql
SELECT *
FROM performance_schema.data_lock_waits;
```

查看当前事务：

```sql
SELECT *
FROM information_schema.INNODB_TRX;
```

如果 SQL 执行时间长，但实际处于锁等待，优化索引不一定能够直接解决问题。

应进一步检查：

- 阻塞事务。
- 长事务。
- 未提交事务。
- 大批量更新。
- 不一致的更新顺序。
- 元数据锁。
- 事务隔离级别。
- SQL 是否锁定了过多记录。

## 22. 检查数据库资源

慢 SQL 也可能是数据库整体资源不足造成的。

### 22.1 CPU

```bash
top
```

如果 MySQL CPU 长期较高，应检查高频 SQL、复杂计算、排序和并发量。

### 22.2 磁盘 IO

```bash
iostat -x 1 10
```

重点关注：

- `%util`。
- `await`。
- 读写吞吐量。
- IO 队列长度。

### 22.3 内存

```bash
free -h
```

如果系统频繁使用 Swap，数据库查询性能可能明显下降。

### 22.4 Buffer Pool

查看配置：

```sql
SHOW VARIABLES LIKE 'innodb_buffer_pool_size';
```

查看读请求：

```sql
SHOW GLOBAL STATUS LIKE 'Innodb_buffer_pool_read%';
```

Buffer Pool 过小会导致数据页频繁从磁盘读取，但不能脱离服务器总内存盲目调大。

## 23. 检查临时表

查看临时表统计：

```sql
SHOW GLOBAL STATUS LIKE 'Created_tmp%';
```

常见指标包括：

- `Created_tmp_tables`。
- `Created_tmp_disk_tables`。
- `Created_tmp_files`。

如果磁盘临时表比例较高，应检查：

- `GROUP BY`。
- `ORDER BY`。
- `DISTINCT`。
- 大字段。
- 查询中间结果集。
- 临时表内存限制。
- SQL 是否可以通过索引优化。

不能只通过增大临时表内存限制解决问题。并发较高时，每个会话都可能使用内存临时表，参数过大可能造成整体内存压力。

## 24. 检查表统计信息

优化器依赖统计信息选择执行计划。如果统计信息过旧或数据分布变化较大，可能选择错误的索引。

可以评估执行：

```sql
ANALYZE TABLE <表名>;
```

该操作可能消耗一定资源，并对表产生影响，应在评估后执行。

MySQL 8.0 还支持直方图统计，用于改善非索引列或数据分布不均场景下的成本估算。是否使用应根据具体查询验证。

## 25. 慢 SQL 的临时处理措施

当慢 SQL 已经严重影响业务时，可以采取：

1. 对相关接口临时限流。
2. 暂停非核心统计和报表任务。
3. 暂停大批量导入、更新和删除。
4. 终止确认可以安全终止的异常查询。
5. 处理长事务和锁阻塞。
6. 将部分流量切换到只读实例。
7. 增加应用缓存。
8. 降低任务并发度。
9. 在从库执行非实时查询。
10. 对高成本功能进行降级。

终止查询可以执行：

```sql
KILL QUERY <连接ID>;
```

终止连接：

```sql
KILL CONNECTION <连接ID>;
```

执行前必须确认连接、事务和业务影响。终止大事务后，回滚过程可能持续较长时间并继续消耗资源。

## 26. 永久优化措施

### 26.1 增加合适索引

根据查询条件、关联字段、排序和分组设计联合索引，并避免重复和无效索引。

### 26.2 优化 SQL

- 避免 `SELECT *`。
- 只查询必要字段。
- 使用分页和结果数量限制。
- 避免对索引列使用函数。
- 避免隐式类型转换。
- 优化 `OR` 和子查询。
- 减少不必要的多表关联。
- 将复杂实时统计改为预计算。

### 26.3 优化数据模型

- 选择合理字段类型。
- 保持关联字段类型一致。
- 对历史数据进行归档。
- 对超大表进行分区评估。
- 将大字段与高频查询字段拆分。
- 避免单表数据无限增长。

### 26.4 优化架构

- 使用 Redis 等缓存热点数据。
- 进行读写分离。
- 使用只读实例处理统计查询。
- 将大报表改为异步任务。
- 对历史数据使用离线分析系统。
- 对热点接口进行限流和降级。
- 根据业务规模进行分库分表评估。

## 27. 不建议直接执行的操作

### 27.1 不建议盲目创建索引

索引会增加写入成本和磁盘占用。创建前应检查现有索引和真实查询模式。

查看表索引：

```sql
SHOW INDEX FROM <表名>;
```

### 27.2 不建议强制索引作为首选方案

```sql
FORCE INDEX
```

可能暂时改善当前数据分布下的查询，但随着数据变化可能变差。应先理解优化器没有选择索引的原因。

### 27.3 不建议随意增大数据库参数

增大 Buffer Pool、排序区和临时表内存可能改善部分查询，也可能导致整体内存不足。参数调整应结合并发量和服务器容量。

### 27.4 不建议只在应用侧增加超时时间

增加超时时间只会让连接和线程等待更久，无法解决 SQL 执行缓慢的根本原因。

### 27.5 不建议直接在生产大表执行高风险 DDL

创建索引可能消耗大量 CPU、IO 和磁盘空间，也可能影响在线请求。应评估 MySQL 版本、DDL 算法、表大小和业务窗口。

## 28. 监控与预防建议

建议持续监控以下指标：

- 慢查询数量。
- SQL 平均响应时间。
- SQL P95、P99 延迟。
- SQL 执行次数。
- 扫描行数。
- 返回行数。
- 数据库 QPS 和 TPS。
- 当前连接数。
- 活跃连接数。
- `Threads_running`。
- 数据库 CPU。
- 磁盘 IO。
- Buffer Pool 命中情况。
- 临时表数量。
- 锁等待时间。
- 长事务数量。
- 主从复制延迟。
- 数据库连接池使用率。

建议建立以下机制：

1. 开启并轮转慢查询日志。
2. 定期分析 Top SQL。
3. 上线前检查核心 SQL 执行计划。
4. 对大表查询设置结果限制。
5. 对高风险 SQL 进行代码审核。
6. 监控表数据增长速度。
7. 定期清理无效和重复索引。
8. 对数据库变更进行灰度验证。
9. 建立 SQL 性能基线。
10. 对突发慢 SQL 自动告警。

## 29. 推荐排查流程

MySQL 慢 SQL 可以按照以下顺序排查：

1. 明确慢接口、SQL、参数和发生时间。
2. 查看数据库 CPU、内存、磁盘 IO 和连接数。
3. 使用 `SHOW FULL PROCESSLIST` 查看当前长时间 SQL。
4. 检查是否存在锁等待和长事务。
5. 查看慢查询日志。
6. 使用 `mysqldumpslow` 或 `pt-query-digest` 汇总 Top SQL。
7. 使用 Performance Schema 查看累计耗时和执行次数。
8. 使用 `EXPLAIN` 分析执行计划。
9. 检查访问类型、索引、扫描行数和 Extra。
10. 检查索引是否失效。
11. 检查联合索引、排序、分组和多表关联。
12. 检查是否存在深分页和大结果集。
13. 使用 `EXPLAIN ANALYZE` 验证实际执行情况。
14. 采取限流、终止异常查询或暂停任务等临时措施。
15. 优化索引、SQL、表结构或应用架构。
16. 在接近生产的数据规模下进行测试。
17. 上线后持续观察执行时间和数据库资源。

## 30. 常用排查命令汇总

```sql
-- 查看当前会话
SHOW FULL PROCESSLIST;

-- 查看 InnoDB 状态
SHOW ENGINE INNODB STATUS;

-- 查看当前事务
SELECT *
FROM information_schema.INNODB_TRX;

-- 查看数据锁
SELECT *
FROM performance_schema.data_locks;

-- 查看锁等待
SELECT *
FROM performance_schema.data_lock_waits;

-- 查看慢查询是否开启
SHOW VARIABLES LIKE 'slow_query_log';

-- 查看慢查询日志路径
SHOW VARIABLES LIKE 'slow_query_log_file';

-- 查看慢查询阈值
SHOW VARIABLES LIKE 'long_query_time';

-- 查看慢查询累计数量
SHOW GLOBAL STATUS LIKE 'Slow_queries';

-- 查看数据库连接
SHOW GLOBAL STATUS LIKE 'Threads_connected';

-- 查看活跃线程
SHOW GLOBAL STATUS LIKE 'Threads_running';

-- 查看临时表
SHOW GLOBAL STATUS LIKE 'Created_tmp%';

-- 查看 Buffer Pool 配置
SHOW VARIABLES LIKE 'innodb_buffer_pool_size';

-- 查看 Buffer Pool 读取指标
SHOW GLOBAL STATUS LIKE 'Innodb_buffer_pool_read%';

-- 查看表索引
SHOW INDEX FROM <表名>;

-- 查看执行计划
EXPLAIN SELECT ...;

-- 查看树形执行计划
EXPLAIN FORMAT=TREE SELECT ...;

-- 查看实际执行计划
EXPLAIN ANALYZE SELECT ...;

-- 更新表统计信息
ANALYZE TABLE <表名>;

-- 终止指定查询
KILL QUERY <连接ID>;
```

```bash
# 查看 MySQL 进程资源
top

# 查看磁盘 IO
iostat -x 1 10

# 查看系统内存
free -h

# 查看慢查询日志
tail -n 200 /path/to/mysql-slow.log

# 按总执行时间汇总慢 SQL
mysqldumpslow -s t -t 20 /path/to/mysql-slow.log

# 使用 pt-query-digest 分析
pt-query-digest /path/to/mysql-slow.log
```

## 31. 排查结论模板

### 故障现象

订单列表接口响应时间从 100 毫秒上升到数秒，应用数据库连接池使用率持续升高，MySQL CPU 和磁盘读取量明显增加。

### 故障确认

通过慢查询日志和 Performance Schema 定位到订单分页查询累计耗时最高。`EXPLAIN` 显示访问类型为 `ALL`，预计扫描数百万行，并出现 `Using filesort`。

### 根本原因

查询按照 `user_id`、`status` 过滤，并按照 `create_time` 倒序分页，但表中只有 `user_id` 单列索引。随着订单数据增长，MySQL 需要扫描该用户的大量订单后再进行额外排序，导致查询耗时持续增加。

### 临时处理

对订单列表接口进行限流，限制最大分页深度，并暂时关闭非核心批量查询任务，降低数据库压力。

### 永久修复

根据查询模式增加 `(user_id, status, create_time)` 联合索引，将深分页改为基于 `create_time` 和主键的游标分页，并将 `SELECT *` 改为只查询页面需要的字段。

### 验证结果

在接近生产数据量的测试环境执行优化后的 SQL，扫描行数明显下降，执行计划使用联合索引，不再出现大范围额外排序。上线后接口 P95 响应时间和数据库 CPU 恢复正常。

## 32. 总结

MySQL 慢 SQL 排查不能只看单次执行时间，还需要同时关注执行频率、扫描行数、锁等待和数据库资源消耗。

首先应通过 `SHOW FULL PROCESSLIST`、慢查询日志和 Performance Schema 定位影响最大的 SQL，再使用 `EXPLAIN` 分析访问类型、实际索引、预计扫描行数和额外操作。对于 MySQL 8.0，可以在评估风险后使用 `EXPLAIN ANALYZE` 查看真实执行过程。

慢 SQL 的常见原因包括缺少索引、索引失效、联合索引不合理、返回数据过多、深分页、排序分组、多表关联和锁等待。优化时应结合真实查询模式和数据分布，不能机械地要求所有查询都必须走索引，也不能通过无限增加索引解决问题。

临时处理可以通过限流、暂停批量任务、处理阻塞事务和终止异常查询恢复数据库稳定。永久解决方案则应从 SQL、索引、数据模型、缓存和系统架构等方面综合优化，并通过接近生产的数据规模验证实际效果。