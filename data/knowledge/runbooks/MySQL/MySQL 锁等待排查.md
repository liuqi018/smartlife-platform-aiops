# MySQL 锁等待排查

## 1. 问题概述

MySQL 锁等待是指一个事务需要获取某个锁，但该锁已经被其他未提交事务持有，因此当前事务只能等待持锁事务提交、回滚或释放锁。

锁是 MySQL 保证事务隔离性和数据一致性的重要机制。正常、短暂的锁等待不一定是故障，但如果持锁事务执行时间过长、事务没有及时提交，或者多个事务竞争相同数据，就可能导致大量请求阻塞。

严重的锁等待可能引发：

1. SQL 执行时间明显增加。
2. 接口请求超时。
3. 数据库连接长期不释放。
4. 应用连接池逐渐耗尽。
5. 应用线程池任务堆积。
6. 数据库活跃连接数持续升高。
7. 主从复制延迟增加。
8. 事务回滚数量增加。
9. 最终出现锁等待超时或数据库死锁。

锁等待排查的核心目标包括：

1. 确认当前是否存在锁等待。
2. 找出被阻塞的事务和 SQL。
3. 找出持有锁的阻塞事务和 SQL。
4. 判断持有的锁类型和锁定范围。
5. 分析事务为什么长时间没有释放锁。
6. 采取安全的临时处理措施。
7. 从 SQL、索引、事务和业务并发设计上解决根本问题。

## 2. 常见问题现象

MySQL 出现严重锁等待时，通常会出现以下现象：

1. 更新、删除或插入 SQL 长时间不返回。
2. 普通查询或加锁查询响应变慢。
3. 应用日志出现锁等待超时。
4. 数据库连接池活跃连接数达到上限。
5. 应用获取数据库连接超时。
6. `SHOW PROCESSLIST` 中出现大量等待会话。
7. `information_schema.INNODB_TRX` 中存在长事务。
8. `performance_schema.data_lock_waits` 中出现等待关系。
9. 数据库 `Threads_running` 数量升高。
10. 部分业务接口集中超时。
11. 批量更新任务长时间无法完成。
12. DDL 操作一直等待。
13. MySQL CPU 不一定很高，但请求无法继续执行。
14. 服务重启后问题暂时消失。
15. 监控中事务执行时间和锁等待时间明显增加。

常见错误如下：

```text
Lock wait timeout exceeded; try restarting transaction
```

```text
Deadlock found when trying to get lock; try restarting transaction
```

```text
Waiting for table metadata lock
```

需要区分锁等待、锁等待超时和死锁：

- 锁等待：事务正在等待其他事务释放锁。
- 锁等待超时：等待时间超过 `innodb_lock_wait_timeout`。
- 死锁：多个事务形成循环等待，InnoDB 通常会自动回滚其中一个事务。

## 3. MySQL 常见锁类型

### 3.1 共享锁

共享锁也叫 S 锁。多个事务可以同时持有同一记录的共享锁，但不能同时对该记录进行修改。

常见加锁方式：

```sql
SELECT *
FROM orders
WHERE id = 1
FOR SHARE;
```

### 3.2 排他锁

排他锁也叫 X 锁。事务持有某条记录的排他锁后，其他事务通常不能再获取该记录的共享锁或排他锁。

以下操作通常会获取排他锁：

```sql
UPDATE orders SET status = 1 WHERE id = 1;
```

```sql
DELETE FROM orders WHERE id = 1;
```

```sql
SELECT *
FROM orders
WHERE id = 1
FOR UPDATE;
```

### 3.3 记录锁

记录锁锁定索引中的具体记录。

如果按照主键或唯一索引进行等值查询并加锁，通常会锁定对应索引记录。

### 3.4 间隙锁

间隙锁锁定索引记录之间的间隙，主要用于防止其他事务在范围内插入新记录。

间隙锁通常出现在 InnoDB 可重复读隔离级别的范围查询中。

### 3.5 Next-Key Lock

Next-Key Lock 是记录锁和间隙锁的组合，通常锁定一个索引记录以及它前面的间隙。

### 3.6 意向锁

意向锁是表级锁，用于表示事务准备在表中某些记录上获取共享锁或排他锁。

常见类型包括：

- IS：意向共享锁。
- IX：意向排他锁。

### 3.7 元数据锁

元数据锁简称 MDL，用于保护表结构和表对象的一致性。

普通查询和事务会持有表的元数据读锁，DDL 操作通常需要元数据写锁。如果有事务长期不结束，`ALTER TABLE` 等 DDL 可能一直等待。

## 4. 锁等待的常见原因

### 4.1 长事务没有及时提交

事务执行完 SQL 后长时间不提交或回滚，会继续持有锁。

常见原因包括：

- 程序忘记提交事务。
- 异常处理没有回滚。
- 手动开启事务后长时间未操作。
- 事务中等待用户输入。
- 数据库客户端窗口长期保持事务。
- 连接池归还连接前事务未结束。

### 4.2 事务中包含耗时操作

在数据库事务中执行以下操作会延长持锁时间：

- 远程接口调用。
- 文件上传和处理。
- 消息发送。
- 大量业务计算。
- 线程等待。
- 用户交互。
- 多次循环查询和更新。

事务应尽量只包含必要的数据库操作。

### 4.3 SQL 没有使用索引

更新或删除语句没有使用合适索引时，可能扫描并锁定大量记录。

例如：

```sql
UPDATE orders
SET status = 2
WHERE user_name = 'test';
```

如果 `user_name` 没有索引，可能导致锁定范围扩大，并增加与其他事务冲突的概率。

### 4.4 批量更新数据过多

单个事务一次更新、删除或插入大量数据，会持有大量锁，并且在事务提交前不会释放。

### 4.5 并发更新相同热点数据

大量请求同时更新同一条或同一小批数据，会形成热点行竞争。

常见场景包括：

- 库存扣减。
- 账户余额更新。
- 热点商品计数。
- 同一任务状态更新。
- 同一用户数据更新。
- 全局序号生成。

### 4.6 事务加锁顺序不一致

事务 A 按照 `记录1 → 记录2` 的顺序更新，事务 B 按照 `记录2 → 记录1` 的顺序更新，可能产生锁等待甚至死锁。

### 4.7 范围更新导致间隙锁

在可重复读隔离级别下，范围查询和更新可能产生间隙锁或 Next-Key Lock，阻止其他事务在相关范围内插入数据。

### 4.8 DDL 等待元数据锁

长事务访问过某张表后没有结束，DDL 操作需要等待其元数据锁释放。

DDL 等待期间，后续访问同一张表的请求还可能继续排队，造成业务阻塞范围扩大。

## 5. 排查前的注意事项

生产环境排查锁等待时，应注意：

1. 不要看到阻塞连接就立即执行 `KILL`。
2. 不要只终止被阻塞事务，应优先定位阻塞源。
3. 终止事务可能触发长时间回滚。
4. 大事务回滚期间仍可能占用资源。
5. 不要直接重启 MySQL 作为首选方案。
6. 不要盲目缩短锁等待超时时间。
7. 不要在未确认业务影响时终止 DDL。
8. 应保存阻塞链、SQL、事务和客户端信息。
9. 应记录问题发生时间和相关业务操作。
10. 处理前应确认主库、从库和具体实例。

建议先保存：

```sql
SHOW FULL PROCESSLIST;
SHOW ENGINE INNODB STATUS;
SELECT * FROM information_schema.INNODB_TRX;
SELECT * FROM performance_schema.data_locks;
SELECT * FROM performance_schema.data_lock_waits;
```

## 6. 第一步：查看当前会话

执行：

```sql
SHOW FULL PROCESSLIST;
```

重点关注：

- `Id`：连接 ID。
- `User`：数据库用户。
- `Host`：客户端地址。
- `db`：当前数据库。
- `Command`：当前命令。
- `Time`：当前状态持续时间。
- `State`：会话当前状态。
- `Info`：正在执行的 SQL。

常见锁等待状态包括：

```text
Waiting for table metadata lock
```

```text
Waiting for row lock
```

```text
Updating
```

```text
statistics
```

`SHOW PROCESSLIST` 只能展示当前会话状态，不能完整说明谁在等待谁，还需要结合事务表和 Performance Schema 分析。

## 7. 第二步：查看当前事务

执行：

```sql
SELECT
    trx_id,
    trx_state,
    trx_started,
    trx_wait_started,
    trx_mysql_thread_id,
    trx_query,
    trx_tables_locked,
    trx_lock_structs,
    trx_rows_locked,
    trx_rows_modified
FROM information_schema.INNODB_TRX
ORDER BY trx_started;
```

重点关注：

- `trx_id`：事务 ID。
- `trx_state`：事务状态。
- `trx_started`：事务开始时间。
- `trx_wait_started`：开始等待时间。
- `trx_mysql_thread_id`：对应连接 ID。
- `trx_query`：当前 SQL。
- `trx_rows_locked`：锁定行数。
- `trx_rows_modified`：修改行数。

如果某个事务开始时间很早，并且锁定大量行，但当前没有执行 SQL，应重点怀疑长事务或未提交事务。

## 8. 第三步：查看锁等待关系

MySQL 8.0 可以执行：

```sql
SELECT *
FROM performance_schema.data_lock_waits;
```

查看锁信息：

```sql
SELECT *
FROM performance_schema.data_locks;
```

关联查看等待事务和阻塞事务：

```sql
SELECT
    waiting_trx.trx_id AS waiting_trx_id,
    waiting_trx.trx_mysql_thread_id AS waiting_thread_id,
    waiting_trx.trx_query AS waiting_query,
    blocking_trx.trx_id AS blocking_trx_id,
    blocking_trx.trx_mysql_thread_id AS blocking_thread_id,
    blocking_trx.trx_query AS blocking_query
FROM performance_schema.data_lock_waits w
JOIN information_schema.INNODB_TRX waiting_trx
    ON waiting_trx.trx_id = w.REQUESTING_ENGINE_TRANSACTION_ID
JOIN information_schema.INNODB_TRX blocking_trx
    ON blocking_trx.trx_id = w.BLOCKING_ENGINE_TRANSACTION_ID;
```

不同 MySQL 小版本中的字段和事务 ID 类型可能存在差异，应根据当前版本确认。

如果安装了 `sys` Schema，可以执行：

```sql
SELECT *
FROM sys.innodb_lock_waits;
```

该视图通常可以更直观地显示：

- 等待事务。
- 阻塞事务。
- 等待 SQL。
- 阻塞 SQL。
- 等待时间。
- 锁定表。
- 可用于终止会话的语句。

## 9. 第四步：查看 InnoDB 状态

执行：

```sql
SHOW ENGINE INNODB STATUS;
```

重点关注：

- `TRANSACTIONS`。
- 当前事务。
- 锁等待。
- 最近死锁。
- 锁定记录。
- 回滚情况。
- 历史列表长度。

该命令输出的是某一时刻的 InnoDB 状态，其中最近死锁信息只保留最近一次，需要及时保存。

如果需要长期记录所有死锁，可以评估启用：

```sql
SET GLOBAL innodb_print_all_deadlocks = ON;
```

启用后，死锁信息会写入 MySQL 错误日志。长期启用前应考虑日志量。

## 10. 第五步：定位阻塞源

排查锁等待的重点不是只找等待时间最长的 SQL，而是找到阻塞其他事务的源头事务。

典型阻塞链如下：

```text
事务 A 持有锁
    ↓
事务 B 等待事务 A
    ↓
事务 C 等待事务 B
    ↓
事务 D 等待事务 C
```

事务 A 可能当前处于 `Sleep` 状态，但事务尚未提交，因此仍然持有锁。

需要重点确认：

- 最上游阻塞事务的连接 ID。
- 事务开始时间。
- 客户端地址。
- 数据库用户。
- 当前 SQL。
- 上一条 SQL。
- 锁定表和索引。
- 锁定行数。
- 是否可以安全提交、回滚或终止。

阻塞事务当前 `trx_query` 可能为空，因为它已经执行完更新 SQL，正在等待应用提交。此时需要结合应用日志、审计日志或 Performance Schema 历史语句查找之前执行的 SQL。

## 11. 第六步：查看事务之前执行的 SQL

可以通过 Performance Schema 查看连接最近执行的语句：

```sql
SELECT
    THREAD_ID,
    EVENT_ID,
    SQL_TEXT,
    TIMER_WAIT,
    LOCK_TIME,
    ROWS_AFFECTED,
    ROWS_EXAMINED
FROM performance_schema.events_statements_history
WHERE THREAD_ID = <线程ID>
ORDER BY EVENT_ID DESC;
```

MySQL 连接 ID 与 Performance Schema 线程 ID 不完全相同，可以先查询：

```sql
SELECT
    THREAD_ID,
    PROCESSLIST_ID,
    PROCESSLIST_USER,
    PROCESSLIST_HOST
FROM performance_schema.threads
WHERE PROCESSLIST_ID = <连接ID>;
```

如果历史消费者未开启或历史记录已被覆盖，可能无法查到之前的语句，因此建议同时保留应用侧 SQL 日志和链路追踪信息。

## 12. 第七步：分析锁定表、索引和记录

查看 `performance_schema.data_locks` 时，应重点关注：

- `OBJECT_SCHEMA`：数据库名。
- `OBJECT_NAME`：表名。
- `INDEX_NAME`：索引名。
- `LOCK_TYPE`：锁类型。
- `LOCK_MODE`：锁模式。
- `LOCK_STATUS`：已获得或等待。
- `LOCK_DATA`：锁相关数据。

常见锁模式可能包括：

```text
S
X
IS
IX
S,GAP
X,GAP
X,REC_NOT_GAP
```

如果 `INDEX_NAME` 为某个普通索引，应结合 SQL 条件确认为什么锁定该索引范围。

如果 SQL 没有使用合适索引，InnoDB 可能扫描并对大量记录加锁，从而扩大锁冲突范围。

## 13. 第八步：检查 SQL 执行计划

对阻塞 SQL 或等待 SQL执行：

```sql
EXPLAIN
SELECT ...;
```

对于更新和删除语句，可以将条件部分转换为等价的 `SELECT` 进行执行计划分析，或者在支持的 MySQL 版本中谨慎使用相应的 `EXPLAIN`。

重点关注：

- `type` 是否为 `ALL`。
- 实际使用的索引。
- 预计扫描行数。
- 查询条件是否使用索引。
- 是否存在范围扫描。
- 是否发生隐式类型转换。
- 联合索引是否符合最左前缀原则。
- 锁定范围是否超出业务预期。

更新语句没有索引时，不仅执行慢，还可能锁定大量记录，造成严重并发冲突。

## 14. 长事务排查

查看运行时间较长的事务：

```sql
SELECT
    trx_id,
    trx_mysql_thread_id,
    trx_started,
    TIMESTAMPDIFF(SECOND, trx_started, NOW()) AS trx_seconds,
    trx_state,
    trx_query,
    trx_rows_locked,
    trx_rows_modified
FROM information_schema.INNODB_TRX
ORDER BY trx_started;
```

长事务的风险包括：

- 长时间持有锁。
- 阻止 Undo Log 清理。
- 增加历史版本数量。
- 增加回滚成本。
- 占用数据库连接。
- 扩大死锁概率。
- 影响 DDL 获取元数据锁。

应用代码中应重点检查：

- `@Transactional` 方法范围是否过大。
- 是否在事务中调用远程接口。
- 是否在事务中执行文件处理。
- 是否存在异常后未回滚。
- 是否关闭了自动提交后忘记提交。
- 是否存在人工操作连接长期不关闭。

## 15. 元数据锁等待排查

如果 `SHOW PROCESSLIST` 中出现：

```text
Waiting for table metadata lock
```

通常表示 DDL 或其他需要强元数据锁的操作正在等待。

常见场景：

```sql
ALTER TABLE orders ADD COLUMN remark VARCHAR(255);
```

此时有其他事务已经访问 `orders` 表但长期没有结束，导致 DDL 无法获取元数据写锁。

排查步骤：

1. 找到等待 MDL 的 DDL。
2. 查找正在访问目标表的长事务。
3. 确认事务是否未提交。
4. 判断是否可以安全终止阻塞事务。
5. 在业务低峰重新执行 DDL。

MySQL 8.0 可以查询：

```sql
SELECT *
FROM performance_schema.metadata_locks
WHERE OBJECT_SCHEMA = '<数据库名>'
  AND OBJECT_NAME = '<表名>';
```

需要特别注意，等待中的 DDL 可能阻塞后续新的查询，形成请求排队。因此，在线执行 DDL 时应设置合理超时并监控元数据锁。

## 16. 间隙锁和范围锁排查

在 InnoDB 可重复读隔离级别下，以下范围加锁查询可能产生 Next-Key Lock：

```sql
SELECT *
FROM orders
WHERE amount BETWEEN 100 AND 200
FOR UPDATE;
```

其他事务向该索引范围插入数据时可能被阻塞。

排查时应确认：

- 当前事务隔离级别。
- 查询是否为范围条件。
- 条件字段是否有索引。
- 锁定的是记录还是间隙。
- 是否真的需要 `FOR UPDATE`。
- 是否可以缩小查询范围。
- 是否可以通过唯一索引精确定位。

查看隔离级别：

```sql
SELECT @@transaction_isolation;
```

不能只为了减少间隙锁就随意修改事务隔离级别。修改前应评估一致性、幻读和业务逻辑影响。

## 17. 热点行锁竞争排查

如果大量事务同时更新同一条记录，就会形成热点行竞争。

例如：

```sql
UPDATE products
SET stock = stock - 1
WHERE id = 1001
  AND stock > 0;
```

秒杀、库存、计数器和余额场景尤其容易出现热点行。

处理方向包括：

- 缩短事务时间。
- 减少同一事务中的其他操作。
- 将热点数据分片。
- 使用队列串行化部分操作。
- 通过 Redis 等系统进行流量削峰。
- 合并批量更新。
- 使用乐观锁并限制重试次数。
- 根据一致性要求重新设计库存扣减流程。

不能通过无限重试解决热点锁冲突。重试过多会进一步增加数据库压力。

## 18. 批量更新和删除排查

以下 SQL 可能一次锁定大量记录：

```sql
UPDATE orders
SET status = 3
WHERE create_time < '2025-01-01';
```

```sql
DELETE FROM logs
WHERE create_time < '2025-01-01';
```

优化方向包括：

- 确保条件字段有合适索引。
- 分批更新或删除。
- 控制单批数据量。
- 每批单独提交。
- 在业务低峰执行。
- 监控锁等待和复制延迟。
- 对历史数据使用分区或归档方案。

示例：

```sql
DELETE FROM logs
WHERE create_time < '2025-01-01'
LIMIT 1000;
```

需要在应用或脚本中循环执行，并设置合理间隔，避免持续占用数据库资源。

## 19. 临时处理措施

锁等待已经严重影响业务时，可以采取以下措施：

1. 保存事务和锁等待信息。
2. 找到最上游阻塞事务。
3. 联系相关业务确认事务用途。
4. 优先让应用正常提交或回滚。
5. 对相关接口临时限流。
6. 暂停批量更新、删除或 DDL。
7. 将非核心任务调整到业务低峰。
8. 必要时终止确认可以安全终止的阻塞连接。
9. 观察事务回滚进度和数据库负载。
10. 恢复后持续监控是否再次出现。

终止查询：

```sql
KILL QUERY <连接ID>;
```

终止连接：

```sql
KILL CONNECTION <连接ID>;
```

对于持有锁但当前处于 Sleep 状态的事务，`KILL QUERY` 可能无法释放事务，需要根据实际情况处理连接。

终止大事务后可能触发长时间回滚，回滚期间数据库仍可能承受较高 IO 和锁压力。

## 20. 永久解决方案

### 20.1 缩小事务范围

事务中只保留必要的数据库操作，不在事务内执行远程调用、文件处理和长时间计算。

### 20.2 及时提交或回滚

确保所有事务在正常和异常路径中都能结束。

使用 Spring 时应检查：

- `@Transactional` 是否生效。
- 异常是否被错误捕获。
- 回滚异常类型是否正确。
- 是否存在自调用导致事务失效。
- 事务传播行为是否符合预期。
- 是否在事务中执行耗时调用。

### 20.3 增加合适索引

为更新、删除和加锁查询的条件字段建立合理索引，缩小扫描和锁定范围。

### 20.4 统一更新顺序

多个事务更新相同的一组记录时，应按照统一顺序执行。

例如，按照主键从小到大加锁：

```text
记录 1 → 记录 2 → 记录 3
```

避免不同事务使用相反顺序。

### 20.5 拆分大事务

将大批量操作拆分为多个小事务，减少单次持锁数量和持锁时间。

### 20.6 设置合理超时

查看锁等待超时：

```sql
SHOW VARIABLES LIKE 'innodb_lock_wait_timeout';
```

可以根据业务设置合理值，但缩短超时只会让等待事务更快失败，不能修复阻塞源。

应用应正确捕获锁等待超时，并根据业务幂等性决定是否有限重试。

## 21. 锁等待重试建议

锁等待超时或死锁回滚后，应用可能需要重试，但必须满足：

1. 操作具有幂等性。
2. 设置最大重试次数。
3. 使用随机退避或指数退避。
4. 不在数据库压力过高时立即重试。
5. 记录重试原因和次数。
6. 区分死锁、锁超时和普通异常。
7. 避免多个事务同时固定间隔重试。
8. 重试应重新开启完整事务。

错误的无限重试可能形成重试风暴，使锁竞争更加严重。

## 22. 不建议直接执行的操作

### 22.1 不建议直接 KILL 所有等待连接

等待事务通常不是根本原因。只终止等待方，阻塞事务仍然存在，新请求还会继续被阻塞。

### 22.2 不建议直接重启 MySQL

重启会中断全部连接和业务，并触发未提交事务恢复。应先定位和处理阻塞源。

### 22.3 不建议无限增大锁等待时间

增加 `innodb_lock_wait_timeout` 会让请求等待更久，占用更多连接和应用线程。

### 22.4 不建议盲目降低事务隔离级别

降低隔离级别可能减少部分锁，但也会改变一致性语义，应评估业务影响。

### 22.5 不建议在事务中执行外部调用

外部调用耗时不可控，会显著延长数据库锁持有时间。

### 22.6 不建议直接执行大范围更新和删除

即使 SQL 使用了索引，大事务仍可能持有大量锁并产生高额回滚成本。

## 23. 监控与预防建议

建议持续监控以下指标：

- 当前事务数量。
- 长事务数量。
- 最长事务运行时间。
- 锁等待事务数量。
- 锁等待总时间。
- 锁等待超时次数。
- 死锁次数。
- 数据库连接数。
- `Threads_running`。
- 数据库连接池使用率。
- SQL 执行时间。
- 更新和删除扫描行数。
- 大事务修改行数。
- 元数据锁等待。
- 主从复制延迟。
- Undo 历史列表长度。

建议建立以下机制：

1. 长事务自动告警。
2. 锁等待阻塞链监控。
3. 死锁日志自动采集。
4. 大批量更新审核。
5. DDL 变更前锁检查。
6. 核心更新 SQL 执行计划检查。
7. 数据库连接池泄漏检测。
8. 事务执行时间监控。
9. 热点数据并发监控。
10. 锁等待故障应急流程。

## 24. 推荐排查流程

MySQL 锁等待可以按照以下顺序排查：

1. 确认发生问题的数据库实例。
2. 使用 `SHOW FULL PROCESSLIST` 查看等待会话。
3. 查询 `INNODB_TRX` 查看当前事务和持续时间。
4. 查询 `data_lock_waits` 查看等待关系。
5. 使用 `sys.innodb_lock_waits` 快速定位阻塞链。
6. 找到最上游持锁事务。
7. 确认阻塞事务的连接、客户端和业务来源。
8. 查询该连接之前执行的 SQL。
9. 查看锁定的表、索引和锁模式。
10. 使用 `EXPLAIN` 检查阻塞 SQL 是否使用索引。
11. 检查是否存在长事务、大事务或热点行。
12. 检查是否为元数据锁或间隙锁。
13. 保存现场后评估是否终止阻塞事务。
14. 观察回滚过程和业务恢复情况。
15. 优化索引、事务范围和更新顺序。
16. 将大事务改为分批处理。
17. 增加锁等待、长事务和死锁监控。

## 25. 常用排查命令汇总

```sql
-- 查看当前会话
SHOW FULL PROCESSLIST;

-- 查看当前事务
SELECT *
FROM information_schema.INNODB_TRX
ORDER BY trx_started;

-- 查看锁
SELECT *
FROM performance_schema.data_locks;

-- 查看锁等待关系
SELECT *
FROM performance_schema.data_lock_waits;

-- 查看简化的 InnoDB 锁等待
SELECT *
FROM sys.innodb_lock_waits;

-- 查看 InnoDB 状态
SHOW ENGINE INNODB STATUS;

-- 查看元数据锁
SELECT *
FROM performance_schema.metadata_locks;

-- 查看连接与 Performance Schema 线程的对应关系
SELECT
    THREAD_ID,
    PROCESSLIST_ID,
    PROCESSLIST_USER,
    PROCESSLIST_HOST
FROM performance_schema.threads
WHERE PROCESSLIST_ID = <连接ID>;

-- 查看连接最近执行的语句
SELECT
    THREAD_ID,
    EVENT_ID,
    SQL_TEXT,
    LOCK_TIME,
    ROWS_AFFECTED,
    ROWS_EXAMINED
FROM performance_schema.events_statements_history
WHERE THREAD_ID = <线程ID>
ORDER BY EVENT_ID DESC;

-- 查看事务隔离级别
SELECT @@transaction_isolation;

-- 查看锁等待超时
SHOW VARIABLES LIKE 'innodb_lock_wait_timeout';

-- 查看死锁日志开关
SHOW VARIABLES LIKE 'innodb_print_all_deadlocks';

-- 查看 SQL 执行计划
EXPLAIN SELECT ...;

-- 查看表索引
SHOW INDEX FROM <表名>;

-- 终止查询
KILL QUERY <连接ID>;

-- 终止连接
KILL CONNECTION <连接ID>;
```

## 26. 排查结论模板

### 故障现象

订单更新接口大量超时，应用数据库连接池使用率达到上限，MySQL 中出现大量等待会话。

### 故障确认

通过 `sys.innodb_lock_waits` 定位到多个订单更新事务都在等待同一个阻塞事务。阻塞事务已经运行十余分钟，锁定了大量订单记录，当前连接处于 Sleep 状态。

### 根本原因

批量订单同步任务开启事务后更新了大量数据，随后在事务中调用外部物流接口。物流接口响应异常缓慢，导致数据库事务长时间未提交，并持续持有订单记录锁。在线订单更新请求访问相同记录时全部进入锁等待。

### 临时处理

暂停批量同步任务，保存事务和锁等待信息后终止阻塞连接，使其事务回滚。回滚完成后，在线订单请求逐步恢复。

### 永久修复

将外部物流接口调用移出数据库事务，批量订单更新改为小批次提交；为更新条件增加合适索引；增加事务执行时间、锁定行数和锁等待数量告警。

### 验证结果

修复后模拟物流接口超时，数据库事务能够在完成数据更新后及时提交，不再因外部调用长期持锁。并发订单更新测试中未出现持续锁等待。

## 27. 总结

MySQL 锁等待排查的关键是找到完整的等待关系，而不是只关注被阻塞的 SQL。真正需要处理的通常是阻塞链最上游、长时间持有锁且没有提交的事务。

可以通过 `SHOW FULL PROCESSLIST` 查看当前会话，通过 `INNODB_TRX` 查看事务状态，再结合 `performance_schema.data_lock_waits`、`data_locks` 或 `sys.innodb_lock_waits` 确认谁在等待谁。对于 DDL 长时间无法执行的问题，还应检查元数据锁和未结束事务。

锁等待的常见根本原因包括长事务、事务中执行远程调用、SQL 缺少索引、大批量更新、热点行竞争和事务加锁顺序不一致。临时终止阻塞连接可以恢复业务，但可能触发长时间回滚，操作前必须确认业务影响。

永久解决方案应缩小事务范围、及时提交或回滚、增加合理索引、统一更新顺序，并将大事务拆分为多个小事务。同时应建立长事务、锁等待、死锁和元数据锁监控，避免问题发展到连接池和线程池同时耗尽。