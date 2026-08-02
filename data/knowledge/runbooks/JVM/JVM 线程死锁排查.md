# JVM 线程死锁排查

## 1. 问题概述

线程死锁是指两个或多个线程在执行过程中相互等待对方持有的锁，导致所有相关线程都无法继续执行。

例如，线程 A 已经持有锁 `lockA`，准备获取锁 `lockB`；线程 B 已经持有锁 `lockB`，准备获取锁 `lockA`。由于两个线程都不会主动释放当前持有的锁，因此会永久等待。

```text
线程 A：持有 lockA → 等待 lockB
线程 B：持有 lockB → 等待 lockA
```

线程死锁通常不会直接导致 JVM 进程退出，而是表现为部分请求长期无响应、线程池逐渐耗尽、任务持续堆积。随着越来越多的请求受到影响，最终可能导致整个服务不可用。

## 2. 常见问题现象

JVM 发生线程死锁时，通常会出现以下现象：

1. 部分接口请求一直没有响应。
2. 接口超时数量持续增加。
3. Java 进程仍然存在，但无法正常提供服务。
4. CPU 使用率不一定高，甚至可能较低。
5. 大量线程处于 `BLOCKED` 状态。
6. Web 容器工作线程逐渐被占满。
7. 线程池活跃线程数达到上限。
8. 线程池任务队列持续增长。
9. 数据库连接长期不释放。
10. 定时任务停止执行。
11. 消息消费速度突然下降。
12. 服务健康检查失败。
13. 重启服务后暂时恢复。
14. `jstack` 输出中出现死锁提示。
15. 多次线程 Dump 中，相关线程始终停留在相同位置。

死锁与普通慢请求、数据库锁等待、线程池任务堆积的表现可能比较相似，需要通过线程 Dump 和业务监控进行确认。

## 3. 线程死锁产生的必要条件

线程死锁通常需要同时满足以下四个条件。

### 3.1 互斥条件

一个资源在同一时间只能被一个线程占用。例如，某个 `synchronized` 锁只能被一个线程持有。

### 3.2 请求并保持条件

线程已经持有至少一个资源，同时继续请求其他线程持有的资源，并且在等待期间不释放自己已经持有的资源。

### 3.3 不可剥夺条件

线程已经获得的锁不能被其他线程强制夺走，只能由持有锁的线程主动释放。

### 3.4 循环等待条件

多个线程之间形成循环等待关系。

```text
线程 A 等待线程 B 的锁
线程 B 等待线程 C 的锁
线程 C 等待线程 A 的锁
```

只要破坏其中一个条件，就可以降低或消除死锁发生的可能性。

## 4. 常见产生原因

### 4.1 锁获取顺序不一致

这是最常见的死锁原因。

```java
public void methodA() {
    synchronized (lockA) {
        synchronized (lockB) {
            // 业务处理
        }
    }
}

public void methodB() {
    synchronized (lockB) {
        synchronized (lockA) {
            // 业务处理
        }
    }
}
```

`methodA()` 按照 `lockA → lockB` 的顺序获取锁，而 `methodB()` 按照 `lockB → lockA` 的顺序获取锁，并发执行时可能形成死锁。

### 4.2 嵌套锁过多

一个线程在持有锁的情况下继续获取其他锁。嵌套层级越多，锁之间的关系越复杂，死锁风险越高。

### 4.3 锁范围过大

在锁内执行数据库访问、网络请求、文件操作或复杂计算，会延长锁持有时间，增加锁竞争和循环等待的风险。

### 4.4 不同组件之间互相调用

两个服务类分别使用自己的锁，并在持锁状态下调用对方的方法，可能形成隐藏的循环依赖。

### 4.5 错误使用 ReentrantLock

使用 `ReentrantLock` 后没有在 `finally` 中释放锁，可能导致其他线程永久等待。

```java
lock.lock();
try {
    process();
} finally {
    lock.unlock();
}
```

### 4.6 线程池之间相互等待

线程池中的任务提交新任务到同一个线程池，并同步等待新任务完成。如果线程池中的线程已经全部被占用，可能形成任务级死锁。

```java
Future<?> future = executor.submit(task);
future.get();
```

如果当前任务本身也运行在容量已满的同一个线程池中，新任务没有线程可以执行，而当前线程又一直等待它完成，就可能形成线程池饥饿死锁。

### 4.7 数据库锁与 Java 锁组合

线程持有 Java 锁后等待数据库锁，另一个线程持有相关数据库资源后等待 Java 锁，可能形成跨系统的复杂等待。

这种问题不一定会被 JVM 自动死锁检测完整识别，需要结合 Java 线程栈和数据库锁信息分析。

## 5. 死锁与其他阻塞问题的区别

### 5.1 死锁

多个线程形成明确的循环等待关系，相关线程通常无法自行恢复。

### 5.2 锁竞争

多个线程等待同一个锁，但持锁线程仍然可以继续运行并最终释放锁。锁竞争严重时会造成性能下降，但不一定是死锁。

### 5.3 数据库锁等待

Java 线程可能在等待数据库执行结果，线程栈通常显示在 JDBC Socket 读取位置。根本原因可能是数据库事务或行锁，而不是 JVM 内部锁。

### 5.4 网络阻塞

线程等待下游接口响应时，可能处于 `RUNNABLE` 或 `TIMED_WAITING` 状态，需要结合 Socket 调用栈和超时配置判断。

### 5.5 线程池耗尽

所有线程可能都在处理慢请求或等待下游服务，没有形成循环等待，但新任务无法得到执行。

因此，看到大量 `BLOCKED` 或请求超时不能直接认定为线程死锁。

## 6. 排查前的注意事项

发生疑似死锁时，不要立即重启服务。重启会清除线程之间的等待关系，使最关键的故障现场丢失。

建议优先保存：

1. 至少三次线程 Dump。
2. Java 进程 PID。
3. JVM 启动参数。
4. 服务日志。
5. 接口超时记录。
6. 线程池监控信息。
7. 数据库事务与锁信息。
8. 问题发生时间和最近变更。

连续采集线程栈：

```bash
jstack <PID> > /tmp/jstack-1.txt
sleep 5
jstack <PID> > /tmp/jstack-2.txt
sleep 5
jstack <PID> > /tmp/jstack-3.txt
```

多次采集可以判断线程是短暂阻塞，还是长期停留在相同的锁等待位置。

## 7. 第一步：定位 Java 进程

查看 Java 进程：

```bash
jps -lv
```

也可以执行：

```bash
ps -ef | grep java
```

记录目标进程的 PID，并确认它对应发生故障的应用。

查看完整启动命令：

```bash
tr '\0' ' ' < /proc/<PID>/cmdline
```

如果服务器上运行多个 Java 服务，必须先确认正确的进程，避免分析错误实例。

## 8. 第二步：查看线程整体状态

执行：

```bash
top -H -p <PID>
```

该命令可以查看 Java 进程中的所有线程。

查看线程数量：

```bash
ps -o nlwp= -p <PID>
```

也可以执行：

```bash
ls /proc/<PID>/task | wc -l
```

查看线程状态分布：

```bash
jcmd <PID> Thread.print | grep 'java.lang.Thread.State' | sort | uniq -c
```

如果 `BLOCKED` 线程数量明显增加，应进一步分析这些线程正在等待的锁以及锁的持有者。

需要注意，死锁线程通常不会持续消耗大量 CPU，因此不能只通过高 CPU 线程排查方法定位死锁。

## 9. 第三步：获取线程 Dump

### 9.1 使用 jstack

```bash
jstack -l <PID> > /tmp/jstack.txt
```

`-l` 参数可以输出更多锁相关信息。

### 9.2 使用 jcmd

```bash
jcmd <PID> Thread.print -l > /tmp/thread.txt
```

### 9.3 使用 kill -3

如果 `jstack` 和 `jcmd` 无法正常使用，可以执行：

```bash
kill -3 <PID>
```

该命令通常不会终止 Java 进程，而是让 JVM 将线程 Dump 输出到标准错误或应用日志中。

不要误用：

```bash
kill -9 <PID>
```

`kill -9` 会直接终止进程并导致现场丢失。

## 10. 第四步：查看 JVM 自动死锁检测结果

在 `jstack` 输出的末尾搜索：

```bash
grep -i -A 100 "deadlock" /tmp/jstack.txt
```

如果 JVM 检测到死锁，通常会出现：

```text
Found one Java-level deadlock:
=============================
```

以及：

```text
Java stack information for the threads listed above:
```

最后可能显示：

```text
Found 1 deadlock.
```

示例：

```text
"thread-A":
  waiting to lock monitor 0x00007f01
  which is held by "thread-B"

"thread-B":
  waiting to lock monitor 0x00007f02
  which is held by "thread-A"
```

该信息说明线程 A 和线程 B 形成了循环等待。

需要重点记录：

- 死锁线程名称。
- Java 线程 ID。
- `nid`。
- 正在等待的锁地址。
- 当前持有锁的线程。
- 业务类名。
- 方法名称。
- 代码行号。

## 11. 第五步：分析线程栈中的锁信息

典型线程栈如下：

```text
"thread-A" #20 prio=5 tid=0x00007f01 nid=0x3021 waiting for monitor entry
   java.lang.Thread.State: BLOCKED
        at com.example.DeadlockService.methodA(DeadlockService.java:35)
        - waiting to lock <0x000000076b123450>
        - locked <0x000000076b123400>
```

其中：

```text
- waiting to lock <0x000000076b123450>
```

表示线程正在等待这个对象锁。

```text
- locked <0x000000076b123400>
```

表示线程已经持有这个对象锁。

另一个线程可能显示：

```text
"thread-B" #21 prio=5 tid=0x00007f02 nid=0x3022 waiting for monitor entry
   java.lang.Thread.State: BLOCKED
        at com.example.DeadlockService.methodB(DeadlockService.java:52)
        - waiting to lock <0x000000076b123400>
        - locked <0x000000076b123450>
```

由此可以得到：

```text
线程 A 持有 123400，等待 123450
线程 B 持有 123450，等待 123400
```

这就是完整的循环等待关系。

## 12. 第六步：多次对比线程 Dump

单次线程 Dump 可能只能看到普通的瞬时锁等待，因此建议间隔数秒采集至少三次。

对比时重点检查：

- 线程是否始终处于 `BLOCKED`。
- 是否始终等待同一个锁。
- 锁持有者是否始终不变。
- 业务代码行号是否保持一致。
- 线程池队列是否持续增长。
- 请求是否长期没有完成。

如果线程在不同 Dump 中能够继续执行，说明它可能只是短暂锁竞争，而不是永久死锁。

## 13. ReentrantLock 死锁排查

`ReentrantLock` 发生死锁时，线程栈中可能出现：

```text
java.util.concurrent.locks.LockSupport.park
java.util.concurrent.locks.AbstractQueuedSynchronizer.acquire
java.util.concurrent.locks.ReentrantLock.lock
```

如果线程使用的是 `ReentrantLock`、读写锁、Semaphore 或其他 AQS 同步器，部分复杂等待关系不一定被 JVM 的经典监视器死锁检测完整识别。

此时需要结合：

- 多次线程 Dump。
- 线程当前等待的方法。
- 代码中的加锁顺序。
- 锁对象之间的关系。
- 业务日志。
- 线程池运行状态。

如果所有相关线程长期停留在 `LockSupport.park()`，应继续向上查看业务调用栈，确认具体在等待哪个并发组件。

## 14. 线程池饥饿死锁排查

线程池饥饿死锁通常表现为：

1. 线程池所有工作线程都在等待子任务结果。
2. 子任务又被提交到同一个线程池。
3. 线程池中没有空闲线程执行子任务。
4. 所有工作线程永久等待。

线程栈可能出现：

```text
java.util.concurrent.FutureTask.get
java.util.concurrent.CompletableFuture.get
java.util.concurrent.CountDownLatch.await
```

排查时应检查：

- 线程池核心线程数和最大线程数。
- 任务队列长度。
- 是否在任务内部同步等待同一线程池的新任务。
- 是否使用 `Future.get()` 且没有超时。
- 是否存在多个线程池互相等待。
- 是否在异步回调中执行阻塞操作。

处理建议包括：

- 避免任务同步等待同一线程池中的子任务。
- 为不同类型任务使用独立线程池。
- 使用异步组合代替阻塞等待。
- 为 `get()`、`await()` 设置超时。
- 合理配置线程池和有界队列。

## 15. 数据库锁等待排查

如果线程栈显示在以下位置：

```text
java.net.SocketInputStream
com.mysql.cj.jdbc
org.postgresql
PreparedStatement.execute
```

说明 Java 线程可能正在等待数据库响应。

此时应同时检查数据库：

- 当前执行 SQL。
- 长事务。
- 行锁等待。
- 表锁。
- 元数据锁。
- 死锁日志。
- 连接池状态。

MySQL 可以查看：

```sql
SHOW FULL PROCESSLIST;
```

```sql
SHOW ENGINE INNODB STATUS;
```

数据库死锁通常由数据库自动检测并回滚其中一个事务，不一定形成 JVM 内部死锁。但如果线程同时持有 Java 锁，就可能形成更复杂的跨资源等待。

## 16. 常见临时处理措施

线程死锁已经严重影响业务时，可以采取以下临时措施：

1. 先保存至少三次线程 Dump。
2. 保存相关应用和数据库日志。
3. 对故障实例停止接收新流量。
4. 将流量切换到健康实例。
5. 暂停触发问题的定时任务。
6. 对异常接口进行限流或降级。
7. 在保存现场后滚动重启故障实例。
8. 避免同时重启所有服务实例。

Java 内部的经典死锁通常无法自行恢复。如果无法通过业务方式解除等待，重启进程是恢复服务的常见临时措施，但不能代替代码修复。

## 17. 永久解决方案

### 17.1 统一锁获取顺序

所有代码按照固定顺序获取多个锁。

```java
synchronized (lockA) {
    synchronized (lockB) {
        process();
    }
}
```

其他代码也必须保持：

```text
lockA → lockB
```

不能反向获取。

### 17.2 减少嵌套锁

尽量避免在持有一个锁时继续获取其他锁。可以拆分临界区，减少锁之间的依赖关系。

### 17.3 缩小锁范围

不要在锁内执行：

- 数据库查询。
- HTTP 请求。
- 文件读写。
- 长时间计算。
- 线程等待。
- 调用未知耗时的第三方方法。

### 17.4 使用带超时的锁

使用：

```java
if (lock.tryLock(3, TimeUnit.SECONDS)) {
    try {
        process();
    } finally {
        lock.unlock();
    }
} else {
    // 超时、降级或重试
}
```

`tryLock()` 可以避免线程无限等待，但仍需要合理设计失败后的处理逻辑，防止高频重试。

### 17.5 确保锁在 finally 中释放

```java
lock.lock();
try {
    process();
} finally {
    lock.unlock();
}
```

### 17.6 减少共享状态

可以通过不可变对象、局部变量、消息队列、并发集合和无锁设计减少线程之间对共享资源的竞争。

### 17.7 为等待操作设置超时

以下操作尽量设置合理超时：

- `Future.get()`。
- `CountDownLatch.await()`。
- 网络请求。
- 数据库访问。
- 分布式锁。
- 线程池任务等待。

超时不能直接解决锁顺序错误，但可以避免部分资源等待无限持续。

## 18. 不建议直接执行的操作

### 18.1 不建议立即重启

重启前未保存线程 Dump，会导致死锁关系和代码现场丢失。

### 18.2 不建议盲目增加线程池大小

如果线程池中存在循环等待，增加线程数只能暂时延缓线程耗尽，无法修复死锁。

### 18.3 不建议使用 kill -9 作为首选方案

`kill -9` 会立即终止进程，无法执行正常关闭逻辑，也可能影响正在处理的任务和数据一致性。

### 18.4 不建议把所有 BLOCKED 都视为死锁

`BLOCKED` 可能只是短暂的锁竞争。必须结合持锁关系、多次线程 Dump 和 JVM 死锁检测结果判断。

### 18.5 不建议随意删除同步控制

直接移除锁可能引入数据竞争、脏数据和并发安全问题。应重新设计锁顺序和临界区，而不是简单取消同步。

## 19. 监控与预防建议

建议监控以下指标：

- Java 线程总数。
- 各线程状态数量。
- `BLOCKED` 线程数量。
- Web 容器活跃线程数。
- 线程池队列长度。
- 线程池任务等待时间。
- 接口超时率。
- 请求最大响应时间。
- 数据库连接池活跃连接数。
- 数据库锁等待时间。
- 定时任务执行时长。
- JVM 死锁检测结果。
- 服务健康检查状态。

可以通过 JMX 的 `ThreadMXBean` 定期检测死锁：

```java
ThreadMXBean threadMXBean = ManagementFactory.getThreadMXBean();
long[] threadIds = threadMXBean.findDeadlockedThreads();

if (threadIds != null && threadIds.length > 0) {
    // 记录告警和线程信息
}
```

还可以使用：

```java
findMonitorDeadlockedThreads()
```

`findDeadlockedThreads()` 能够覆盖更多基于 `java.util.concurrent` 的同步器，通常更适合现代 Java 应用。

## 20. 推荐排查流程

JVM 线程死锁可以按照以下顺序排查：

1. 确认 Java 进程仍然存在。
2. 检查接口超时、线程池和业务日志。
3. 使用 `jstack -l` 或 `jcmd Thread.print -l` 获取线程 Dump。
4. 间隔数秒连续采集至少三次。
5. 搜索 `Found one Java-level deadlock`。
6. 记录死锁线程名称、锁地址和代码行号。
7. 分析线程分别持有什么锁、等待什么锁。
8. 画出线程与锁之间的循环等待关系。
9. 检查代码中的加锁顺序。
10. 如果没有自动检测结果，检查 AQS、线程池和数据库等待。
11. 检查是否存在 `Future.get()`、`await()` 等无限等待。
12. 检查数据库事务和锁等待。
13. 保存故障现场后滚动重启异常实例。
14. 统一锁顺序、缩小锁范围并增加超时机制。
15. 通过并发测试验证死锁不再出现。
16. 增加线程状态和死锁自动监控。

## 21. 常用排查命令汇总

```bash
# 查看 Java 进程
jps -lv
ps -ef | grep java

# 查看完整启动命令
tr '\0' ' ' < /proc/<PID>/cmdline

# 查看 Java 线程
top -H -p <PID>

# 查看线程数量
ps -o nlwp= -p <PID>
ls /proc/<PID>/task | wc -l

# 使用 jstack 获取线程 Dump
jstack -l <PID> > /tmp/jstack.txt

# 使用 jcmd 获取线程 Dump
jcmd <PID> Thread.print -l > /tmp/thread.txt

# 连续获取线程 Dump
jstack -l <PID> > /tmp/jstack-1.txt
sleep 5
jstack -l <PID> > /tmp/jstack-2.txt
sleep 5
jstack -l <PID> > /tmp/jstack-3.txt

# 搜索死锁
grep -i -A 100 "deadlock" /tmp/jstack.txt

# 统计线程状态
jcmd <PID> Thread.print | grep 'java.lang.Thread.State' | sort | uniq -c

# 查看 BLOCKED 线程
grep -B 2 -A 20 'java.lang.Thread.State: BLOCKED' /tmp/jstack.txt

# 通过信号获取线程 Dump
kill -3 <PID>

# 查看 JVM 参数
jcmd <PID> VM.flags

# 查看服务日志
journalctl -u <服务名称> -n 200

# 查看应用最近日志
tail -n 200 /path/to/application.log
```

## 22. 排查结论模板

### 故障现象

订单接口大量超时，Java 进程仍然运行且 CPU 使用率较低，Web 线程池活跃线程数达到上限，任务队列持续增长。

### 故障确认

连续采集三次线程 Dump 后，均在文件末尾发现 `Found one Java-level deadlock`。线程 A 持有订单锁并等待库存锁，线程 B 持有库存锁并等待订单锁，形成循环等待。

### 根本原因

订单处理方法按照“订单锁 → 库存锁”的顺序获取锁，而库存回滚方法按照“库存锁 → 订单锁”的顺序获取锁。两个方法并发执行时产生锁顺序反转，最终导致线程死锁。

### 临时处理

保存线程 Dump 和应用日志后，将故障实例从负载均衡中摘除并重启，其他健康实例继续提供服务。

### 永久修复

统一所有相关代码的锁获取顺序，固定按照“订单锁 → 库存锁”获取锁；缩小同步代码块范围，避免在锁内调用数据库和远程接口；增加并发测试和 JVM 死锁监控。

### 验证结果

修复后通过高并发测试重复执行订单创建和库存回滚操作，未再检测到死锁，线程池队列和接口响应时间保持正常。

## 23. 总结

JVM 线程死锁排查的关键是获取并分析线程 Dump。`jstack -l` 和 `jcmd Thread.print -l` 可以显示线程状态、锁地址、锁持有者和等待关系。JVM 检测到经典 Java 死锁时，通常会在线程 Dump 末尾直接输出 `Found one Java-level deadlock`。

如果 JVM 没有自动报告死锁，也不能完全排除问题。基于 AQS 的锁、线程池之间的相互等待、数据库锁和网络资源等待，可能需要结合多次线程 Dump、线程池监控和数据库状态分析。

锁获取顺序不一致是最常见的死锁原因。永久修复应统一加锁顺序、减少嵌套锁、缩小锁范围，并避免在锁内执行数据库访问、远程调用和其他耗时操作。对于 `ReentrantLock`，应在 `finally` 中释放锁，并根据业务情况使用带超时的 `tryLock()`。

重启服务可以临时解除死锁，但会清除故障现场。生产环境应先保存线程 Dump 和日志，再进行流量切换或滚动重启，并通过并发测试和自动死锁监控防止问题再次发生。