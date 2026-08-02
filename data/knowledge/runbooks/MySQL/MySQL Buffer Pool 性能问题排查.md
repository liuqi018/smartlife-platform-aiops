# MySQL Buffer Pool 性能问题排查

## 1. 问题概述

InnoDB Buffer Pool 是 MySQL InnoDB 存储引擎最重要的内存区域，主要用于缓存数据页、索引页、Undo 页和部分内部数据结构。

当应用查询数据时，如果目标数据页已经存在于 Buffer Pool 中，MySQL 可以直接从内存读取；如果不存在，则需要从磁盘加载。内存访问速度远高于磁盘，因此 Buffer Pool 的容量和使用效率会直接影响 MySQL 查询性能。

Buffer Pool 性能问题通常表现为：

1. 缓存命中率下降。
2. 磁盘读取量明显增加。
3. 查询响应时间升高。
4. 脏页积压或集中刷盘。
5. Buffer Pool 空闲页不足。
6. 数据页频繁被淘汰和重新加载。
7. MySQL 重启后性能长时间没有恢复。
8. 大表扫描挤出热点数据。
9. 数据库内存或系统 Swap 压力升高。

Buffer Pool 排查不能只看一个命中率指标，还需要结合数据读取量、脏页比例、页淘汰、磁盘 IO、SQL 扫描范围和服务器内存综合分析。

## 2. Buffer Pool 的主要作用

Buffer Pool 主要缓存以下内容：

- 表数据页。
- 普通索引页。
- 聚簇索引页。
- Undo 页。
- Change Buffer 相关页面。
- 自适应哈希索引相关结构。
- InnoDB 内部管理数据。

InnoDB 读取数据时，大致流程如下：

```text
SQL 请求读取数据
    ↓
检查数据页是否在 Buffer Pool
    ├── 已存在：直接从内存读取
    └── 不存在：从磁盘读取到 Buffer Pool
                    ↓
                返回查询结果
```

数据页加载到 Buffer Pool 后，会进入 LRU 管理结构。当 Buffer Pool 空间不足时，InnoDB 会淘汰一部分不活跃的数据页，为新页面腾出空间。

数据更新通常先修改 Buffer Pool 中的数据页。被修改但尚未写回磁盘的数据页称为脏页，后台线程会根据检查点和刷盘策略将脏页写入磁盘。

## 3. 常见问题现象

Buffer Pool 出现性能问题时，通常会出现以下现象：

1. 原本正常的查询突然变慢。
2. MySQL 磁盘读取量持续增加。
3. `Innodb_buffer_pool_reads` 快速增长。
4. Buffer Pool 命中率下降。
5. 数据库重启后大量查询变慢。
6. 大表扫描后其他热点查询变慢。
7. 磁盘 `%util`、`await` 和队列长度升高。
8. 脏页比例持续处于高位。
9. 数据库写入出现周期性延迟。
10. MySQL Checkpoint 压力升高。
11. Buffer Pool 中空闲页数量接近 0。
12. Page Cleaner 无法及时完成刷脏。
13. MySQL CPU 不一定很高，但接口响应明显变慢。
14. 系统出现 Swap，MySQL 性能严重下降。
15. 数据库重启后需要较长时间完成缓存预热。

常见日志或状态信息可能包括：

```text
InnoDB: page_cleaner: 1000ms intended loop took ... ms
```

该信息通常表示 Page Cleaner 一次循环耗时超过预期，需要继续检查磁盘性能、脏页数量、刷盘能力和数据库负载。

## 4. Buffer Pool 性能问题的常见原因

### 4.1 Buffer Pool 配置过小

如果 Buffer Pool 远小于业务热点数据集，数据页会频繁从磁盘读取，并在空间不足时被淘汰。

常见特征包括：

- `Innodb_buffer_pool_reads` 持续快速增长。
- 磁盘随机读取较多。
- 热点查询也频繁触发物理读。
- Buffer Pool 中空闲页长期很少。
- 增大 Buffer Pool 后性能明显改善。

### 4.2 Buffer Pool 配置过大

Buffer Pool 并不是越大越好。如果配置接近服务器物理内存上限，可能导致：

- 操作系统可用内存不足。
- MySQL 其他内存区域没有足够空间。
- 连接级缓冲区叠加后发生内存压力。
- 系统开始使用 Swap。
- 容器达到内存限制。
- MySQL 或其他进程被 OOM Killer 终止。

### 4.3 大表全表扫描污染缓存

大范围查询会将大量冷数据加载到 Buffer Pool，挤出原本频繁访问的热点数据。

常见场景包括：

- 报表查询。
- 全表统计。
- 无索引查询。
- 数据导出。
- 数据校验。
- 备份或扫描任务。
- 大范围 `SELECT *`。

### 4.4 热点数据集超过内存容量

即使 SQL 和 Buffer Pool 配置没有明显错误，如果业务需要频繁访问的数据量已经超过可用内存，也会出现页频繁换入换出的情况。

### 4.5 脏页比例过高

大量写入会产生脏页。如果磁盘写入速度不足，或者刷盘速度跟不上脏页产生速度，脏页可能持续积压。

当脏页比例达到较高水平时，InnoDB 可能加快刷盘，从而导致磁盘 IO 和查询延迟突然升高。

### 4.6 磁盘性能不足

Buffer Pool 缓存未命中后需要从磁盘读取。如果磁盘响应时间过高，即使缓存未命中次数不算很多，也可能对查询性能产生明显影响。

### 4.7 SQL 和索引设计不合理

缺少索引、索引失效、返回数据过多和深分页会增加数据页扫描量，造成 Buffer Pool 压力。

### 4.8 MySQL 重启导致缓存变冷

MySQL 重启后，Buffer Pool 中原有的缓存页会丢失或需要重新恢复。业务请求初期需要从磁盘重新加载数据，可能出现明显的冷启动性能下降。

## 5. 排查前的注意事项

生产环境排查 Buffer Pool 问题时，应注意：

1. 不要看到命中率低就立即增加 Buffer Pool。
2. 不要将服务器全部内存分配给 Buffer Pool。
3. 不要只观察某一时刻的累计状态值。
4. 应通过连续采样计算指标增长速度。
5. 不要忽略 SQL、磁盘和操作系统内存问题。
6. 不要直接重启 MySQL 清理缓存。
7. 不要使用全表扫描测试缓存性能。
8. 修改内存参数前应评估 MySQL 总内存。
9. 容器环境中应以容器内存限制为容量依据。
10. 调整后应使用相同业务负载验证效果。

建议先保存：

```sql
SHOW VARIABLES LIKE 'innodb_buffer_pool%';
SHOW GLOBAL STATUS LIKE 'Innodb_buffer_pool%';
SHOW ENGINE INNODB STATUS;
```

同时检查：

```bash
free -h
vmstat 1 10
iostat -x 1 10
ps -eo pid,user,%mem,rss,vsz,cmd --sort=-rss | head -20
```

## 6. 第一步：查看 Buffer Pool 配置

查看 Buffer Pool 大小：

```sql
SHOW VARIABLES LIKE 'innodb_buffer_pool_size';
```

查看实例数量：

```sql
SHOW VARIABLES LIKE 'innodb_buffer_pool_instances';
```

查看相关配置：

```sql
SHOW VARIABLES LIKE 'innodb_buffer_pool%';
```

`innodb_buffer_pool_size` 是 Buffer Pool 的总容量，单位为字节。

可以通过以下 SQL 换算为 GB：

```sql
SELECT
    @@innodb_buffer_pool_size / 1024 / 1024 / 1024
    AS buffer_pool_size_gb;
```

Buffer Pool 配置需要综合考虑：

- 服务器物理内存。
- 是否为数据库专用服务器。
- MySQL 连接数。
- 排序、连接和临时表内存。
- Performance Schema 内存。
- 操作系统和文件系统缓存。
- 备份与监控进程。
- 容器内存限制。
- 业务热点数据规模。

## 7. 第二步：查看 Buffer Pool 整体状态

执行：

```sql
SHOW GLOBAL STATUS LIKE 'Innodb_buffer_pool%';
```

常用指标包括：

- `Innodb_buffer_pool_pages_total`：Buffer Pool 总页数。
- `Innodb_buffer_pool_pages_data`：包含数据的页数。
- `Innodb_buffer_pool_pages_free`：空闲页数。
- `Innodb_buffer_pool_pages_dirty`：脏页数。
- `Innodb_buffer_pool_read_requests`：逻辑读请求数量。
- `Innodb_buffer_pool_reads`：无法从缓存满足，需要读取磁盘的次数。
- `Innodb_buffer_pool_write_requests`：写请求数量。
- `Innodb_buffer_pool_pages_flushed`：已经刷新的页面数量。
- `Innodb_buffer_pool_wait_free`：等待获得空闲页的次数。

这些指标大多是 MySQL 启动以来的累计值，不能只看绝对数值。应间隔一段时间采集并计算增量。

## 8. 第三步：计算 Buffer Pool 命中率

可以使用以下近似公式：

```text
命中率
= 1 - Innodb_buffer_pool_reads / Innodb_buffer_pool_read_requests
```

查询示例：

```sql
SELECT
    ROUND(
        (
            1 -
            (
                reads.variable_value /
                NULLIF(read_requests.variable_value, 0)
            )
        ) * 100,
        4
    ) AS buffer_pool_hit_rate
FROM performance_schema.global_status reads
JOIN performance_schema.global_status read_requests
WHERE reads.variable_name = 'Innodb_buffer_pool_reads'
  AND read_requests.variable_name = 'Innodb_buffer_pool_read_requests';
```

命中率较高通常表示大部分逻辑读取可以从 Buffer Pool 满足，但不能只凭命中率判断性能。

例如：

- 命中率达到 99%，但请求量极大，剩余 1% 的物理读仍可能很多。
- 命中率较低可能是数据库刚刚重启。
- 离线扫描任务可能暂时降低命中率。
- 某个关键接口的热点页可能被淘汰，但整体命中率仍然很高。

更有价值的分析方式是观察单位时间内：

```text
Innodb_buffer_pool_reads 的增量
```

并将其与磁盘读取、SQL 延迟和业务流量进行对比。

## 9. 第四步：连续采样物理读取

第一次记录：

```sql
SHOW GLOBAL STATUS LIKE 'Innodb_buffer_pool_reads';
```

间隔一分钟再次记录，计算两次结果的差值。

也可以查询：

```sql
SELECT
    VARIABLE_NAME,
    VARIABLE_VALUE
FROM performance_schema.global_status
WHERE VARIABLE_NAME IN (
    'Innodb_buffer_pool_read_requests',
    'Innodb_buffer_pool_reads'
);
```

如果业务流量保持稳定，但 `Innodb_buffer_pool_reads` 增长速度突然加快，应检查：

- 是否出现大表扫描。
- 热点数据是否被挤出。
- Buffer Pool 是否过小。
- SQL 执行计划是否发生变化。
- 是否刚刚重启 MySQL。
- 是否新增了报表或导出任务。
- 表数据量是否快速增长。

## 10. 第五步：查看空闲页

查看空闲页：

```sql
SHOW GLOBAL STATUS LIKE 'Innodb_buffer_pool_pages_free';
```

Buffer Pool 运行一段时间后空闲页较少并不一定异常。InnoDB 会尽量使用 Buffer Pool 缓存数据。

真正需要关注的是：

- 是否频繁等待空闲页。
- 页面淘汰和加载是否过于频繁。
- 是否存在高物理读。
- 脏页是否无法及时刷出。
- 磁盘 IO 是否已经饱和。

查看等待空闲页次数：

```sql
SHOW GLOBAL STATUS LIKE 'Innodb_buffer_pool_wait_free';
```

如果 `Innodb_buffer_pool_wait_free` 持续增加，说明 InnoDB 需要空闲页时无法及时获得，可能与 Buffer Pool 压力、脏页积压或磁盘刷盘能力不足有关。

## 11. 第六步：查看脏页比例

查询脏页和总页数：

```sql
SHOW GLOBAL STATUS
WHERE Variable_name IN (
    'Innodb_buffer_pool_pages_dirty',
    'Innodb_buffer_pool_pages_total'
);
```

计算脏页比例：

```sql
SELECT
    ROUND(
        dirty.variable_value /
        NULLIF(total.variable_value, 0) * 100,
        2
    ) AS dirty_page_ratio
FROM performance_schema.global_status dirty
JOIN performance_schema.global_status total
WHERE dirty.variable_name = 'Innodb_buffer_pool_pages_dirty'
  AND total.variable_name = 'Innodb_buffer_pool_pages_total';
```

脏页比例持续升高可能说明：

- 写入量过大。
- 磁盘写入性能不足。
- 后台刷盘速度跟不上。
- Checkpoint 压力较大。
- IO 容量参数与磁盘能力不匹配。
- 存在集中批量更新。

脏页多不一定立即表示故障，应结合脏页变化趋势、刷盘速度和业务响应时间判断。

## 12. 第七步：查看 InnoDB 状态

执行：

```sql
SHOW ENGINE INNODB STATUS;
```

重点关注 `BUFFER POOL AND MEMORY` 部分。

常见指标包括：

```text
Buffer pool size
Free buffers
Database pages
Old database pages
Modified db pages
Pending reads
Pending writes
Pages read
Pages created
Pages written
Buffer pool hit rate
```

重点分析：

- `Free buffers` 是否长期过少。
- `Modified db pages` 是否持续升高。
- `Pending reads` 是否积压。
- `Pending writes` 是否积压。
- `Pages read` 增长是否过快。
- Buffer Pool Hit Rate 是否下降。
- 后台刷盘是否跟不上。

`SHOW ENGINE INNODB STATUS` 是某一时刻的快照，应在问题发生期间多次采集。

## 13. 第八步：查看 Buffer Pool 分实例状态

可以查询：

```sql
SELECT *
FROM information_schema.INNODB_BUFFER_POOL_STATS;
```

该表可以查看各 Buffer Pool 实例的：

- Pool 大小。
- 空闲页数量。
- 数据页数量。
- 脏页数量。
- 读写请求。
- 页读取和写入。
- 命中率。
- LRU 相关信息。

如果不同实例之间负载差异明显，应结合 MySQL 版本、Buffer Pool 大小和访问模式继续分析。

## 14. 第九步：检查磁盘 IO

执行：

```bash
iostat -x 1 10
```

重点关注：

- `r/s`：每秒读取次数。
- `w/s`：每秒写入次数。
- `rkB/s`：每秒读取量。
- `wkB/s`：每秒写入量。
- `await`：平均 IO 响应时间。
- `aqu-sz`：平均 IO 队列长度。
- `%util`：磁盘繁忙程度。

如果 `Innodb_buffer_pool_reads` 增长很快，同时磁盘读取和 `await` 升高，说明缓存未命中已经对数据库性能产生实际影响。

如果脏页比例高、写入量大，并且磁盘写入延迟升高，应重点检查刷脏能力和存储性能。

## 15. 第十步：检查系统内存和 Swap

执行：

```bash
free -h
```

查看 Swap：

```bash
swapon --show
```

观察换页：

```bash
vmstat 1 10
```

重点关注：

- `MemAvailable`。
- Swap 使用量。
- `si`。
- `so`。
- MySQL 进程 RSS。

如果系统频繁将 MySQL 内存页换入换出，性能可能严重下降。

Buffer Pool 配置过大时，MySQL 连接级内存、系统进程和其他服务可能共同造成物理内存不足。此时不能只关注 Buffer Pool 命中率，还需要降低整体内存压力。

## 16. 第十一步：检查大表扫描

查看当前长时间 SQL：

```sql
SHOW FULL PROCESSLIST;
```

使用 Performance Schema 查找扫描行数较多的 SQL：

```sql
SELECT
    DIGEST_TEXT,
    COUNT_STAR,
    SUM_ROWS_EXAMINED,
    SUM_ROWS_SENT,
    ROUND(AVG_TIMER_WAIT / 1000000000, 2) AS avg_ms
FROM performance_schema.events_statements_summary_by_digest
WHERE DIGEST_TEXT IS NOT NULL
ORDER BY SUM_ROWS_EXAMINED DESC
LIMIT 20;
```

重点关注：

```text
扫描行数很多，但返回行数很少
```

这通常说明 SQL 过滤效率较低，可能存在：

- 缺少索引。
- 索引失效。
- 大范围扫描。
- 深分页。
- 不合理多表关联。
- 报表全表扫描。

对目标 SQL 执行：

```sql
EXPLAIN
SELECT ...;
```

检查：

- `type`。
- `key`。
- `rows`。
- `filtered`。
- `Extra`。

## 17. 第十二步：检查热点数据是否被污染

Buffer Pool 使用改进的 LRU 机制，将缓存区域区分为新生区和旧生区，目的是降低全表扫描对热点页的影响。

相关参数包括：

```sql
SHOW VARIABLES LIKE 'innodb_old_blocks_pct';
```

```sql
SHOW VARIABLES LIKE 'innodb_old_blocks_time';
```

如果存在大表扫描后热点查询突然变慢，应检查：

- 是否新增全量报表查询。
- 是否发生备份或数据校验。
- 大表扫描频率是否过高。
- 热点数据集是否接近 Buffer Pool 容量。
- `innodb_old_blocks_time` 是否适合当前负载。
- 是否应将分析任务迁移到从库。

不建议只通过调整 LRU 参数解决问题。更重要的是优化扫描 SQL，并将离线任务与在线业务隔离。

## 18. 第十三步：检查表和索引大小

查看表大小：

```sql
SELECT
    TABLE_SCHEMA,
    TABLE_NAME,
    ROUND(DATA_LENGTH / 1024 / 1024, 2) AS data_mb,
    ROUND(INDEX_LENGTH / 1024 / 1024, 2) AS index_mb,
    ROUND((DATA_LENGTH + INDEX_LENGTH) / 1024 / 1024, 2) AS total_mb
FROM information_schema.TABLES
WHERE TABLE_SCHEMA NOT IN (
    'mysql',
    'information_schema',
    'performance_schema',
    'sys'
)
ORDER BY DATA_LENGTH + INDEX_LENGTH DESC
LIMIT 20;
```

需要评估：

- 核心表和索引总大小。
- 热点数据集大小。
- Buffer Pool 是否可以容纳热点数据。
- 是否存在无效或重复索引。
- 历史数据是否长期占用表空间。
- 是否可以进行数据归档。
- 是否存在不必要的大字段。

不能简单要求 Buffer Pool 容纳全部数据库数据。实际目标通常是尽量容纳高频访问的热点数据和索引。

## 19. 第十四步：检查写入和刷脏压力

查看相关状态：

```sql
SHOW GLOBAL STATUS LIKE 'Innodb_buffer_pool_pages_flushed';
```

```sql
SHOW GLOBAL STATUS LIKE 'Innodb_data_writes';
```

```sql
SHOW GLOBAL STATUS LIKE 'Innodb_data_fsyncs';
```

```sql
SHOW GLOBAL STATUS LIKE 'Innodb_os_log_pending%';
```

检查 IO 容量参数：

```sql
SHOW VARIABLES LIKE 'innodb_io_capacity';
```

```sql
SHOW VARIABLES LIKE 'innodb_io_capacity_max';
```

这些参数用于告诉 InnoDB 后台任务大致可以使用多少 IO 能力。

如果设置过低，脏页可能无法及时刷出；设置过高，则后台刷盘可能抢占过多磁盘资源。

参数应根据实际存储设备的 IOPS 和业务负载进行测试，不能直接使用固定模板。

## 20. 第十五步：检查 Buffer Pool 预热

MySQL 重启后 Buffer Pool 变冷，会导致物理读取增加。

查看是否在关闭时保存 Buffer Pool 状态：

```sql
SHOW VARIABLES LIKE 'innodb_buffer_pool_dump_at_shutdown';
```

查看是否在启动时加载：

```sql
SHOW VARIABLES LIKE 'innodb_buffer_pool_load_at_startup';
```

手动保存：

```sql
SET GLOBAL innodb_buffer_pool_dump_now = ON;
```

手动加载：

```sql
SET GLOBAL innodb_buffer_pool_load_now = ON;
```

查看加载状态：

```sql
SHOW STATUS LIKE 'Innodb_buffer_pool_load_status';
```

Buffer Pool Dump 保存的主要是页标识信息，不是直接保存全部数据页。启动时仍然需要从磁盘重新加载，因此预热过程可能产生较高磁盘读取。

## 21. Buffer Pool 大小调整原则

在专用 MySQL 服务器上，Buffer Pool 通常可以占用较大比例的物理内存，但不能机械地套用固定百分比。

应为以下内容预留内存：

- 操作系统。
- MySQL 连接线程。
- 排序缓冲区。
- Join Buffer。
- Read Buffer。
- 临时表。
- Performance Schema。
- Binlog Cache。
- 备份和监控程序。
- 文件系统缓存。
- 其他数据库组件。

评估时需要考虑：

```text
MySQL 总内存
≈ 全局内存
+ Buffer Pool
+ 连接级内存 × 活跃连接数
+ 临时内存
+ 其他内部内存
```

容器环境中必须根据容器 Memory Limit 配置，而不是宿主机物理内存。

## 22. 动态调整 Buffer Pool

部分 MySQL 版本支持在线调整 Buffer Pool 大小：

```sql
SET GLOBAL innodb_buffer_pool_size = <字节数>;
```

例如调整为 8GB：

```sql
SET GLOBAL innodb_buffer_pool_size = 8589934592;
```

调整前应确认：

- 当前 MySQL 版本是否支持。
- 配置值是否满足块大小要求。
- 系统是否有足够可用内存。
- 调整过程中是否影响业务。
- 配置文件是否同步修改。
- 容器内存限制是否足够。

在线调大 Buffer Pool 可能增加内存压力，调小则需要淘汰页面，也可能影响查询性能。生产环境应在监控下逐步调整。

## 23. 常见临时处理措施

Buffer Pool 问题已经影响业务时，可以采取：

1. 暂停大表扫描和报表任务。
2. 暂停非核心数据导出。
3. 降低批量任务并发度。
4. 对高扫描量接口临时限流。
5. 优化或终止异常 SQL。
6. 将统计查询切换到从库。
7. 降低高频写入任务速度。
8. 扩容磁盘性能。
9. 在内存充足时适当增加 Buffer Pool。
10. 如果发生 Swap，优先降低整体内存压力。
11. 将部分流量切换到其他数据库实例。
12. 对热点查询增加应用缓存。

临时措施只能缓解压力，后续仍需完成 SQL、索引、内存和存储优化。

## 24. 永久解决方案

### 24.1 合理配置 Buffer Pool

根据热点数据规模、服务器内存和实际负载调整 `innodb_buffer_pool_size`，并为其他内存区域预留安全空间。

### 24.2 优化 SQL 和索引

- 为高频条件增加合适索引。
- 避免全表扫描。
- 避免深分页。
- 限制返回数据量。
- 避免 `SELECT *`。
- 大结果集使用分页或流式处理。
- 优化多表关联。
- 清理无效和重复索引。

### 24.3 隔离在线和离线负载

将报表、导出、校验和分析任务迁移到：

- 从库。
- 只读实例。
- 独立分析数据库。
- 数据仓库。
- 离线计算系统。

### 24.4 优化写入和刷脏

- 将大批量写入拆分为小批次。
- 控制并发写入。
- 根据磁盘能力设置 IO 容量。
- 使用性能更好的 SSD 或 NVMe。
- 避免业务高峰集中执行数据更新。
- 监控脏页和 Pending Writes。

### 24.5 归档历史数据

将不再频繁访问的历史数据迁移到归档表或独立存储，缩小核心表和热点索引规模。

### 24.6 建立缓存预热机制

MySQL 重启或主从切换后，应通过 Buffer Pool 状态恢复、逐步放量或业务预热降低冷缓存对在线流量的影响。

## 25. 不建议直接执行的操作

### 25.1 不建议盲目增大 Buffer Pool

Buffer Pool 过大可能导致系统 Swap、容器 OOM 或 MySQL 进程被终止。

### 25.2 不建议直接重启 MySQL

重启会使缓存变冷，短时间内增加磁盘读取，还会中断当前连接和事务。

### 25.3 不建议只看命中率

整体命中率很高时，关键接口仍可能因为热点页被淘汰而变慢。应结合物理读增量、SQL 延迟和磁盘 IO 分析。

### 25.4 不建议使用全表扫描预热

直接扫描全部表可能产生大量磁盘 IO，并将不需要的冷数据加载到 Buffer Pool，反而挤出热点数据。

### 25.5 不建议盲目提高 IO 容量参数

设置超过实际磁盘能力的值，可能造成后台刷盘抢占大量 IO，使前台查询延迟升高。

### 25.6 不建议忽略连接级内存

只按照物理内存减去 Buffer Pool 估算剩余空间是不够的。高并发连接的排序、Join 和临时表内存可能产生很大峰值。

## 26. 监控与预防建议

建议持续监控以下指标：

- `innodb_buffer_pool_size`。
- Buffer Pool 数据页数量。
- Buffer Pool 空闲页数量。
- Buffer Pool 脏页数量。
- Buffer Pool 命中率。
- `Innodb_buffer_pool_reads` 增长速率。
- `Innodb_buffer_pool_read_requests`。
- `Innodb_buffer_pool_wait_free`。
- `Innodb_buffer_pool_pages_flushed`。
- Pending Reads。
- Pending Writes。
- 数据库磁盘读取量。
- 数据库磁盘写入量。
- 磁盘响应时间。
- MySQL 进程 RSS。
- 系统可用内存。
- Swap 换入和换出。
- SQL 扫描行数。
- 慢查询数量。
- 脏页比例。
- Buffer Pool 加载状态。

建议设置以下告警：

1. 物理读取速率持续升高。
2. `Innodb_buffer_pool_wait_free` 持续增加。
3. 脏页比例长期处于高位。
4. Pending Reads 或 Pending Writes 持续不为 0。
5. 磁盘 `await` 明显高于正常基线。
6. 系统开始频繁使用 Swap。
7. 大表扫描 SQL 突然增加。
8. MySQL 重启后缓存预热失败。
9. Buffer Pool 命中率和接口性能同步下降。
10. MySQL 进程内存接近容器限制。

## 27. 推荐排查流程

MySQL Buffer Pool 性能问题可以按照以下顺序排查：

1. 查看 `innodb_buffer_pool_size` 和相关参数。
2. 检查服务器物理内存和容器内存限制。
3. 查看 Buffer Pool 总页、数据页、空闲页和脏页。
4. 查看逻辑读和物理读累计值。
5. 连续采样并计算物理读取增长速度。
6. 计算并观察 Buffer Pool 命中率趋势。
7. 检查 `Innodb_buffer_pool_wait_free` 是否增加。
8. 检查脏页比例和页面刷盘速度。
9. 使用 `SHOW ENGINE INNODB STATUS` 查看 Pending IO。
10. 使用 `iostat` 检查磁盘读取、写入和响应时间。
11. 使用 `free` 和 `vmstat` 检查 Swap。
12. 使用 Performance Schema 查找扫描行数较大的 SQL。
13. 使用 `EXPLAIN` 检查全表扫描和索引问题。
14. 检查是否存在报表、导出或大表扫描任务。
15. 检查核心表、索引和热点数据规模。
16. 检查 MySQL 重启后的 Buffer Pool 预热情况。
17. 采取暂停扫描任务、限流或切换从库等临时措施。
18. 优化 SQL、索引、Buffer Pool 和存储配置。
19. 使用相同负载验证物理读、命中率和接口延迟。
20. 建立长期 Buffer Pool 和磁盘 IO 监控。

## 28. 常用排查命令汇总

```sql
-- 查看 Buffer Pool 大小
SHOW VARIABLES LIKE 'innodb_buffer_pool_size';

-- 查看 Buffer Pool 相关配置
SHOW VARIABLES LIKE 'innodb_buffer_pool%';

-- 查看 Buffer Pool 相关状态
SHOW GLOBAL STATUS LIKE 'Innodb_buffer_pool%';

-- 查看总页数、空闲页和脏页
SHOW GLOBAL STATUS
WHERE Variable_name IN (
    'Innodb_buffer_pool_pages_total',
    'Innodb_buffer_pool_pages_data',
    'Innodb_buffer_pool_pages_free',
    'Innodb_buffer_pool_pages_dirty'
);

-- 查看逻辑读和物理读
SHOW GLOBAL STATUS
WHERE Variable_name IN (
    'Innodb_buffer_pool_read_requests',
    'Innodb_buffer_pool_reads'
);

-- 查看等待空闲页次数
SHOW GLOBAL STATUS LIKE 'Innodb_buffer_pool_wait_free';

-- 查看页面刷新数量
SHOW GLOBAL STATUS LIKE 'Innodb_buffer_pool_pages_flushed';

-- 查看 InnoDB 状态
SHOW ENGINE INNODB STATUS;

-- 查看各 Buffer Pool 实例状态
SELECT *
FROM information_schema.INNODB_BUFFER_POOL_STATS;

-- 查看 IO 容量参数
SHOW VARIABLES LIKE 'innodb_io_capacity';
SHOW VARIABLES LIKE 'innodb_io_capacity_max';

-- 查看 Buffer Pool 保存和加载配置
SHOW VARIABLES LIKE 'innodb_buffer_pool_dump_at_shutdown';
SHOW VARIABLES LIKE 'innodb_buffer_pool_load_at_startup';

-- 查看 Buffer Pool 加载状态
SHOW STATUS LIKE 'Innodb_buffer_pool_load_status';

-- 保存 Buffer Pool 状态
SET GLOBAL innodb_buffer_pool_dump_now = ON;

-- 加载 Buffer Pool 状态
SET GLOBAL innodb_buffer_pool_load_now = ON;

-- 查看当前 SQL
SHOW FULL PROCESSLIST;

-- 查看 SQL 扫描情况
SELECT
    DIGEST_TEXT,
    COUNT_STAR,
    SUM_ROWS_EXAMINED,
    SUM_ROWS_SENT,
    ROUND(AVG_TIMER_WAIT / 1000000000, 2) AS avg_ms
FROM performance_schema.events_statements_summary_by_digest
WHERE DIGEST_TEXT IS NOT NULL
ORDER BY SUM_ROWS_EXAMINED DESC
LIMIT 20;

-- 查看 SQL 执行计划
EXPLAIN SELECT ...;

-- 查看大表和索引大小
SELECT
    TABLE_SCHEMA,
    TABLE_NAME,
    ROUND(DATA_LENGTH / 1024 / 1024, 2) AS data_mb,
    ROUND(INDEX_LENGTH / 1024 / 1024, 2) AS index_mb,
    ROUND((DATA_LENGTH + INDEX_LENGTH) / 1024 / 1024, 2) AS total_mb
FROM information_schema.TABLES
ORDER BY DATA_LENGTH + INDEX_LENGTH DESC
LIMIT 20;
```

```bash
# 查看系统内存
free -h

# 查看 Swap
swapon --show

# 查看内存换页
vmstat 1 10

# 查看磁盘 IO
iostat -x 1 10

# 查看 MySQL 进程内存
ps -eo pid,user,%mem,rss,vsz,cmd --sort=-rss | head -20

# 查看系统负载
top
```

## 29. 排查结论模板

### 故障现象

MySQL 查询响应时间明显上升，多个核心接口出现超时，数据库磁盘读取量和平均 IO 响应时间持续升高。

### 故障确认

通过连续采集发现 `Innodb_buffer_pool_reads` 在短时间内快速增长，Buffer Pool 物理读明显增加。Performance Schema 显示一条报表 SQL 扫描了数千万行数据，执行计划为全表扫描。

### 根本原因

新上线的报表任务每隔数分钟扫描完整订单表。大量历史冷数据被加载到 Buffer Pool，挤出了在线订单接口频繁访问的热点数据和索引页，导致核心查询需要重新从磁盘读取页面。

### 临时处理

暂停报表任务，对报表接口进行限流，并将相关查询切换到只读从库。随着热点页重新加载，核心接口响应时间逐步恢复。

### 永久修复

为报表查询增加合适索引并按时间范围分批查询；将报表和数据导出任务固定运行在独立只读实例；根据热点数据规模适当调整 Buffer Pool，并增加物理读取速率和大表扫描监控。

### 验证结果

修复后在相同业务流量下执行报表任务，主库 `Innodb_buffer_pool_reads` 增长保持稳定，磁盘响应时间恢复正常，核心接口未再次出现明显延迟。

## 30. 总结

MySQL Buffer Pool 性能排查的重点是判断缓存是否能够有效承载热点数据，以及缓存未命中、脏页和刷盘是否已经对磁盘和业务造成影响。

排查时应同时查看 Buffer Pool 大小、逻辑读、物理读、空闲页、脏页、等待空闲页次数和 InnoDB Pending IO。由于多数状态值是累计值，必须通过连续采样计算增长速度，不能只根据某个绝对值判断。

Buffer Pool 命中率很高并不代表一定没有问题，命中率下降也不一定说明必须扩容。大表扫描、SQL 索引失效、数据库冷启动、热点数据增长和磁盘性能不足，都可能造成类似现象。

永久解决方案通常包括合理调整 Buffer Pool、优化 SQL 和索引、隔离报表与在线业务、控制批量写入、提升存储性能以及归档历史数据。任何内存调整都应为连接级缓冲区、操作系统和其他组件预留空间，避免为了提高缓存命中率而引发系统 Swap 或 OOM。