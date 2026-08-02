# JVM OOM 排查

## 1. 问题概述

JVM OOM 是指 Java 虚拟机在运行过程中无法为对象、类元数据、线程栈、直接内存或其他内存区域分配足够空间，从而抛出 `java.lang.OutOfMemoryError`。

JVM OOM 不等同于 Linux 系统 OOM。JVM OOM 通常由 Java 进程内部某个内存区域达到限制引起，此时 Java 应用日志中一般可以看到 `OutOfMemoryError`。Linux 系统 OOM 则是宿主机或容器整体内存不足，由 Linux 内核或 Cgroup 强制终止进程，应用可能来不及输出 Java 异常。

常见的 OOM 类型包括：

1. Java 堆空间不足。
2. GC 开销超过限制。
3. 元空间不足。
4. 直接内存不足。
5. 无法创建新的本地线程。
6. 数组申请大小超过虚拟机限制。
7. 本地内存分配失败。
8. 容器内存达到限制。
9. Linux 系统触发 OOM Killer。

JVM OOM 排查不能只通过增加 `-Xmx` 解决。内存不足可能由内存泄漏、业务数据量过大、线程数量失控、缓存没有上限、堆外内存泄漏或 JVM 参数不合理等原因引起。

排查的核心目标包括：

1. 确认是否真正发生 OOM。
2. 区分 JVM OOM、容器 OOM 和系统 OOM。
3. 确认发生异常的内存区域。
4. 保存 Heap Dump、GC 日志、线程栈和系统日志。
5. 分析哪些对象、线程或本地内存占用过多。
6. 判断属于容量不足还是内存泄漏。
7. 修复程序逻辑或调整内存配置。
8. 通过压力测试和监控验证修复效果。

## 2. 常见问题现象

JVM 发生 OOM 时，通常会出现以下现象：

1. 应用日志中出现 `java.lang.OutOfMemoryError`。
2. Java 进程突然退出。
3. 服务反复重启。
4. 接口响应时间持续升高。
5. Full GC 频繁执行。
6. Full GC 后内存仍然无法明显下降。
7. Java 堆使用率长期接近上限。
8. 进程 RSS 持续增长。
9. 线程数量持续增加。
10. 容器状态显示 `OOMKilled`。
11. 容器退出码为 `137`。
12. Linux 内核日志中出现 `Killed process`。
13. 应用无法创建新的线程。
14. Netty、NIO 或文件处理出现直接内存错误。
15. 类动态生成或类加载过多导致元空间耗尽。
16. Heap Dump 文件生成后占用大量磁盘空间。
17. 应用在大文件上传、批量导入或报表生成时异常退出。
18. 应用在运行一段时间后内存持续增长，重启后暂时恢复。

常见错误信息包括：

```text
java.lang.OutOfMemoryError: Java heap space
```

```text
java.lang.OutOfMemoryError: GC overhead limit exceeded
```

```text
java.lang.OutOfMemoryError: Metaspace
```

```text
java.lang.OutOfMemoryError: Direct buffer memory
```

```text
java.lang.OutOfMemoryError: unable to create new native thread
```

```text
java.lang.OutOfMemoryError: Requested array size exceeds VM limit
```

```text
java.lang.OutOfMemoryError: Compressed class space
```

```text
Out of memory: Killed process 12345 (java)
```

不同错误对应不同的内存区域和排查方向，不能统一按照 Java 堆内存不足处理。

## 3. JVM 内存结构说明

### 3.1 Java 堆

Java 堆主要用于存放对象实例和数组，通常由以下参数控制：

```text
-Xms
-Xmx
```

其中：

- `-Xms`：Java 堆初始大小。
- `-Xmx`：Java 堆最大大小。

当 Java 堆中没有足够空间分配新对象，并且垃圾回收后仍然无法释放足够内存时，可能出现：

```text
java.lang.OutOfMemoryError: Java heap space
```

### 3.2 元空间

元空间用于存放类元数据、运行时常量池以及与类加载相关的信息。

常见参数包括：

```text
-XX:MetaspaceSize
-XX:MaxMetaspaceSize
```

JDK 8 及以后使用本地内存实现元空间。如果元空间达到设置的最大值，可能出现：

```text
java.lang.OutOfMemoryError: Metaspace
```

### 3.3 直接内存

直接内存不属于普通 Java 堆，常见于 NIO、Netty、文件读写和网络通信。

最大直接内存可以通过以下参数控制：

```text
-XX:MaxDirectMemorySize
```

直接内存不足时，可能出现：

```text
java.lang.OutOfMemoryError: Direct buffer memory
```

### 3.4 线程栈

每个 Java 线程通常都有独立的本地线程栈，大小可以通过以下参数配置：

```text
-Xss
```

线程数量过多时，即使 Java 堆还有空间，也可能因为本地内存不足而无法创建新线程。

常见异常如下：

```text
java.lang.OutOfMemoryError: unable to create new native thread
```

### 3.5 Code Cache

Code Cache 用于保存 JIT 编译后的本地代码。相关参数包括：

```text
-XX:ReservedCodeCacheSize
```

Code Cache 被占满时通常会影响 JIT 编译和应用性能，但不一定直接表现为常见的 Java 堆 OOM。

### 3.6 JVM 本地内存

JVM 自身、GC 数据结构、线程、JNI、本地库、内存映射和动态链接库都会使用本地内存。

因此，Java 进程总内存不等于 Java 堆大小。即使配置：

```bash
-Xmx4g
```

Java 进程实际 RSS 也可能明显超过 4GB。

## 4. JVM OOM 的常见原因

### 4.1 内存泄漏

对象已经没有业务用途，但仍然被 GC Root 间接引用，无法被垃圾回收。

常见泄漏来源包括：

- 静态集合持续保存对象。
- 本地缓存没有容量限制。
- ThreadLocal 没有清理。
- 监听器、回调或订阅关系没有注销。
- 类加载器无法释放。
- 数据库连接和网络连接没有关闭。
- 线程池任务长期引用业务对象。
- 会话数据没有过期。
- 消息积压在内存队列中。
- 定时任务持续保存历史结果。

### 4.2 一次性加载大量数据

应用一次性把大量文件、数据库结果或消息读取到内存中，可能导致堆空间迅速耗尽。

常见场景包括：

- 数据库查询没有分页。
- Excel、CSV 或 PDF 文件整体加载。
- 大文件使用 `readAllBytes()`。
- 批量导出全部数据。
- 超大 JSON 反序列化。
- 一次性获取全部缓存数据。
- 消息批量大小设置过大。

### 4.3 缓存没有上限

使用 `HashMap`、`ConcurrentHashMap` 或其他集合实现本地缓存，但没有设置最大容量、过期时间和淘汰策略，会导致缓存对象持续累积。

### 4.4 堆内存配置过小

应用本身没有泄漏，但正常业务所需内存已经超过 `-Xmx` 限制，也会发生 OOM。

此时 Heap Dump 中对象通常具有合理业务用途，内存增长与流量或数据量高度相关。

### 4.5 对象创建速度过快

应用短时间内创建大量临时对象，垃圾回收速度跟不上对象分配速度，可能导致频繁 GC，最终出现 OOM。

常见场景包括：

- 高频 JSON 转换。
- 大量字符串拼接。
- 日志参数构造。
- 图片和文件处理。
- 高频数据复制。
- 循环中创建大集合。
- 请求突增。

### 4.6 大对象分配失败

应用需要创建一个超大数组、字符串、集合或字节缓冲区，但堆中没有足够连续空间，可能导致 OOM。

### 4.7 类加载器泄漏

应用持续动态生成或加载类，但旧类加载器无法被回收，会导致元空间持续增长。

常见场景包括：

- 热部署。
- 动态代理。
- CGLIB。
- Groovy 等脚本引擎。
- JSP 重编译。
- 插件系统。
- 应用容器重复部署。
- 自定义 ClassLoader 使用不当。

### 4.8 直接内存泄漏

NIO、Netty 等框架可能使用直接内存。如果 DirectByteBuffer 没有及时释放，或者直接内存配置过小，可能出现直接内存 OOM。

### 4.9 线程泄漏

应用不断创建线程或线程池，线程无法结束，可能耗尽本地内存、进程数或线程数限制。

常见场景包括：

- 每个请求创建线程。
- 线程池被重复创建。
- 线程阻塞后无法退出。
- 定时任务重复注册。
- 连接池或客户端内部线程泄漏。
- 无限重试任务长期存活。

### 4.10 容器内存配置不合理

Java 堆接近容器内存限制，没有为直接内存、元空间、线程栈和 JVM 本身预留空间，可能导致容器被 Cgroup 强制终止。

例如，容器内存限制为 4GB，却配置：

```bash
-Xmx4g
```

此时即使 Java 堆没有 OOM，容器总内存也可能超过 4GB，从而显示 `OOMKilled`。

## 5. 第一步：区分 JVM OOM、容器 OOM 和系统 OOM

### 5.1 JVM OOM

如果应用日志中出现明确的：

```text
java.lang.OutOfMemoryError
```

通常表示 JVM 内部某个内存区域发生 OOM。

Java 进程是否退出取决于：

- OOM 发生在哪个线程。
- 应用是否捕获异常。
- 是否配置 OOM 后退出。
- JVM 是否还能继续工作。

### 5.2 容器 OOM

Docker 容器可以执行：

```bash
docker inspect <容器名称或ID> --format '{{.State.OOMKilled}}'
```

查看退出码：

```bash
docker inspect <容器名称或ID> --format '{{.State.ExitCode}}'
```

如果：

```text
OOMKilled=true
```

并且退出码为：

```text
137
```

通常表示容器内存达到限制，被 Cgroup 强制终止。

### 5.3 Kubernetes OOM

查看 Pod 状态：

```bash
kubectl describe pod <pod-name> -n <namespace>
```

如果出现：

```text
Reason: OOMKilled
Exit Code: 137
```

说明容器曾因超过内存限制被终止。

查看上一次退出前的日志：

```bash
kubectl logs <pod-name> -n <namespace> --previous
```

### 5.4 Linux 系统 OOM

执行：

```bash
dmesg -T | grep -iE "out of memory|oom|killed process"
```

或者：

```bash
journalctl -k | grep -iE "out of memory|oom|killed process"
```

如果内核日志中出现：

```text
Out of memory: Killed process 12345 (java)
```

说明宿主机或对应内存 Cgroup 发生了 OOM。

## 6. 排查前的注意事项

发生 OOM 后，不要立即删除日志、Heap Dump 或重启全部实例。

应优先保存：

1. OOM 完整异常信息。
2. OOM 发生时间。
3. Java 进程 PID。
4. JVM 启动参数。
5. Heap Dump。
6. GC 日志。
7. 线程 Dump。
8. Java 进程内存信息。
9. 操作系统内存信息。
10. 容器资源限制。
11. 内核 OOM 日志。
12. 问题发生时的业务流量和任务信息。

建议执行：

```bash
date
free -h
ps -eo pid,ppid,user,%mem,rss,vsz,cmd --sort=-rss | head -20
cat /proc/<PID>/status
jcmd <PID> VM.flags
jcmd <PID> VM.command_line
jcmd <PID> GC.heap_info
jstat -gcutil <PID> 1000 10
```

生产环境生成 Heap Dump 可能导致应用暂停、磁盘 IO 增加或磁盘空间耗尽，操作前必须检查剩余空间并评估影响。

## 7. 查看 JVM 启动参数

执行：

```bash
jcmd <PID> VM.flags
```

查看完整启动命令：

```bash
jcmd <PID> VM.command_line
```

也可以查看：

```bash
tr '\0' ' ' < /proc/<PID>/cmdline
```

重点关注：

```text
-Xms
-Xmx
-Xss
-XX:MaxMetaspaceSize
-XX:MaxDirectMemorySize
-XX:+HeapDumpOnOutOfMemoryError
-XX:HeapDumpPath
-XX:+ExitOnOutOfMemoryError
-XX:+CrashOnOutOfMemoryError
```

需要确认：

- 最大堆内存是否符合服务器容量。
- 是否为堆外内存预留空间。
- 线程栈是否设置过大。
- 元空间是否限制过小。
- 直接内存是否限制过小。
- 是否配置自动 Heap Dump。
- Heap Dump 目录是否存在并有足够空间。

## 8. Java heap space 排查

异常信息：

```text
java.lang.OutOfMemoryError: Java heap space
```

表示 Java 堆无法为新对象分配空间。

常见原因包括：

- Java 堆设置过小。
- 存在内存泄漏。
- 缓存对象持续增长。
- 一次性加载大量数据。
- 消息或任务大量积压。
- 大对象分配过多。
- 请求并发量过高。
- 对象创建速度超过 GC 回收速度。

排查重点包括：

1. 分析 Heap Dump。
2. 查看堆中占用最大的对象。
3. 查看对象的 GC Root 引用链。
4. 检查 Full GC 后内存是否回落。
5. 检查缓存、集合、队列和会话数量。
6. 对比内存增长与业务流量。
7. 检查最近代码和配置变更。

## 9. GC overhead limit exceeded 排查

异常信息：

```text
java.lang.OutOfMemoryError: GC overhead limit exceeded
```

表示 JVM 花费了大量时间执行垃圾回收，但每次回收得到的内存很少。

常见原因包括：

- 堆空间接近耗尽。
- 内存泄漏。
- 老年代中存活对象过多。
- 堆设置过小。
- 对象分配速度过快。
- 应用在 OOM 边缘反复 Full GC。

执行：

```bash
jstat -gcutil <PID> 1000 10
```

如果 Full GC 次数持续增长，老年代使用率仍然接近 100%，应优先分析 Heap Dump，而不是简单关闭 GC Overhead Limit 检查。

不建议仅通过以下参数掩盖问题：

```text
-XX:-UseGCOverheadLimit
```

关闭检查后，应用可能继续长时间频繁 GC，最终仍然发生堆 OOM。

## 10. Metaspace OOM 排查

异常信息：

```text
java.lang.OutOfMemoryError: Metaspace
```

常见原因包括：

- `MaxMetaspaceSize` 设置过小。
- 动态生成了大量类。
- 类加载器泄漏。
- 应用频繁热部署。
- 大量动态代理。
- 脚本引擎持续创建类。
- 插件或框架反复创建 ClassLoader。

查看类加载统计：

```bash
jstat -class <PID> 1000 10
```

查看类加载器统计：

```bash
jcmd <PID> VM.classloader_stats
```

查看类直方图：

```bash
jcmd <PID> GC.class_histogram
```

查看元空间情况：

```bash
jcmd <PID> GC.heap_info
```

排查重点包括：

- 已加载类数量是否持续增长。
- 已卸载类数量是否很少。
- ClassLoader 数量是否持续增加。
- 是否存在大量相同框架生成类。
- 应用是否频繁重新部署或动态加载脚本。
- 元空间最大值是否合理。

如果只是正常业务需要更多类元数据，可以适当增加元空间限制。如果类和 ClassLoader 数量持续增长，则应修复类加载器泄漏。

## 11. Direct buffer memory OOM 排查

异常信息：

```text
java.lang.OutOfMemoryError: Direct buffer memory
```

常见于：

- Netty。
- NIO。
- 文件传输。
- 大文件处理。
- 网络通信。
- 直接缓冲池。

查看直接内存配置：

```bash
jcmd <PID> VM.flags | grep -i DirectMemory
```

如果启动时启用了 Native Memory Tracking，可以执行：

```bash
jcmd <PID> VM.native_memory summary
```

也可以检查 DirectByteBuffer 数量：

```bash
jcmd <PID> GC.class_histogram | grep DirectByteBuffer
```

排查重点包括：

- 是否频繁调用 `ByteBuffer.allocateDirect()`。
- DirectByteBuffer 是否被长期引用。
- Netty Buffer 是否正确释放。
- `MaxDirectMemorySize` 是否过小。
- 直接内存使用是否随流量持续增长。
- 是否存在大文件并发处理。
- 是否因为 GC 不及时导致 Cleaner 延迟执行。

对于 Netty，应同时检查引用计数、内存泄漏检测日志和 PooledByteBufAllocator 配置。

## 12. unable to create new native thread 排查

异常信息：

```text
java.lang.OutOfMemoryError: unable to create new native thread
```

该异常不一定表示 Java 堆已满，通常表示 JVM 无法再创建新的操作系统线程。

常见原因包括：

- 线程数量过多。
- 线程泄漏。
- 单线程栈设置过大。
- 进程线程数达到限制。
- 用户进程数达到限制。
- 容器 PID 限制。
- 本地内存不足。
- 系统整体内存不足。

查看 Java 线程数量：

```bash
ps -o nlwp= -p <PID>
```

也可以执行：

```bash
ls /proc/<PID>/task | wc -l
```

查看用户进程限制：

```bash
ulimit -u
```

查看进程限制：

```bash
cat /proc/<PID>/limits
```

查看系统线程和进程限制：

```bash
sysctl kernel.threads-max
sysctl kernel.pid_max
```

查看线程 Dump：

```bash
jcmd <PID> Thread.print > /tmp/thread.txt
```

排查重点包括：

- 哪类线程数量最多。
- 是否重复创建线程池。
- 是否每个请求创建线程。
- 线程是否因为下游超时长期阻塞。
- `-Xss` 是否配置过大。
- 容器 PID Limit 是否过小。
- 本地内存是否不足。

## 13. Requested array size exceeds VM limit 排查

异常信息：

```text
java.lang.OutOfMemoryError: Requested array size exceeds VM limit
```

表示程序尝试创建一个超过 JVM 允许范围的数组。

常见原因包括：

- 数组长度计算溢出。
- 文件大小转换错误。
- 错误地将全部数据装入数组。
- 使用非常大的集合初始化容量。
- 外部输入参数没有限制。
- 数据长度字段异常。

排查时应重点查看异常堆栈中的代码行，并检查：

- 数组长度的来源。
- 整数计算是否溢出。
- 是否可以使用流式处理。
- 是否可以分块处理。
- 是否限制了上传文件和请求体大小。
- 是否需要分页读取数据。

单纯增大 Java 堆通常无法解决超过 JVM 数组长度限制的问题。

## 14. Compressed class space OOM 排查

异常信息：

```text
java.lang.OutOfMemoryError: Compressed class space
```

表示压缩类指针使用的类空间不足。

相关参数包括：

```text
-XX:CompressedClassSpaceSize
```

常见原因与元空间 OOM 类似：

- 动态生成大量类。
- ClassLoader 泄漏。
- 代理类持续增加。
- 参数设置过小。

应结合类加载数量、ClassLoader 统计和 Heap Dump 分析。

## 15. 获取 Heap Dump

### 15.1 自动生成 Heap Dump

建议在启动参数中配置：

```bash
-XX:+HeapDumpOnOutOfMemoryError
-XX:HeapDumpPath=/data/dump
```

如果希望 JVM 在发生 OOM 后退出，可以根据业务情况配置：

```bash
-XX:+ExitOnOutOfMemoryError
```

是否配置退出需要结合服务治理、自动重启和数据一致性要求评估。

### 15.2 手动生成 Heap Dump

使用 `jcmd`：

```bash
jcmd <PID> GC.heap_dump /data/dump/heap.hprof
```

也可以使用：

```bash
jmap -dump:format=b,file=/data/dump/heap.hprof <PID>
```

生成前应检查：

```bash
df -h /data/dump
```

Heap Dump 文件大小可能接近 Java 堆的已使用容量。生产环境中生成 Heap Dump 可能引发 Stop The World 和大量磁盘 IO，应尽量选择有足够空间的独立磁盘。

### 15.3 优先使用 OOM 自动 Dump

OOM 自动生成的 Heap Dump 更接近故障发生时的真实现场。故障发生后重启服务再手动生成的 Dump，通常无法还原 OOM 前的对象状态。

## 16. Heap Dump 分析思路

常见分析工具包括：

- Eclipse Memory Analyzer。
- VisualVM。
- JProfiler。
- YourKit。
- IntelliJ Profiler。
- IBM HeapAnalyzer。

使用 Eclipse MAT 时，可以先查看：

- Leak Suspects Report。
- Dominator Tree。
- Histogram。
- Top Consumers。
- Path to GC Roots。
- Thread Overview。
- Class Loader Explorer。

### 16.1 Histogram

Histogram 按类统计：

- 对象数量。
- Shallow Heap。
- Retained Heap。

### 16.2 Dominator Tree

Dominator Tree 可以查看哪些对象支配了大量其他对象，适合定位大集合、缓存和业务对象持有链。

### 16.3 Shallow Heap

Shallow Heap 表示对象自身占用的内存，不包括其引用对象。

### 16.4 Retained Heap

Retained Heap 表示该对象被回收后，能够一起释放的总内存。

分析泄漏时，Retained Heap 通常比 Shallow Heap 更有参考价值。

### 16.5 Path to GC Roots

通过 `Path to GC Roots` 可以查看对象为什么无法被回收。

常见 GC Root 包括：

- 静态字段。
- 活跃线程。
- ThreadLocal。
- JNI 引用。
- 类加载器。
- 监视器锁。
- JVM 内部引用。

## 17. 判断内存泄漏还是容量不足

### 17.1 内存泄漏特征

- 内存随运行时间持续增长。
- 业务低峰期内存也不下降。
- Full GC 后老年代仍然持续升高。
- 重启后内存恢复，运行一段时间后再次增长。
- Heap Dump 中某类对象数量异常。
- 对象被静态集合、线程或 ClassLoader 长期引用。
- 同类业务对象远超正常数量。
- 缓存、队列或会话没有上限。

### 17.2 容量不足特征

- 内存增长与并发量或数据量同步。
- Heap Dump 中对象大多属于正常存活对象。
- 流量降低后内存可以回落。
- Full GC 后可以释放较多空间。
- 增加实例或降低批量大小后问题消失。
- 堆内存配置明显小于正常业务需求。
- 没有明显异常引用链。

### 17.3 两者可能同时存在

应用可能既存在容量不足，也存在局部内存泄漏。例如，堆配置偏小，同时缓存没有限制。排查时不能只选择一个原因。

## 18. 查看堆内存和 GC 状态

查看堆信息：

```bash
jcmd <PID> GC.heap_info
```

持续查看 GC：

```bash
jstat -gcutil <PID> 1000 10
```

查看 GC 原因：

```bash
jstat -gccause <PID> 1000 10
```

重点关注：

- 新生代使用率。
- 老年代使用率。
- 元空间使用率。
- Young GC 次数和耗时。
- Full GC 次数和耗时。
- Full GC 后内存是否下降。
- OOM 前是否发生连续 Full GC。
- GC 总耗时占应用运行时间的比例。

如果 Full GC 后老年代始终维持在高位，应重点分析长期存活对象。

## 19. 查看对象直方图

执行：

```bash
jcmd <PID> GC.class_histogram
```

保存结果：

```bash
jcmd <PID> GC.class_histogram > /tmp/histogram.txt
```

也可以执行：

```bash
jmap -histo <PID>
```

对象直方图可以快速查看：

- 对象数量最多的类。
- 占用内存最大的类。
- 字节数组数量。
- 字符串数量。
- 集合节点数量。
- DirectByteBuffer 数量。
- 业务实体对象数量。

常见高占用类型包括：

```text
[B
[C
java.lang.String
java.util.HashMap$Node
java.util.concurrent.ConcurrentHashMap$Node
java.lang.Object[]
```

`[B` 表示字节数组，`[C` 表示字符数组。发现基础数组占用过高时，需要结合引用链确定具体由哪些业务对象持有。

## 20. 连续比较对象增长

如果应用尚未 OOM，可以在不同时间采集对象直方图：

```bash
jcmd <PID> GC.class_histogram > /tmp/histo-1.txt
```

间隔一段时间后再次采集：

```bash
jcmd <PID> GC.class_histogram > /tmp/histo-2.txt
```

对比哪些类的对象数量和内存持续增长。

重点关注：

- 业务请求对象。
- 缓存对象。
- 队列任务对象。
- 会话对象。
- 日志事件对象。
- 网络 Buffer。
- ClassLoader。
- 线程相关对象。

对象增长只能说明现象，最终仍需通过 Heap Dump 引用链定位谁在持有这些对象。

## 21. ThreadLocal 内存泄漏排查

ThreadLocal 数据存储在线程的 `ThreadLocalMap` 中。在线程池环境中，线程生命周期通常很长，如果业务代码没有调用 `remove()`，对象可能长期无法释放。

常见错误写法：

```java
threadLocal.set(context);
try {
    process();
} finally {
    // 缺少 threadLocal.remove()
}
```

正确处理方式：

```java
threadLocal.set(context);
try {
    process();
} finally {
    threadLocal.remove();
}
```

Heap Dump 中如果发现大量对象通过以下路径被线程引用，应重点检查 ThreadLocal：

```text
Thread
  → threadLocals
  → ThreadLocalMap
  → Entry
  → value
```

## 22. 本地缓存泄漏排查

常见问题代码：

```java
private static final Map<String, Object> CACHE = new ConcurrentHashMap<>();
```

如果只写入、不删除，缓存会持续增长。

应检查：

- 是否设置最大容量。
- 是否设置过期时间。
- 是否存在淘汰策略。
- Key 是否持续变化。
- 缓存值是否过大。
- 是否缓存了完整文件或响应。
- 是否重复保存相同数据。
- 是否可以使用 Redis 等外部缓存。

可以使用 Caffeine 等支持容量控制和过期机制的缓存组件，并监控缓存大小、命中率和淘汰数量。

## 23. 集合和队列堆积排查

常见高风险结构包括：

- 无界 `BlockingQueue`。
- `ConcurrentLinkedQueue`。
- 无限制的 `List`。
- 未清理的 `Map`。
- 消息发送缓冲区。
- 异步日志队列。
- 线程池任务队列。
- 事件总线队列。

排查时应检查：

- 队列生产速度是否大于消费速度。
- 线程池是否阻塞。
- 下游服务是否变慢。
- 是否配置队列最大长度。
- 队列满后使用什么拒绝策略。
- 任务对象是否持有大对象。
- 消费失败后是否重新入队。

## 24. 大文件和批量任务排查

以下写法容易导致内存峰值过高：

```java
byte[] data = Files.readAllBytes(path);
```

```java
List<Entity> data = repository.findAll();
```

```java
String result = readEntireFile();
```

优化方向包括：

- 流式读取。
- 分页查询。
- 分批处理。
- 限制上传文件大小。
- 限制请求体大小。
- 降低批量任务并发度。
- 处理完成后及时释放引用。
- 避免同时保留原始数据和多个转换副本。
- 大文件使用临时文件而不是全部保存在内存中。

## 25. 线程泄漏排查

查看线程数量：

```bash
ps -o nlwp= -p <PID>
```

获取线程 Dump：

```bash
jcmd <PID> Thread.print > /tmp/thread.txt
```

查看线程名称：

```bash
grep '^"' /tmp/thread.txt | sort | uniq -c | sort -nr | head -30
```

重点检查：

- 相同名称线程是否异常多。
- 线程数量是否持续增长。
- 是否重复创建线程池。
- 是否存在大量阻塞线程。
- 线程是否等待永远不会返回的下游请求。
- 线程栈大小是否过大。
- 容器 PID Limit 是否达到上限。

## 26. Native Memory Tracking 排查

Native Memory Tracking 可以帮助分析 JVM 本地内存，但通常需要在 JVM 启动时开启。

启动参数：

```bash
-XX:NativeMemoryTracking=summary
```

更详细模式：

```bash
-XX:NativeMemoryTracking=detail
```

查看本地内存：

```bash
jcmd <PID> VM.native_memory summary
```

建立基线：

```bash
jcmd <PID> VM.native_memory baseline
```

一段时间后查看差异：

```bash
jcmd <PID> VM.native_memory summary.diff
```

常见分类包括：

- Java Heap。
- Class。
- Thread。
- Code。
- GC。
- Compiler。
- Internal。
- Symbol。
- Arena Chunk。
- Native Memory Tracking。

需要注意，NMT 会带来一定额外开销，应根据生产环境要求选择 `summary` 或 `detail`。

## 27. RSS 高但 Java 堆不高的排查

如果操作系统看到 Java 进程 RSS 很高，但 Java 堆使用率不高，应重点排查堆外内存。

查看进程状态：

```bash
cat /proc/<PID>/status
```

重点查看：

```text
VmRSS
RssAnon
RssFile
RssShmem
VmSwap
Threads
```

查看内存映射：

```bash
pmap -x <PID> | tail -20
```

可能原因包括：

- 直接内存。
- 线程栈。
- 元空间。
- Code Cache。
- JNI 本地库。
- 内存映射文件。
- glibc 内存分配碎片。
- JVM 或 GC 本地数据结构。
- 大量线程。
- 本地库内存泄漏。

此类问题不能只分析 Java Heap Dump，因为 Heap Dump 通常不包含完整的本地内存内容。

## 28. 检查容器内存配置

Kubernetes 配置示例：

```yaml
resources:
  requests:
    memory: 2Gi
  limits:
    memory: 4Gi
```

查看 Pod 实际使用：

```bash
kubectl top pod <pod-name> -n <namespace>
```

查看 Pod 配置：

```bash
kubectl get pod <pod-name> -n <namespace> -o yaml
```

容器内存需要覆盖：

```text
Java Heap
+ Metaspace
+ Direct Memory
+ Thread Stack
+ Code Cache
+ GC Native Memory
+ JNI 和本地库
+ 其他进程内存
```

如果使用较新的 JDK，可以考虑基于容器内存比例配置：

```text
-XX:InitialRAMPercentage
-XX:MaxRAMPercentage
```

具体比例不能机械套用，应根据应用的直接内存、线程数和元空间实际占用预留安全空间。

## 29. 检查 Linux 系统内存

执行：

```bash
free -h
```

查看高内存进程：

```bash
ps -eo pid,user,%mem,rss,vsz,cmd --sort=-rss | head -20
```

查看 Swap：

```bash
swapon --show
```

查看内核 OOM：

```bash
dmesg -T | grep -iE "out of memory|oom|killed process"
```

如果服务器运行多个 Java 服务，需要确认所有服务的最大堆内存总和是否超过物理内存可承载范围。

不能只计算 `-Xmx` 总和，还应为操作系统、文件缓存、堆外内存、数据库和其他进程预留空间。

## 30. GC 日志分析

建议启用 GC 日志。

JDK 9 及以上：

```bash
-Xlog:gc*:file=/data/logs/gc.log:time,uptime,level,tags:filecount=10,filesize=100M
```

JDK 8 常见配置：

```bash
-XX:+PrintGCDetails
-XX:+PrintGCDateStamps
-Xloggc:/data/logs/gc.log
-XX:+UseGCLogFileRotation
-XX:NumberOfGCLogFiles=10
-XX:GCLogFileSize=100M
```

重点分析：

- Young GC 频率。
- Full GC 频率。
- GC 暂停时间。
- 每次 GC 前后内存变化。
- 老年代增长趋势。
- 对象晋升情况。
- Humongous Object。
- 元空间增长。
- GC 失败原因。
- OOM 前是否发生连续 Full GC。

如果 Full GC 后内存几乎不下降，通常说明堆中存在大量长期存活对象或内存泄漏。

## 31. 使用 Arthas 辅助排查

Arthas 可以查看 JVM 实时状态。

常用命令：

```text
dashboard
```

查看内存和 GC：

```text
memory
```

查看线程：

```text
thread
```

查看类加载器：

```text
classloader
```

查看 JVM 参数：

```text
jvm
```

查看对象实例：

```text
vmtool --action getInstances --className <类名> --limit 10
```

使用在线诊断工具时应注意：

- 不要一次获取大量对象实例。
- 不要打印包含敏感信息的完整对象。
- 不要在高负载环境执行范围过大的命令。
- 不要通过在线修改掩盖根本问题。
- 操作前评估对生产性能的影响。

## 32. 常见临时处理措施

当 OOM 已经影响业务时，可以根据实际情况采取：

1. 对高内存接口进行限流。
2. 暂停大文件和批量任务。
3. 降低消息消费并发度。
4. 暂停异常定时任务。
5. 清理可以安全清理的业务队列。
6. 增加应用实例分担流量。
7. 临时增加容器内存限制。
8. 在保存现场后滚动重启异常实例。
9. 将流量切换到健康实例。
10. 对非核心功能进行降级。
11. 限制上传文件和请求体大小。
12. 临时关闭异常增长的本地缓存。

重启之前应尽量保存 Heap Dump、GC 日志和线程 Dump，否则问题现场会丢失。

## 33. 常见永久修复措施

### 33.1 修复内存泄漏

- 删除无效的长期引用。
- 为 ThreadLocal 增加 `remove()`。
- 清理监听器和回调。
- 修复 ClassLoader 泄漏。
- 关闭未释放的连接和资源。
- 清理无效会话。
- 避免静态集合无限增长。

### 33.2 限制缓存大小

- 设置最大容量。
- 设置过期时间。
- 使用合理的淘汰策略。
- 监控缓存条目数量。
- 避免缓存超大对象。
- 将大规模缓存迁移到独立缓存服务。

### 33.3 优化数据处理

- 数据库分页查询。
- 大文件流式处理。
- 批量任务分批执行。
- 限制单次任务数据量。
- 减少对象重复复制。
- 处理完成后及时解除引用。
- 避免将整个响应保存在内存中。

### 33.4 优化线程管理

- 使用统一线程池。
- 限制最大线程数。
- 使用有界任务队列。
- 设置任务超时。
- 正确处理中断。
- 避免每个请求创建线程。
- 修复阻塞和线程泄漏。

### 33.5 合理调整 JVM 参数

- 根据实际存活对象容量调整堆大小。
- 为元空间设置合理限制。
- 为直接内存预留空间。
- 根据线程数量设置合适的 `-Xss`。
- 为容器总内存保留安全余量。
- 根据 GC 日志调整垃圾收集器参数。

JVM 参数调整必须建立在监控、Heap Dump 和压力测试结果上，不能代替程序缺陷修复。

## 34. 不建议直接执行的操作

### 34.1 不建议只增加 Xmx

增加堆内存可以缓解容量不足，但如果存在内存泄漏，只会延迟 OOM 再次发生，还会增加故障发生时的 Heap Dump 大小和 GC 停顿风险。

### 34.2 不建议发生 OOM 后立即重启

重启会清除内存现场。应优先保留 Heap Dump、GC 日志和线程栈，再根据业务影响决定是否重启。

### 34.3 不建议随意执行 jmap -dump:live

带 `live` 的 Heap Dump 通常会触发 Full GC，可能造成较长停顿。生产环境执行前必须评估影响。

### 34.4 不建议忽略堆外内存

Java Heap Dump 只能反映 Java 堆对象。如果 RSS 明显高于堆使用量，应继续排查线程、直接内存、元空间和本地库。

### 34.5 不建议捕获 OOM 后继续长期运行

OOM 后 JVM 可能已经处于不稳定状态。应用即使捕获 `OutOfMemoryError`，也不能保证所有功能仍然正常，应根据服务治理策略进行安全退出和实例替换。

## 35. 监控与预防建议

建议持续监控以下指标：

- Java 堆使用量和使用率。
- 新生代和老年代使用率。
- 元空间使用量。
- 直接内存使用量。
- Java 进程 RSS。
- 容器内存使用量。
- 容器 Working Set。
- Swap 使用量。
- Young GC 次数和耗时。
- Full GC 次数和耗时。
- GC 后老年代使用量。
- 对象分配速率。
- 线程数量。
- 本地缓存条目数量。
- 线程池队列长度。
- 消息积压数量。
- OOM 次数。
- 容器 OOMKilled 次数。
- Heap Dump 生成状态。
- 磁盘剩余空间。

推荐设置以下预防措施：

1. 启用 OOM 自动 Heap Dump。
2. 配置 Heap Dump 独立目录。
3. 确保 Dump 目录空间充足。
4. 启用 GC 日志轮转。
5. 监控老年代和元空间趋势。
6. 监控线程数量和缓存大小。
7. 对大文件和批量任务设置限制。
8. 对队列设置容量上限。
9. 定期进行压力测试。
10. 建立 OOM 后自动告警和实例替换机制。

## 36. 推荐排查流程

JVM OOM 可以按照以下顺序排查：

1. 查看应用日志中的完整 `OutOfMemoryError`。
2. 根据错误信息确认 OOM 类型。
3. 检查 Java 进程是否仍然存活。
4. 检查 Docker、Kubernetes 和内核 OOM 记录。
5. 区分 JVM OOM、容器 OOM 和系统 OOM。
6. 保存 JVM 启动参数、线程栈和 GC 状态。
7. 检查是否已经生成 Heap Dump。
8. 检查 Heap Dump 文件是否完整。
9. 使用 MAT 等工具查看 Histogram 和 Dominator Tree。
10. 定位 Retained Heap 较大的对象。
11. 通过 Path to GC Roots 查找对象无法释放的原因。
12. 检查缓存、集合、队列、ThreadLocal 和 ClassLoader。
13. 如果堆使用不高，检查元空间、直接内存和线程。
14. 如果进程被容器终止，检查内存 Limit 和堆外空间。
15. 对比内存增长与流量、任务和最近发布。
16. 判断属于内存泄漏还是容量不足。
17. 采取限流、暂停任务、扩容和滚动重启等临时措施。
18. 修复代码、缓存、线程、数据处理或 JVM 配置。
19. 通过压力测试复现并验证修复效果。
20. 增加长期监控和 OOM 现场自动保留机制。

## 37. 常用排查命令汇总

```bash
# 查看 Java 进程
jps -lv
ps -ef | grep java

# 查看 JVM 参数
jcmd <PID> VM.flags

# 查看 JVM 启动命令
jcmd <PID> VM.command_line

# 查看堆信息
jcmd <PID> GC.heap_info

# 查看 GC 使用率
jstat -gcutil <PID> 1000 10

# 查看 GC 原因
jstat -gccause <PID> 1000 10

# 查看类加载统计
jstat -class <PID> 1000 10

# 查看对象直方图
jcmd <PID> GC.class_histogram

# 生成 Heap Dump
jcmd <PID> GC.heap_dump /data/dump/heap.hprof

# 使用 jmap 生成 Heap Dump
jmap -dump:format=b,file=/data/dump/heap.hprof <PID>

# 查看线程栈
jcmd <PID> Thread.print -l

# 查看 Java 线程数量
ps -o nlwp= -p <PID>
ls /proc/<PID>/task | wc -l

# 查看类加载器统计
jcmd <PID> VM.classloader_stats

# 查看本地内存
jcmd <PID> VM.native_memory summary

# 建立 NMT 基线
jcmd <PID> VM.native_memory baseline

# 查看 NMT 增量
jcmd <PID> VM.native_memory summary.diff

# 查看进程内存
cat /proc/<PID>/status

# 查看内存映射
pmap -x <PID> | tail -20

# 查看系统内存
free -h

# 查看高内存进程
ps -eo pid,user,%mem,rss,vsz,cmd --sort=-rss | head -20

# 查看 Swap
swapon --show

# 查看 Linux OOM 日志
dmesg -T | grep -iE "out of memory|oom|killed process"

# 查看 systemd 内核日志
journalctl -k | grep -iE "out of memory|oom|killed process"

# 查看 Docker OOM 状态
docker inspect <容器名称或ID> --format '{{.State.OOMKilled}}'

# 查看 Docker 退出码
docker inspect <容器名称或ID> --format '{{.State.ExitCode}}'

# 查看 Kubernetes Pod 状态
kubectl describe pod <pod-name> -n <namespace>

# 查看 Kubernetes 上次退出日志
kubectl logs <pod-name> -n <namespace> --previous

# 查看磁盘空间
df -hT

# 查看 inode
df -i
```

## 38. 排查结论模板

### 故障现象

Java 应用运行数小时后内存使用率持续升高，Full GC 频率不断增加，最终出现 `java.lang.OutOfMemoryError: Java heap space`，服务实例退出。

### 故障确认

通过应用日志确认发生 Java 堆 OOM。JVM 已配置自动 Heap Dump，故障发生后成功生成 `heap.hprof`。GC 日志显示 Full GC 后老年代使用率仍然接近 100%。

### 根本原因

使用 Eclipse MAT 分析 Heap Dump 后发现，一个静态 `ConcurrentHashMap` 持有大量用户查询结果，占用了大部分 Retained Heap。该 Map 被作为本地缓存使用，但没有配置最大容量和过期清理机制。随着不同查询条件持续增加，缓存条目无法释放，最终耗尽 Java 堆。

### 临时处理

暂停高流量查询接口，在保存 Heap Dump 和 GC 日志后滚动重启异常实例，并对查询接口临时限流，恢复核心业务。

### 永久修复

将无上限 `ConcurrentHashMap` 替换为具有最大容量和过期策略的缓存组件，限制单条缓存对象大小，并增加缓存条目数量、命中率和淘汰数量监控。同时根据正常业务存活对象容量重新评估 Java 堆大小。

### 验证结果

修复后使用相同请求模式进行持续压力测试，缓存容量保持在设定上限以内，Full GC 后老年代内存能够正常回落，Java 进程 RSS 保持稳定，未再次出现 OOM。

## 39. 总结

JVM OOM 排查的第一步是确认 OOM 类型，并区分 Java 堆 OOM、元空间 OOM、直接内存 OOM、线程创建失败、容器 OOM 和 Linux 系统 OOM。

如果是 Java 堆 OOM，应优先分析 OOM 发生时的 Heap Dump，通过 Histogram、Dominator Tree、Retained Heap 和 Path to GC Roots 定位占用内存最大的对象及其引用关系。如果 Java 堆使用率不高，但进程 RSS 持续增长，则应继续检查直接内存、线程栈、元空间、JNI 和其他本地内存。

内存泄漏与容量不足的处理方式不同。内存泄漏需要修复长期引用、无界缓存、ThreadLocal、队列或 ClassLoader 等问题；正常容量不足则需要通过分页、流式处理、降低并发、增加实例或合理扩容解决。

增加 `-Xmx` 只能缓解部分容量问题，不能代替根本原因分析。生产环境应提前配置 Heap Dump、GC 日志和内存监控，在 OOM 发生时自动保留故障现场，并通过压力测试验证修复结果。