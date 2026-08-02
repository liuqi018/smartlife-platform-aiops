# Java 进程 CPU 过高排查

## 1. 问题概述

Java 进程 CPU 过高是指 Java 应用在一段时间内持续占用较多 CPU 资源，导致服务器负载升高、接口响应变慢、任务处理延迟，严重时可能造成服务不可用。

Java 进程 CPU 使用率升高不一定代表应用出现故障。在业务高峰期、批量计算、数据导入、垃圾回收或系统启动预热过程中，CPU 短时间升高可能是正常现象。真正需要重点关注的是 CPU 长时间处于高位，并伴随接口超时、线程堆积、Full GC 频繁或系统负载持续升高。

Java 进程 CPU 过高排查的核心思路是：

1. 确认服务器整体 CPU 是否确实存在压力。
2. 确认高 CPU 是否由 Java 进程引起。
3. 定位 Java 进程中占用 CPU 较高的线程。
4. 将操作系统线程 ID 与 JVM 线程栈对应。
5. 根据线程栈定位具体代码、GC 或运行时问题。
6. 结合业务日志、监控和代码逻辑确认根本原因。
7. 采取限流、降级、修复代码或调整 JVM 参数等措施。

## 2. 常见问题现象

Java 进程 CPU 过高时，通常会出现以下现象：

1. `top` 中 Java 进程 CPU 使用率持续较高。
2. 服务器 Load Average 持续升高。
3. 应用接口响应时间明显增加。
4. 大量请求出现超时。
5. 定时任务或消息消费速度下降。
6. 应用线程池任务持续堆积。
7. Full GC 或 Young GC 频繁执行。
8. Java 进程频繁创建和销毁线程。
9. 服务器上其他服务响应受到影响。
10. 应用日志中出现大量重复错误。
11. 下游服务超时后触发大量重试。
12. Java 服务启动后 CPU 长时间不下降。
13. CPU 使用率偶发性达到 100% 或更高。
14. 容器 CPU 使用率达到 Limit，并发生 CPU Throttling。
15. 线程 Dump 中同一线程长期停留在相同代码位置。

在 Linux 中，多核服务器上的进程 CPU 使用率可能超过 100%。例如，一台服务器有 8 个逻辑 CPU，一个 Java 进程最多可能显示接近 800% CPU。因此，需要结合 CPU 核心数理解指标含义。

## 3. Java 进程 CPU 过高的常见原因

### 3.1 代码中存在死循环

程序中的循环退出条件错误，可能导致线程持续执行计算，占满一个或多个 CPU 核心。

例如：

```java
while (true) {
    // 没有阻塞、休眠或退出条件
}
```

常见场景包括：

- 循环条件始终成立。
- 状态变量没有正确更新。
- 异常被捕获后立即重新执行。
- 任务失败后没有退避时间。
- 消费队列为空时仍然持续轮询。
- 自旋锁长时间无法获取锁。

### 3.2 算法复杂度过高

业务代码可能存在高复杂度算法，在数据量增加后消耗大量 CPU。

常见问题包括：

- 多层嵌套循环。
- 对大集合反复遍历。
- 在循环中执行排序。
- 使用低效的字符串拼接。
- 对相同数据重复计算。
- 正则表达式发生灾难性回溯。
- 大量 JSON 序列化和反序列化。
- 大对象深度复制。
- 图片、音视频或加密计算。

### 3.3 垃圾回收过于频繁

应用不断创建大量临时对象，或者堆内存配置不合理，可能导致 Young GC、Mixed GC 或 Full GC 频繁发生。

GC 线程本身会消耗 CPU。频繁 GC 还可能使业务线程停顿，导致吞吐量下降。

常见原因包括：

- 对象创建速度过快。
- 堆内存设置过小。
- 老年代占用过高。
- 内存泄漏导致可回收空间不足。
- 大对象分配频繁。
- 元空间不足。
- 显式调用 `System.gc()`。
- GC 算法或参数不适合当前业务。

### 3.4 线程数量过多

线程过多会增加上下文切换和调度开销，导致 CPU 消耗升高。

常见原因包括：

- 线程池参数设置过大。
- 使用无界方式创建线程。
- 每个请求都创建新线程。
- 线程泄漏。
- 定时任务重复创建线程池。
- 异步任务持续堆积。
- 大量线程处于可运行状态。

### 3.5 锁竞争和自旋

多个线程竞争同一个锁时，可能产生大量上下文切换。CAS、自旋锁或 `synchronized` 在高竞争环境中也可能消耗大量 CPU。

常见场景包括：

- 热点共享变量竞争。
- 锁粒度过大。
- 长时间持有锁。
- 高并发下频繁 CAS 失败。
- 自旋等待没有合理退出条件。
- 错误使用并发集合。
- 日志或缓存操作使用全局锁。

### 3.6 频繁异常

异常创建、堆栈填充和日志输出都会消耗 CPU。如果异常在循环中被频繁抛出并捕获，可能造成明显的性能问题。

例如：

```java
while (true) {
    try {
        process();
    } catch (Exception e) {
        logger.error("处理失败", e);
    }
}
```

如果没有休眠、限流或退出机制，异常可能形成高速循环。

### 3.7 正则表达式回溯

复杂或不合理的正则表达式可能在特定输入下发生灾难性回溯，使单个线程长时间占用 CPU。

常见高风险写法包括嵌套量词和模糊匹配，例如：

```regex
(a+)+
```

如果高 CPU 线程栈长期停留在 `java.util.regex` 相关方法，应重点检查正则表达式。

### 3.8 序列化和反序列化压力

大量 JSON、XML、Protobuf 或 Java 对象转换会占用较多 CPU。

常见场景包括：

- 一次序列化超大对象。
- 大量重复字段。
- 反射调用过多。
- 重复进行对象转换。
- 日志中序列化完整请求和响应。
- 消息消费速度突然升高。

### 3.9 加密、压缩和哈希计算

以下操作属于 CPU 密集型任务：

- AES、RSA 等加密解密。
- TLS 握手。
- Gzip、Zip 压缩和解压。
- MD5、SHA 等哈希计算。
- 密码哈希。
- 图片处理。
- PDF 解析。
- OCR 和音视频处理。

如果这些任务并发度过高，可能快速耗尽 CPU。

### 3.10 业务流量突然增加

Java 程序本身可能没有明显缺陷，但请求量、消息量或任务量突然增加，也会导致 CPU 使用率升高。

此时应结合 QPS、消息积压、线程池状态和接口响应时间判断系统是否达到容量上限。

### 3.11 下游服务异常引发重试风暴

数据库、Redis、HTTP 接口或消息队列出现异常后，如果应用没有合理设置重试间隔和最大次数，可能发生大量快速重试，消耗 CPU 并进一步放大故障。

### 3.12 JIT 编译或类加载

应用启动、流量预热或动态生成大量类时，JIT 编译线程和类加载过程可能占用一定 CPU。

短时间升高可能属于正常现象。如果长期不下降，则需要检查动态代理、脚本引擎、字节码生成和类加载问题。

## 4. 排查前的注意事项

生产环境排查 Java CPU 过高问题时，应注意：

1. 不要立即重启 Java 服务。
2. 重启前应尽量保存线程栈、GC 和进程信息。
3. 不要只采集一次线程 Dump。
4. 不要只根据某一时刻的 CPU 使用率判断。
5. 不要在系统严重过载时反复执行高开销命令。
6. 生成 Heap Dump 前应评估内存、磁盘和停顿风险。
7. 不要直接终止不清楚用途的高 CPU 线程。
8. 应记录故障发生时间、业务流量和发布变更。
9. 应确认 CPU 高是进程问题、容器限制还是宿主机争抢。
10. 优先使用低开销工具保存故障现场。

建议先执行：

```bash
date
uptime
top -b -n 1 | head -50
vmstat 1 10
ps -eo pid,ppid,user,%cpu,%mem,etime,cmd --sort=-%cpu | head -20
```

## 5. 第一步：确认系统整体 CPU 使用情况

执行：

```bash
top
```

重点关注：

```text
%us
%sy
%ni
%id
%wa
%st
```

各指标含义如下：

- `%us`：用户态 CPU 使用率。
- `%sy`：内核态 CPU 使用率。
- `%ni`：调整过优先级的进程使用率。
- `%id`：CPU 空闲率。
- `%wa`：CPU 等待 IO 的时间比例。
- `%st`：虚拟机被宿主机抢占的 CPU 时间。

如果 `%us` 较高，通常说明用户程序正在进行大量计算。

如果 `%sy` 较高，可能存在大量系统调用、网络处理、上下文切换或内核活动。

如果 `%wa` 较高，问题可能主要是磁盘 IO，而不是 Java 计算。

如果 `%st` 较高，可能是虚拟化宿主机 CPU 资源争抢。

查看 CPU 核心数：

```bash
nproc
```

也可以执行：

```bash
lscpu
```

## 6. 第二步：确认高 CPU Java 进程

使用 `ps` 按照 CPU 使用率排序：

```bash
ps -eo pid,ppid,user,%cpu,%mem,etime,cmd --sort=-%cpu | head -20
```

查找 Java 进程：

```bash
ps -ef | grep java
```

使用 JDK 工具查看 Java 进程：

```bash
jps -lv
```

重点记录：

- Java 进程 PID。
- 启动时间。
- CPU 使用率。
- 内存使用率。
- 启动命令。
- JVM 参数。
- 应用名称。
- 运行用户。

查看完整启动命令：

```bash
tr '\0' ' ' < /proc/<PID>/cmdline
```

如果服务器运行多个 Java 进程，需要先确认具体是哪一个服务出现问题，避免分析错误进程。

## 7. 第三步：持续观察 CPU 变化

一次 CPU 快照无法判断问题是否持续存在，可以执行：

```bash
pidstat -u -p <PID> 1 10
```

重点关注：

- `%usr`：进程用户态 CPU 使用率。
- `%system`：进程内核态 CPU 使用率。
- `%CPU`：进程总 CPU 使用率。
- `CPU`：进程当前运行的 CPU 核心。

如果 `%usr` 高，通常说明 Java 代码、GC、序列化或算法计算消耗较大。

如果 `%system` 高，可能与系统调用、网络、文件 IO、线程调度或本地代码有关。

还可以使用：

```bash
top -p <PID>
```

持续观察 Java 进程 CPU 是否在业务高峰结束后回落。

## 8. 第四步：定位高 CPU 线程

执行：

```bash
top -H -p <PID>
```

其中：

- `-H` 表示显示线程。
- `-p` 表示只查看指定进程。

进入 `top` 后，可以按大写字母 `P` 按 CPU 使用率排序。

记录占用 CPU 较高线程的线程 ID。例如：

```text
PID    USER   %CPU  COMMAND
12368  app    99.5  java
```

这里显示的 `12368` 是 Linux 线程 ID，也叫 LWP ID。

还可以使用：

```bash
ps -Lp <PID> -o pid,tid,psr,pcpu,stat,comm --sort=-pcpu | head -20
```

字段含义如下：

- `PID`：进程 ID。
- `TID`：线程 ID。
- `PSR`：线程运行所在 CPU。
- `PCPU`：线程 CPU 使用率。
- `STAT`：线程状态。
- `COMMAND`：线程名称。

## 9. 第五步：将线程 ID 转换为十六进制

JVM 线程栈中的 `nid` 通常使用十六进制表示，而 Linux 工具显示的线程 ID 通常是十进制，因此需要转换。

执行：

```bash
printf '%x\n' <线程ID>
```

例如：

```bash
printf '%x\n' 12368
```

输出：

```text
3050
```

那么在线程 Dump 中需要查找：

```text
nid=0x3050
```

也可以执行：

```bash
echo "obase=16; 12368" | bc
```

注意十六进制字母大小写不影响匹配含义。

## 10. 第六步：获取 Java 线程栈

### 10.1 使用 jstack

执行：

```bash
jstack <PID> > /tmp/jstack-1.txt
```

间隔几秒连续采集：

```bash
jstack <PID> > /tmp/jstack-1.txt
sleep 5
jstack <PID> > /tmp/jstack-2.txt
sleep 5
jstack <PID> > /tmp/jstack-3.txt
```

连续采集多次的目的是判断高 CPU 线程是否长期停留在相同代码位置。

### 10.2 使用 jcmd

```bash
jcmd <PID> Thread.print > /tmp/thread-1.txt
```

检查锁信息：

```bash
jcmd <PID> Thread.print -l > /tmp/thread-lock.txt
```

### 10.3 使用 kill -3

如果 `jstack` 或 `jcmd` 无法使用，可以执行：

```bash
kill -3 <PID>
```

`SIGQUIT` 通常不会终止 Java 进程，而是让 JVM 将线程栈输出到标准错误或应用日志中。

需要先确认服务日志位置，避免误认为命令没有生效。

## 11. 第七步：在线程栈中定位高 CPU 线程

根据转换后的十六进制线程 ID 查找：

```bash
grep -i -A 40 "nid=0x3050" /tmp/jstack-1.txt
```

示例线程栈：

```text
"pool-1-thread-3" #35 prio=5 os_prio=0 cpu=125000.00ms elapsed=130.00s tid=0x00007f... nid=0x3050 runnable
   java.lang.Thread.State: RUNNABLE
        at com.example.service.CalculateService.calculate(CalculateService.java:128)
        at com.example.task.CalculateTask.run(CalculateTask.java:56)
```

重点关注：

- 线程名称。
- `nid`。
- 线程状态。
- CPU 累计时间。
- 栈顶方法。
- 业务类名和代码行号。
- 是否在多次线程 Dump 中保持不变。
- 是否为 GC、编译器或业务线程。

如果同一高 CPU 线程在多次线程 Dump 中始终停留在相同方法，说明该方法很可能存在死循环、超大计算或阻塞异常。

## 12. Java 线程状态说明

### 12.1 RUNNABLE

```text
java.lang.Thread.State: RUNNABLE
```

表示线程正在运行，或者等待操作系统分配 CPU，也可能正在执行本地 IO。

高 CPU 线程通常处于 `RUNNABLE` 状态，但并不是所有 `RUNNABLE` 线程都在持续消耗 CPU。

### 12.2 BLOCKED

```text
java.lang.Thread.State: BLOCKED
```

表示线程正在等待进入 `synchronized` 临界区。

大量 `BLOCKED` 线程通常说明存在锁竞争，但等待锁的线程本身一般不会持续占用大量 CPU。

### 12.3 WAITING

```text
java.lang.Thread.State: WAITING
```

表示线程正在无限期等待，例如：

- `Object.wait()`。
- `Thread.join()`。
- `LockSupport.park()`。

### 12.4 TIMED_WAITING

```text
java.lang.Thread.State: TIMED_WAITING
```

表示线程正在限时等待，例如：

- `Thread.sleep()`。
- 带超时的 `Object.wait()`。
- 带超时的 `LockSupport.parkNanos()`。

### 12.5 高 CPU 判断重点

排查高 CPU 时，应优先关注：

- CPU 使用率高的操作系统线程。
- 对应 JVM 栈为 `RUNNABLE` 的线程。
- 多次采样中栈位置基本不变的线程。
- 业务代码中循环、正则、序列化、压缩和加密相关方法。
- GC、JIT 或运行时内部线程。

## 13. 判断是否为死循环

高 CPU 线程栈如果多次停留在同一个业务方法，应检查代码中是否存在：

- `while (true)`。
- 无法退出的 `for` 循环。
- 循环变量没有更新。
- 错误的递归。
- 失败后立即重试。
- 空队列持续轮询。
- 状态判断存在并发可见性问题。
- 自旋等待条件永远无法满足。
- 异常捕获后继续高速执行。

错误示例：

```java
while (!taskCompleted) {
    checkTask();
}
```

如果 `taskCompleted` 缺少正确的并发可见性控制，线程可能无法及时看到其他线程的更新。

改进时可以根据实际场景使用：

- 正确的退出条件。
- `volatile`。
- 锁或并发工具。
- 阻塞队列。
- 定时等待。
- 最大重试次数。
- 指数退避。
- 线程中断机制。

不能简单通过增加 `Thread.sleep()` 掩盖错误逻辑，应先确认循环存在的业务意义。

## 14. 判断是否为频繁异常

如果线程栈经常出现以下方法：

```text
java.lang.Throwable.fillInStackTrace
java.lang.Exception.<init>
```

可能说明程序正在频繁创建异常。

进一步检查应用日志：

```bash
grep -iE "exception|error" /path/to/application.log | tail -200
```

统计重复错误：

```bash
grep -i "Exception" /path/to/application.log | sort | uniq -c | sort -nr | head
```

常见根本原因包括：

- 使用异常控制正常业务流程。
- 下游调用失败后无限重试。
- 数据格式错误反复触发解析异常。
- 空指针异常在循环中持续发生。
- 连接失败后没有退避。
- 日志打印完整异常栈过于频繁。

优化建议包括：

- 不使用异常控制正常流程。
- 修复根本业务错误。
- 设置最大重试次数。
- 使用指数退避。
- 对重复异常进行限频和聚合。
- 避免重复创建相同异常。

## 15. 判断是否为正则表达式问题

如果高 CPU 线程栈包含：

```text
java.util.regex.Pattern
java.util.regex.Matcher
```

应重点检查正则表达式是否存在灾难性回溯。

常见风险包括：

- 嵌套量词。
- 模糊匹配范围过大。
- 对超长文本执行复杂正则。
- 正则表达式缺少边界。
- 在循环中重复编译正则。

错误示例：

```java
String.matches("^(a+)+$")
```

优化方向包括：

- 简化正则表达式。
- 避免嵌套量词。
- 增加明确边界。
- 限制输入长度。
- 预编译 `Pattern`。
- 使用普通字符串查找代替复杂正则。
- 对外部输入增加超时或隔离机制。

## 16. 判断是否为序列化问题

如果线程栈包含：

```text
com.fasterxml.jackson
com.google.gson
fastjson
ObjectMapper
JSON.toJSONString
```

可能存在大量 JSON 序列化或反序列化。

常见原因包括：

- 返回对象层级过深。
- 序列化超大集合。
- 对象存在循环引用。
- 日志打印完整请求对象。
- 同一对象被重复转换。
- 大量反射调用。
- 缓存中重复进行 JSON 转换。

优化建议包括：

- 精简返回字段。
- 使用分页。
- 避免序列化不必要的对象。
- 复用线程安全的序列化工具。
- 避免在高频日志中输出完整对象。
- 对重复结果进行缓存。
- 使用更适合的二进制协议。

## 17. 判断是否为加密、压缩或文件处理

如果线程栈中出现以下内容，应结合业务确认：

```text
java.util.zip
java.security
javax.crypto
MessageDigest
Cipher
Deflater
Inflater
ImageIO
```

常见处理方式包括：

- 降低任务并发度。
- 将 CPU 密集型任务放入独立线程池。
- 对批量任务限流。
- 避免在请求线程中执行大规模压缩。
- 使用缓存避免重复计算。
- 优化输入数据大小。
- 将重型任务拆分为异步任务。
- 必要时使用专用计算节点。

## 18. 判断是否为锁竞争或自旋

线程栈出现大量 `BLOCKED` 时，应检查锁竞争：

```bash
jcmd <PID> Thread.print -l
```

如果高 CPU 线程停留在 CAS 或自旋相关代码，可能出现高竞争自旋。

常见位置包括：

```text
Unsafe.compareAndSwap
VarHandle.compareAndSet
AtomicInteger
AtomicLong
ConcurrentHashMap
Thread.onSpinWait
```

锁竞争可能带来：

- 大量上下文切换。
- CAS 反复失败。
- 线程调度开销。
- 吞吐量下降。
- CPU 使用率升高。

优化建议包括：

- 缩小锁粒度。
- 缩短锁持有时间。
- 避免在锁内执行 IO。
- 减少共享状态。
- 使用分段设计。
- 使用合适的并发集合。
- 控制线程数量。
- 对热点计数使用 `LongAdder` 等结构。
- 避免无上限自旋。

## 19. 检查线程数量和上下文切换

查看 Java 进程线程数量：

```bash
ps -o nlwp= -p <PID>
```

也可以执行：

```bash
ls /proc/<PID>/task | wc -l
```

查看线程统计：

```bash
pidstat -w -p <PID> 1 10
```

字段包括：

- `cswch/s`：主动上下文切换次数。
- `nvcswch/s`：非主动上下文切换次数。

如果线程数量和上下文切换次数都很高，应重点检查：

- 线程池是否设置过大。
- 是否频繁创建线程。
- 是否存在大量短任务。
- 是否有严重的锁竞争。
- 是否存在大量线程争抢少量 CPU。
- 是否重复创建线程池。
- 是否存在任务拒绝后立即重试。

查看线程名称分布：

```bash
jcmd <PID> Thread.print | grep '^"' | sort | uniq -c | sort -nr | head -30
```

## 20. 检查线程池状态

Java 应用中常见的线程池问题包括：

- 核心线程数过大。
- 最大线程数过大。
- 使用无界队列导致任务堆积。
- 使用过小队列导致频繁拒绝。
- 拒绝策略中立即重新提交任务。
- CPU 密集型和 IO 密集型任务混用同一线程池。
- 定时任务执行时间超过调度周期。
- 线程池被重复创建。

建议监控以下线程池指标：

- 活跃线程数。
- 当前线程数。
- 核心线程数。
- 最大线程数。
- 队列长度。
- 已完成任务数。
- 拒绝任务数。
- 任务执行时间。
- 任务等待时间。

CPU 密集型任务的线程数不宜远高于 CPU 核心数。具体配置应通过压力测试确定。

## 21. 检查是否为 GC 导致 CPU 过高

### 21.1 查看 GC 统计

执行：

```bash
jstat -gcutil <PID> 1000 10
```

重点关注：

- `YGC`：Young GC 次数。
- `YGCT`：Young GC 总耗时。
- `FGC`：Full GC 次数。
- `FGCT`：Full GC 总耗时。
- `GCT`：GC 总耗时。
- 老年代使用率。
- 元空间使用率。

如果短时间内 `YGC` 或 `FGC` 快速增长，说明 GC 可能是 CPU 过高的重要原因。

### 21.2 查看堆信息

```bash
jcmd <PID> GC.heap_info
```

### 21.3 查看 GC 原因

```bash
jstat -gccause <PID> 1000 10
```

### 21.4 查看 GC 日志

JDK 9 及以上可以配置：

```bash
-Xlog:gc*:file=/path/to/gc.log:time,uptime,level,tags
```

JDK 8 常见配置如下：

```bash
-XX:+PrintGCDetails
-XX:+PrintGCDateStamps
-Xloggc:/path/to/gc.log
```

应结合 GC 日志判断：

- GC 是否过于频繁。
- 每次 GC 是否回收了足够内存。
- Full GC 后老年代是否仍然很高。
- 是否存在大对象分配。
- 是否发生元空间不足。
- 是否存在晋升失败。
- GC 暂停时间是否符合预期。

## 22. 判断高 CPU 线程是否为 GC 线程

线程名称中可能出现：

```text
GC Thread
G1 Main Marker
G1 Conc
G1 Refine
G1 Young RemSet Sampling
VM Thread
```

如果高 CPU 主要来自 GC 线程，应将排查重点转向：

- 对象分配速率。
- 堆内存大小。
- 老年代占用。
- 内存泄漏。
- GC 算法和参数。
- 大对象和直接内存。
- 业务流量变化。

不能仅通过增加堆内存解决所有 GC 问题。堆过大可能增加 Full GC 的最大停顿时间，应结合对象存活情况和业务要求调整。

## 23. 检查对象分配速率

可以使用 Java Flight Recorder、JDK Mission Control、Arthas 或 async-profiler 分析对象分配热点。

常见对象分配热点包括：

- 字符串拼接。
- JSON 转换。
- 日期格式化。
- 正则匹配。
- 集合临时对象。
- Stream 中间对象。
- 日志参数构造。
- 大量装箱和拆箱。
- 重复创建客户端对象。
- 每个请求创建大数组。

减少无效对象创建可以降低 GC 频率和 CPU 消耗，但不应为了避免所有对象创建而牺牲代码正确性和可维护性。

## 24. 使用 Arthas 排查

如果生产环境允许使用 Arthas，可以附加到目标 Java 进程进行分析。

启动后可以使用：

```text
dashboard
```

查看线程、内存、GC 和运行状态。

查看最忙的线程：

```text
thread -n 10
```

查看指定线程：

```text
thread <线程ID>
```

查看阻塞线程：

```text
thread -b
```

分析方法调用耗时：

```text
trace com.example.service.ExampleService exampleMethod
```

查看方法调用统计：

```text
monitor -c 5 com.example.service.ExampleService exampleMethod
```

观察方法参数和返回值：

```text
watch com.example.service.ExampleService exampleMethod '{params,returnObj,throwExp}' -x 2
```

使用 Arthas 时应注意：

- `trace`、`watch` 等命令可能增加运行开销。
- 不要匹配范围过大的类和方法。
- 不要在高流量方法上输出大量参数。
- 应设置合理采样范围和次数。
- 避免泄露密码、Token 和个人信息。

## 25. 使用 Java Flight Recorder 排查

对于支持 JFR 的 JDK，可以使用：

```bash
jcmd <PID> JFR.start name=cpu settings=profile duration=60s filename=/tmp/cpu.jfr
```

完成后可以使用 JDK Mission Control 分析：

- CPU 热点方法。
- 线程运行情况。
- 锁竞争。
- 对象分配。
- GC 活动。
- 文件和网络 IO。
- 异常事件。

JFR 通常适合进行一段时间的低开销运行时分析，但仍应根据生产环境负载评估采集时长和配置。

## 26. 使用 async-profiler 排查

async-profiler 可以生成 CPU 火焰图，帮助定位 CPU 时间主要消耗在哪些方法中。

常见采样方式如下：

```bash
./asprof -d 30 -e cpu -f /tmp/cpu.html <PID>
```

参数含义：

- `-d 30`：采样 30 秒。
- `-e cpu`：采集 CPU 事件。
- `-f`：指定输出文件。
- `<PID>`：Java 进程 PID。

火焰图中：

- 横向宽度代表采样占比。
- 纵向表示调用栈深度。
- 较宽的方法通常是主要 CPU 热点。
- 应重点分析业务代码、序列化、正则、GC 和加密压缩方法。

使用性能分析工具前，应确认其与当前 JDK、操作系统及权限环境兼容。

## 27. 检查 JIT 编译线程

高 CPU 线程名称可能为：

```text
C1 CompilerThread
C2 CompilerThread
```

应用启动或流量预热阶段出现 JIT 编译通常是正常现象。

如果编译线程长期占用较高 CPU，应检查：

- 是否频繁加载和卸载类。
- 是否大量使用动态代理。
- 是否动态生成字节码。
- 是否存在代码缓存压力。
- 是否存在方法反复编译和去优化。
- 应用是否一直无法完成预热。

查看 Code Cache：

```bash
jcmd <PID> Compiler.codecache
```

查看编译队列：

```bash
jcmd <PID> Compiler.queue
```

## 28. 检查应用流量和业务任务

需要将 CPU 使用率与以下指标进行时间对比：

- 接口 QPS。
- 消息消费量。
- 定时任务执行时间。
- 批处理任务数量。
- 数据导入量。
- 用户请求数量。
- 下游失败率。
- 重试次数。
- 日志输出量。
- 发布和预热时间。

如果 CPU 与业务流量同步增长，说明应用可能已经达到容量上限，需要评估：

- 单请求 CPU 成本。
- 是否存在低效热点代码。
- 是否需要水平扩容。
- 是否需要缓存。
- 是否可以异步处理。
- 是否需要对高成本接口限流。

如果业务流量没有增加，但 CPU 突然升高，应优先怀疑代码异常、GC、重试、死循环或环境变化。

## 29. 检查定时任务

查看应用和系统定时任务是否在 CPU 升高时运行。

系统 Cron：

```bash
crontab -l
```

```bash
systemctl list-timers --all
```

Java 应用中常见定时方式包括：

- `@Scheduled`。
- Quartz。
- XXL-JOB。
- ElasticJob。
- Timer。
- ScheduledExecutorService。

常见问题包括：

- 定时任务执行周期过短。
- 上一次任务未结束，下一次又启动。
- 多实例重复执行同一任务。
- 任务处理数据量突然增加。
- 失败任务立即重试。
- 定时任务与业务高峰重叠。

## 30. 检查容器 CPU 限制

如果 Java 应用运行在 Docker 中，可以执行：

```bash
docker stats
```

查看容器 CPU 配置：

```bash
docker inspect <容器名称或ID> --format '{{.HostConfig.NanoCpus}}'
```

在 Kubernetes 中执行：

```bash
kubectl top pod -n <namespace>
```

查看资源限制：

```bash
kubectl get pod <pod-name> -n <namespace> -o yaml
```

重点检查：

```yaml
resources:
  requests:
    cpu: "500m"
  limits:
    cpu: "1"
```

容器达到 CPU Limit 后，Linux Cgroup 会限制容器 CPU 使用，产生 CPU Throttling。此时应用可能表现为响应变慢，但宿主机整体 CPU 并不一定很高。

在 Cgroup v2 环境中，可以查看：

```bash
cat /sys/fs/cgroup/cpu.stat
```

重点关注：

```text
nr_throttled
throttled_usec
```

如果限流次数和限流时间持续增长，应检查：

- CPU Limit 是否过小。
- 应用是否存在计算热点。
- 是否需要扩容 Pod。
- 是否应调整请求和限制。
- JVM 是否正确识别容器 CPU 数量。

## 31. 常见处理措施

### 31.1 临时止损措施

Java 进程 CPU 已严重影响业务时，可以根据实际情况采取：

1. 对高成本接口临时限流。
2. 暂停非核心定时任务。
3. 降低批处理任务并发度。
4. 暂停异常消息消费。
5. 关闭或降级非核心功能。
6. 将部分流量切换到其他实例。
7. 增加应用实例。
8. 阻止无限重试。
9. 调整容器 CPU 资源。
10. 在保存线程栈后重启异常实例。

重启只能作为恢复业务的临时措施，不能替代根本原因分析。

### 31.2 修复死循环和错误重试

处理建议：

- 修复循环退出条件。
- 增加最大重试次数。
- 使用指数退避。
- 增加线程中断处理。
- 使用阻塞队列代替空轮询。
- 确保共享状态具有正确的可见性。
- 为任务增加超时和熔断。

### 31.3 优化热点代码

处理建议：

- 降低算法复杂度。
- 减少重复计算。
- 优化集合遍历。
- 避免在循环中执行高成本操作。
- 优化正则表达式。
- 减少不必要的序列化。
- 对稳定结果增加缓存。
- 将大型任务拆分处理。
- 使用性能分析工具验证优化效果。

### 31.4 优化线程池

处理建议：

- 根据任务类型设置线程数量。
- CPU 密集型和 IO 密集型任务使用不同线程池。
- 设置有界任务队列。
- 使用合理的拒绝策略。
- 避免拒绝后立即重新提交。
- 监控活跃线程、队列和拒绝数量。
- 避免重复创建线程池。

### 31.5 优化 GC

处理建议：

- 根据实际内存需求调整堆大小。
- 分析并减少对象分配。
- 修复内存泄漏。
- 减少大对象创建。
- 选择适合业务延迟要求的垃圾收集器。
- 避免无必要的 `System.gc()`。
- 根据 GC 日志调整参数。
- 使用压力测试验证参数效果。

### 31.6 水平扩容

如果 CPU 升高来自正常业务增长，并且单实例已经达到合理容量，可以：

- 增加应用实例。
- 使用负载均衡分散请求。
- 增加消息消费者。
- 将 CPU 密集型任务拆分到独立服务。
- 建立自动扩缩容策略。
- 对热点数据增加缓存。

扩容前仍应确认应用不存在死循环、异常重试和低效代码，否则扩容可能只是暂时缓解问题。

## 32. 不建议直接执行的操作

### 32.1 不建议直接 kill -9

直接终止进程会丢失线程栈等故障现场，还可能造成请求中断、任务状态不一致或数据未正常写入。

应优先采集：

```bash
top -H -p <PID>
jstack <PID>
jstat -gcutil <PID> 1000 10
```

### 32.2 不建议只采集一次线程 Dump

线程可能短时间经过某个方法，仅凭一次采样容易误判。建议间隔数秒连续采集至少三次。

### 32.3 不建议盲目增加线程数

线程数增加不一定提高吞吐量。对于 CPU 密集型任务，线程过多会增加上下文切换，使 CPU 问题更加严重。

### 32.4 不建议盲目调整 JVM 参数

没有 GC 日志、线程栈和监控依据时，随意修改堆大小或垃圾收集器可能引入新的性能问题。

### 32.5 不建议将高 CPU 直接归因于 GC

应通过 `jstat`、GC 日志和线程信息确认 GC 次数与 CPU 变化，而不是看到 Java CPU 高就直接调整 GC。

## 33. 监控与预防建议

建议持续监控以下指标：

- Java 进程 CPU 使用率。
- 宿主机整体 CPU 使用率。
- 用户态和内核态 CPU。
- Load Average。
- 容器 CPU 使用率。
- CPU Throttling。
- Java 线程数量。
- 线程池活跃线程数。
- 线程池队列长度。
- 任务拒绝数量。
- Young GC 次数和耗时。
- Full GC 次数和耗时。
- 对象分配速率。
- 接口 QPS 和响应时间。
- 慢接口数量。
- 异常数量。
- 重试次数。
- 消息积压数量。
- 上下文切换次数。
- 定时任务执行耗时。

告警建议结合持续时间设置，避免因瞬时峰值产生大量无效告警。例如：

- Java 进程 CPU 连续 5 分钟超过阈值。
- CPU 使用率升高且接口延迟同步增加。
- Full GC 次数在短时间内快速增长。
- 单个线程长时间占用一个 CPU 核心。
- 容器 CPU Throttling 持续增加。
- 线程池队列长度持续上升。
- 异常和重试数量突然增加。

## 34. 推荐排查流程

Java 进程 CPU 过高时，可以按照以下顺序排查：

1. 使用 `top` 确认服务器整体 CPU 状态。
2. 区分用户态、内核态、IO 等待和虚拟机抢占。
3. 使用 `ps` 或 `pidstat` 定位高 CPU Java 进程。
4. 记录 Java 进程 PID、启动命令和 JVM 参数。
5. 使用 `top -H -p` 定位高 CPU 线程。
6. 记录高 CPU 线程的十进制线程 ID。
7. 将线程 ID 转换为十六进制。
8. 使用 `jstack` 或 `jcmd` 连续采集多次线程栈。
9. 根据 `nid` 定位高 CPU 线程对应的 Java 调用栈。
10. 判断是否为死循环、正则、异常、序列化或计算任务。
11. 使用 `jstat` 和 GC 日志确认是否为频繁 GC。
12. 检查线程数量、锁竞争和上下文切换。
13. 对比业务流量、定时任务、消息量和最近发布。
14. 必要时使用 Arthas、JFR 或 async-profiler 采样。
15. 如果运行在容器中，检查 CPU Limit 和 Throttling。
16. 采取限流、暂停任务、扩容或重启等临时措施。
17. 修复代码、线程池、GC 或资源配置问题。
18. 使用压力测试和持续监控验证处理效果。

## 35. 常用排查命令汇总

```bash
# 查看系统整体 CPU
top

# 查看 CPU 核心数
nproc
lscpu

# 查看系统负载
uptime

# 查看高 CPU 进程
ps -eo pid,ppid,user,%cpu,%mem,etime,cmd --sort=-%cpu | head -20

# 查看 Java 进程
jps -lv
ps -ef | grep java

# 查看 Java 进程 CPU
pidstat -u -p <PID> 1 10

# 查看 Java 进程中的线程
top -H -p <PID>

# 按线程 CPU 排序
ps -Lp <PID> -o pid,tid,psr,pcpu,stat,comm --sort=-pcpu | head -20

# 十进制线程 ID 转十六进制
printf '%x\n' <线程ID>

# 获取线程栈
jstack <PID> > /tmp/jstack.txt

# 使用 jcmd 获取线程栈
jcmd <PID> Thread.print -l > /tmp/thread.txt

# 查找指定线程
grep -i -A 40 "nid=0x<十六进制线程ID>" /tmp/jstack.txt

# 查看 Java 线程数量
ps -o nlwp= -p <PID>
ls /proc/<PID>/task | wc -l

# 查看上下文切换
pidstat -w -p <PID> 1 10

# 查看 GC 情况
jstat -gcutil <PID> 1000 10

# 查看 GC 原因
jstat -gccause <PID> 1000 10

# 查看堆信息
jcmd <PID> GC.heap_info

# 查看 JVM 参数
jcmd <PID> VM.flags

# 查看 JVM 启动命令
jcmd <PID> VM.command_line

# 查看 Code Cache
jcmd <PID> Compiler.codecache

# 启动 JFR 采样
jcmd <PID> JFR.start name=cpu settings=profile duration=60s filename=/tmp/cpu.jfr

# 查看 Docker 容器 CPU
docker stats

# 查看 Kubernetes Pod CPU
kubectl top pod -n <namespace>

# 查看 Cgroup v2 CPU 限流
cat /sys/fs/cgroup/cpu.stat
```

## 36. 排查结论模板

### 故障现象

Java 应用所在服务器 Load Average 持续升高，Java 进程 CPU 使用率接近多个 CPU 核心上限，业务接口响应时间明显增加。

### 故障确认

通过 `top` 和 `pidstat` 确认 CPU 主要由 Java 进程消耗。使用 `top -H -p` 定位到一个业务线程持续占用接近 100% CPU，将线程 ID 转换为十六进制后，在线程 Dump 中定位到订单补偿任务。

### 根本原因

订单补偿任务在查询不到目标数据时进入重试循环。代码没有设置最大重试次数和等待时间，导致任务持续执行数据库查询和 JSON 解析。多个实例同时执行该任务后，大量线程进入高速重试状态，最终导致 CPU 使用率过高。

### 临时处理

暂停订单补偿定时任务，对相关接口临时限流，并滚动重启异常实例，使 CPU 使用率和接口响应时间恢复正常。

### 永久修复

为补偿任务增加最大重试次数、指数退避和任务超时机制；通过分布式锁避免多个实例重复执行；增加任务执行次数、失败次数和持续时间监控。

### 验证结果

修复后使用相同异常场景进行压力测试，任务失败后按照预期进行有限次数重试，CPU 使用率保持在合理范围内，线程池无持续堆积，接口响应时间恢复正常。

## 37. 总结

Java 进程 CPU 过高排查的关键是从操作系统进程逐步定位到 JVM 线程，再从线程栈定位到具体代码。

首先使用 `top`、`ps` 和 `pidstat` 确认高 CPU Java 进程，然后使用 `top -H -p` 找到占用 CPU 较高的线程。将线程 ID 转换为十六进制后，通过 `jstack` 或 `jcmd` 在线程 Dump 中查找对应的 `nid`，即可定位线程正在执行的 Java 方法。

对于持续高 CPU 问题，应连续采集多次线程栈，并结合 GC、线程数量、上下文切换、业务流量、定时任务和最近发布进行综合判断。常见根本原因包括死循环、频繁重试、高复杂度算法、正则回溯、序列化、锁竞争、线程过多和频繁 GC。

临时处理可以通过限流、暂停非核心任务、扩容和滚动重启恢复业务，但最终仍需要修复代码逻辑、线程池设计、GC 配置或资源限制。完成修复后，应通过压力测试和持续监控确认 CPU、线程、GC 和接口响应时间均已恢复正常。