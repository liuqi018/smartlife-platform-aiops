# JVM Full GC 频繁排查

## 1. 问题概述

Full GC 是指 JVM 对整个 Java 堆或堆中主要内存区域进行垃圾回收的过程。与主要回收新生代的 Young GC 相比，Full GC 通常会处理老年代，并且可能同时处理元空间、类卸载等内容。

Full GC 往往会触发 Stop The World。在 Stop The World 期间，大部分业务线程会暂停运行，等待垃圾回收完成。如果 Full GC 执行频繁或单次停顿时间过长，应用就可能出现接口响应变慢、请求超时、线程堆积和服务不可用等问题。

需要注意，不同垃圾收集器中的术语和行为不完全相同。例如，G1 中还存在 Young GC、Mixed GC、Concurrent Mark 和 Full GC。排查时不能只看到日志中有 GC 就认为发生了 Full GC，应结合垃圾收集器类型和 GC 日志中的具体事件判断。

Full GC 频繁排查的核心目标包括：

1. 确认是否真的发生了频繁 Full GC。
2. 确认当前使用的垃圾收集器。
3. 确定 Full GC 的触发原因。
4. 分析 Full GC 前后的内存变化。
5. 判断是内存泄漏、容量不足还是参数不合理。
6. 定位大量存活对象或高分配速率的代码。
7. 通过代码优化、容量调整或 GC 参数优化解决问题。
8. 通过持续监控和压力测试验证处理效果。

## 2. 常见问题现象

JVM Full GC 频繁时，通常会出现以下现象：

1. 应用接口响应时间周期性升高。
2. 请求出现明显停顿或超时。
3. Java 进程 CPU 使用率持续升高。
4. GC 线程占用较多 CPU。
5. 应用吞吐量明显下降。
6. 线程池任务和请求持续堆积。
7. `jstat` 中 `FGC` 数值快速增加。
8. GC 日志中频繁出现 Full GC。
9. 老年代使用率长期处于高位。
10. Full GC 后老年代内存下降不明显。
11. 应用最终出现 `OutOfMemoryError`。
12. 容器健康检查频繁失败。
13. 服务实例被负载均衡摘除。
14. 消息消费速度周期性下降。
15. 日志时间存在明显空白。
16. 数据库连接和下游请求在 GC 停顿期间超时。
17. 应用重启后暂时恢复，运行一段时间后再次出现。
18. 监控中出现规则性的“锯齿形”内存曲线。

Full GC 本身不是故障。少量、低频并且停顿时间可接受的 Full GC 可能属于正常现象。真正需要关注的是 Full GC 的频率、停顿时间、回收效果以及对业务的影响。

## 3. Young GC、Mixed GC 与 Full GC 的区别

### 3.1 Young GC

Young GC 主要回收新生代对象，通常发生频率较高，但停顿时间相对较短。

常见触发原因包括：

- Eden 区空间不足。
- 新对象分配速度较快。
- G1 中达到新生代回收条件。

### 3.2 Mixed GC

Mixed GC 主要出现在 G1 垃圾收集器中。在并发标记完成后，G1 会回收新生代 Region 和部分垃圾较多的老年代 Region。

Mixed GC 不等同于 Full GC。虽然 Mixed GC 也会回收部分老年代，但它通常比 Full GC 更可控。

### 3.3 Full GC

Full GC 通常会回收整个堆或主要堆区域，停顿时间一般比 Young GC 更长。

常见触发原因包括：

- 老年代空间不足。
- 元空间不足。
- 晋升失败。
- 并发回收失败。
- 大对象分配失败。
- 显式调用 `System.gc()`。
- JVM 无法通过普通 GC 获得足够空间。
- 堆碎片导致对象无法分配。
- 垃圾收集器退化到 Full GC。

## 4. Full GC 频繁的常见原因

### 4.1 Java 堆设置过小

如果 `-Xmx` 设置过小，正常业务对象很快就会占满老年代，导致 JVM 频繁触发 Full GC。

常见特征包括：

- Full GC 后能够释放较多空间。
- 内存很快又重新增长。
- Full GC 频率与业务流量相关。
- 增加实例或降低流量后问题缓解。
- Heap Dump 中对象大多属于正常业务对象。

### 4.2 内存泄漏

对象已经失去业务用途，但仍然被 GC Root 引用，无法被回收，导致老年代使用率持续升高。

常见特征包括：

- Full GC 后老年代内存下降很少。
- 内存基线持续升高。
- 应用重启后暂时恢复。
- Heap Dump 中某类对象数量异常。
- 缓存、集合、队列或会话持续增长。
- 最终可能发生 Java 堆 OOM。

### 4.3 对象分配速度过快

应用在短时间内创建大量对象，Young GC 无法及时处理，部分对象被快速晋升到老年代，导致老年代压力增加。

常见场景包括：

- 高频 JSON 序列化。
- 大量字符串拼接。
- 大文件处理。
- 批量数据导入。
- 图片、PDF 或压缩任务。
- 日志中构建超大字符串。
- 大量临时集合。
- 突发流量。

### 4.4 大量对象过早晋升

对象可能因为年龄达到阈值、Survivor 空间不足或动态年龄判断而进入老年代。

常见原因包括：

- Survivor 区过小。
- 对象存活时间较长。
- 新生代设置过小。
- Young GC 频率过高。
- 单次请求产生大量中等生命周期对象。
- 晋升担保或晋升失败。

### 4.5 大对象直接进入老年代

超大数组、字符串、文件内容和集合可能直接进入老年代，或者在 G1 中作为 Humongous Object 分配。

常见场景包括：

- `byte[]` 大数组。
- 一次性读取大文件。
- 超大 JSON。
- 大批量数据库结果。
- 报表导出。
- 图片和视频处理。
- 大对象缓存。
- 大型请求体或响应体。

### 4.6 元空间不足

元空间达到限制时，JVM 可能触发 Full GC，尝试卸载无用的类和 ClassLoader。

常见原因包括：

- `MaxMetaspaceSize` 设置过小。
- 动态生成大量类。
- ClassLoader 泄漏。
- 热部署。
- 大量动态代理。
- 脚本引擎持续加载类。
- 插件反复加载。

### 4.7 显式调用 System.gc()

程序、框架或第三方库可能调用：

```java
System.gc();
```

该调用可能建议 JVM 执行 Full GC。

常见来源包括：

- 业务代码。
- 第三方组件。
- RMI。
- 直接内存清理逻辑。
- 性能测试脚本。
- 管理工具。
- 错误的内存优化代码。

### 4.8 并发模式失败

CMS 可能出现：

```text
concurrent mode failure
```

G1 可能出现：

```text
to-space exhausted
```

```text
evacuation failure
```

```text
G1 Compaction Pause
```

这些情况可能导致垃圾收集器退化为 Full GC。

### 4.9 堆内存碎片

某些垃圾收集器或分配模式可能产生内存碎片。虽然堆中剩余空间总量看起来足够，但无法找到适合大对象的连续区域，从而触发 Full GC。

### 4.10 GC 参数配置不合理

常见问题包括：

- 新生代配置过小。
- 老年代容量不足。
- Survivor 区比例不合理。
- 对象晋升年龄配置不合理。
- G1 Region 大小与大对象特征不匹配。
- G1 并发标记启动过晚。
- GC 线程数量不合理。
- 最大停顿目标设置不符合业务实际。
- 使用不适合当前业务的垃圾收集器。

## 5. 排查前的注意事项

生产环境排查 Full GC 频繁问题时，应注意：

1. 不要立即重启服务。
2. 重启前尽量保存 GC 日志和 JVM 状态。
3. 不要只根据一次 `jstat` 结果下结论。
4. 不要看到 Full GC 就直接增加堆内存。
5. 不要盲目切换垃圾收集器。
6. 不要在未评估影响时生成大型 Heap Dump。
7. 不要执行 `System.gc()` 作为排查手段。
8. 不要只看 GC 次数，还要看停顿时间和回收效果。
9. 应结合业务流量、发布和定时任务分析。
10. JVM 参数修改后必须通过压力测试验证。

建议优先保存：

```bash
date
uptime
free -h
ps -eo pid,user,%cpu,%mem,rss,vsz,cmd --sort=-rss | head -20
jcmd <PID> VM.flags
jcmd <PID> VM.command_line
jcmd <PID> GC.heap_info
jstat -gcutil <PID> 1000 10
jstat -gccause <PID> 1000 10
```

## 6. 第一步：确认当前垃圾收集器

查看 JVM 参数：

```bash
jcmd <PID> VM.flags
```

也可以执行：

```bash
java -XX:+PrintCommandLineFlags -version
```

常见垃圾收集器参数包括：

```text
-XX:+UseSerialGC
-XX:+UseParallelGC
-XX:+UseConcMarkSweepGC
-XX:+UseG1GC
-XX:+UseZGC
-XX:+UseShenandoahGC
```

不同 JDK 版本的默认垃圾收集器可能不同，因此不能仅凭经验判断。

排查前需要确认：

- JDK 版本。
- 垃圾收集器类型。
- 堆内存大小。
- 是否运行在容器中。
- 是否设置停顿时间目标。
- 是否存在自定义 GC 参数。

查看 JDK 版本：

```bash
java -version
```

查看目标进程 JVM 版本：

```bash
jcmd <PID> VM.version
```

## 7. 第二步：使用 jstat 确认 Full GC 频率

执行：

```bash
jstat -gcutil <PID> 1000 10
```

示例输出：

```text
  S0     S1     E      O      M     CCS    YGC    YGCT    FGC    FGCT     GCT
  0.00  80.00  95.00  92.00  88.00  85.00  500   12.50    20    35.20   47.70
```

重点关注：

- `E`：Eden 区使用率。
- `O`：老年代使用率。
- `M`：元空间使用率。
- `CCS`：压缩类空间使用率。
- `YGC`：Young GC 次数。
- `YGCT`：Young GC 总耗时。
- `FGC`：Full GC 次数。
- `FGCT`：Full GC 总耗时。
- `GCT`：GC 总耗时。

判断是否频繁不能只看 `FGC` 累计值，应连续采样观察它在单位时间内增加的速度。

例如，在 10 秒内 `FGC` 增加多次，通常说明 Full GC 已经非常频繁。

## 8. 第三步：查看 Full GC 触发原因

执行：

```bash
jstat -gccause <PID> 1000 10
```

该命令会显示最近一次和当前 GC 原因。

还可以通过 GC 日志查看更准确的触发原因。

常见原因包括：

```text
Allocation Failure
Metadata GC Threshold
System.gc()
Ergonomics
G1 Humongous Allocation
G1 Evacuation Pause
CMS Initial Mark
CMS Final Remark
Last ditch collection
```

需要结合垃圾收集器和前后日志分析，不能仅根据一条原因直接判断根因。

## 9. 第四步：查看 GC 日志

JDK 9 及以上建议配置：

```bash
-Xlog:gc*:file=/data/logs/gc.log:time,uptime,level,tags:filecount=10,filesize=100M
```

JDK 8 常见配置如下：

```bash
-XX:+PrintGCDetails
-XX:+PrintGCDateStamps
-XX:+PrintTenuringDistribution
-Xloggc:/data/logs/gc.log
-XX:+UseGCLogFileRotation
-XX:NumberOfGCLogFiles=10
-XX:GCLogFileSize=100M
```

GC 日志需要重点分析：

1. Full GC 的发生时间。
2. Full GC 的触发原因。
3. Full GC 前后堆内存变化。
4. 单次 Full GC 停顿时间。
5. Full GC 发生频率。
6. 新生代和老年代变化。
7. 元空间变化。
8. 大对象分配。
9. 并发回收是否失败。
10. 是否存在显式 GC。

## 10. 判断 Full GC 回收效果

GC 日志中通常会显示回收前后的内存变化，例如：

```text
Full GC 3800M->3500M(4096M)
```

该信息表示：

- Full GC 前使用约 3800MB。
- Full GC 后仍然使用约 3500MB。
- 堆最大容量约 4096MB。

如果 Full GC 后只能释放少量内存，并且老年代仍然接近上限，通常说明：

- 存在大量长期存活对象。
- 可能存在内存泄漏。
- 堆容量不足。
- 缓存或队列对象无法释放。
- 业务确实需要保留大量数据。

如果 Full GC 后可以释放大量内存，但很快再次增长，通常说明：

- 对象分配速率过快。
- 堆内存过小。
- 流量过高。
- 大批量任务产生大量临时对象。
- 大对象频繁进入老年代。
- Young GC 和对象晋升策略不合理。

## 11. 第五步：查看堆内存状态

执行：

```bash
jcmd <PID> GC.heap_info
```

也可以执行：

```bash
jmap -heap <PID>
```

不同 JDK 版本对 `jmap -heap` 的支持情况不同，优先使用 `jcmd`。

重点关注：

- 最大堆内存。
- 当前堆使用量。
- 新生代大小。
- 老年代大小。
- Region 大小。
- 元空间使用量。
- 当前垃圾收集器。
- 堆各区域使用比例。

还需要确认 JVM 是否正确识别容器内存和 CPU 资源。

## 12. 第六步：分析老年代增长趋势

持续采集：

```bash
jstat -gcutil <PID> 1000 60
```

重点观察 `O` 列：

- Young GC 后老年代是否持续上升。
- Full GC 后老年代是否明显下降。
- 老年代基线是否不断提高。
- 老年代增长是否与流量或任务相关。
- 是否在某个定时任务执行后快速增长。

典型内存泄漏趋势如下：

```text
第一次 Full GC 后：老年代 60%
第二次 Full GC 后：老年代 68%
第三次 Full GC 后：老年代 76%
第四次 Full GC 后：老年代 85%
```

Full GC 后的最低使用率持续升高，说明越来越多对象无法被回收，应重点怀疑内存泄漏或无上限数据结构。

## 13. 第七步：查看对象直方图

执行：

```bash
jcmd <PID> GC.class_histogram
```

保存结果：

```bash
jcmd <PID> GC.class_histogram > /tmp/histogram-1.txt
```

间隔一段时间再次采集：

```bash
jcmd <PID> GC.class_histogram > /tmp/histogram-2.txt
```

重点关注：

- 对象数量持续增加的类。
- 占用内存最大的业务类。
- 大量 `byte[]`。
- 大量 `String`。
- 大量集合节点。
- 大量缓存对象。
- 大量任务和消息对象。
- 大量 ClassLoader。
- 大量 DirectByteBuffer。

常见类型包括：

```text
[B
[C
java.lang.String
java.lang.Object[]
java.util.HashMap$Node
java.util.concurrent.ConcurrentHashMap$Node
```

其中：

- `[B` 表示字节数组。
- `[C` 表示字符数组。
- 对象数组显示为 `[Ljava.lang.Object;`。

基础数组本身通常无法说明业务来源，需要通过 Heap Dump 的引用链继续定位。

## 14. 第八步：生成 Heap Dump

建议提前配置：

```bash
-XX:+HeapDumpOnOutOfMemoryError
-XX:HeapDumpPath=/data/dump
```

如果应用尚未 OOM，但 Full GC 后内存长期处于高位，可以手动生成 Heap Dump：

```bash
jcmd <PID> GC.heap_dump /data/dump/heap.hprof
```

生成前检查磁盘空间：

```bash
df -h /data/dump
```

Heap Dump 可能接近已使用堆内存的大小，并可能引发 Stop The World 和较高磁盘 IO。因此，生产环境操作前必须评估业务影响。

不建议在未评估影响时直接执行：

```bash
jmap -dump:live,format=b,file=/data/dump/heap.hprof <PID>
```

`live` 选项通常会触发 Full GC，可能进一步加重应用停顿。

## 15. 第九步：分析 Heap Dump

可以使用以下工具：

- Eclipse Memory Analyzer。
- VisualVM。
- JProfiler。
- YourKit。
- IntelliJ Profiler。

重点查看：

### 15.1 Histogram

查看对象数量、Shallow Heap 和对象总体占用。

### 15.2 Dominator Tree

查看哪些对象持有大量其他对象。

### 15.3 Retained Heap

表示对象被回收后可以一并释放的内存。定位泄漏时，Retained Heap 通常比对象自身大小更有意义。

### 15.4 Path to GC Roots

查看对象为什么无法被垃圾回收。

常见引用来源包括：

- 静态字段。
- ThreadLocal。
- 活跃线程。
- 类加载器。
- 本地缓存。
- 队列。
- 监听器。
- JNI 引用。

### 15.5 Leak Suspects

自动分析报告可以提供可疑泄漏点，但不能完全依赖工具结论，应结合业务逻辑判断对象是否应该长期存活。

## 16. 判断是否存在内存泄漏

内存泄漏通常具有以下特征：

1. 老年代使用率随时间持续增长。
2. Full GC 后的内存最低点不断升高。
3. Full GC 后只能释放很少空间。
4. Heap Dump 中存在异常大的集合、缓存或队列。
5. 某类业务对象数量持续增加。
6. 应用流量下降后内存仍然不回落。
7. 服务重启后暂时恢复。
8. 最终发生 `OutOfMemoryError`。
9. 同一问题在相似运行周期后重复出现。

常见泄漏来源包括：

- 静态 Map。
- 无界缓存。
- ThreadLocal。
- 未关闭的监听器。
- 线程池任务。
- 消息队列。
- Session。
- ClassLoader。
- 未完成的异步任务。
- 无界连接或请求记录。

## 17. 判断是否为堆容量不足

堆容量不足通常具有以下特征：

1. Full GC 后可以释放较多内存。
2. 业务流量增加后内存快速增长。
3. Heap Dump 中对象大多属于正常业务对象。
4. 降低并发后 Full GC 频率下降。
5. 增加实例后问题缓解。
6. 应用没有明显异常引用链。
7. 单实例正常存活对象接近最大堆容量。
8. 堆大小明显低于业务容量需求。

处理时可以考虑：

- 合理增加 `-Xmx`。
- 增加应用实例。
- 降低单实例流量。
- 优化缓存和对象结构。
- 对数据进行分页和分批处理。
- 降低任务并发度。

增加堆内存前，应确认宿主机或容器存在足够空间，并为元空间、直接内存、线程栈和 JVM 本地内存预留容量。

## 18. 对象分配速度过快排查

可以通过 GC 日志、JFR、Arthas 或 async-profiler 分析对象分配速率和分配热点。

常见分配热点包括：

- JSON 序列化和反序列化。
- 字符串拼接。
- 正则匹配。
- 日志参数构造。
- Stream 中间对象。
- 集合复制。
- 日期格式化。
- 大量装箱对象。
- 图片和文件处理。
- 请求和响应对象。
- 数据库结果转换。

JFR 示例：

```bash
jcmd <PID> JFR.start name=allocation settings=profile duration=60s filename=/tmp/allocation.jfr
```

async-profiler 分配采样示例：

```bash
./asprof -d 30 -e alloc -f /tmp/alloc.html <PID>
```

优化方向包括：

- 减少重复对象创建。
- 避免无意义的中间集合。
- 复用合理且线程安全的对象。
- 避免在日志关闭时仍构造大参数。
- 对大型结果分页。
- 降低批量任务并发度。
- 使用流式处理。
- 减少重复序列化。

## 19. 对象过早晋升排查

对象从新生代晋升到老年代的常见原因包括：

- 对象年龄达到晋升阈值。
- Survivor 区无法容纳存活对象。
- 动态年龄判断触发提前晋升。
- 大对象直接进入老年代。
- Young GC 时老年代担保空间不足。

JDK 8 可以通过以下参数在 GC 日志中观察对象年龄分布：

```bash
-XX:+PrintTenuringDistribution
```

相关参数包括：

```text
-XX:MaxTenuringThreshold
-XX:TargetSurvivorRatio
-XX:SurvivorRatio
```

不应在没有 GC 日志和对象生命周期分析的情况下随意修改晋升年龄。提高晋升年龄可能增加 Survivor 压力，降低晋升年龄则可能增加老年代压力。

## 20. 大对象与 Humongous Object 排查

在 G1 中，如果对象大小超过单个 Region 容量的一半，通常会作为 Humongous Object 处理。

常见大对象包括：

- 超大字节数组。
- 超大字符数组。
- 大型集合。
- 文件完整内容。
- 大型请求或响应。
- 图片数据。
- 压缩数据。
- 大型缓存值。

GC 日志中可能出现：

```text
G1 Humongous Allocation
```

排查方向包括：

- 限制上传文件大小。
- 大文件流式读取。
- 数据库结果分页。
- 降低批量大小。
- 避免多个大对象副本同时存在。
- 将大对象移出本地缓存。
- 检查 G1 Region 大小。
- 减少大数组频繁申请。

查看 Region 大小：

```bash
jcmd <PID> VM.flags | grep -i G1HeapRegionSize
```

调整 Region 大小属于高级 GC 调优，应以对象分布和压力测试结果为依据。

## 21. System.gc() 排查

GC 日志中如果出现：

```text
System.gc()
```

说明程序或第三方组件请求执行显式 GC。

可以在代码中搜索：

```bash
rg "System\.gc|Runtime\.getRuntime\(\)\.gc" .
```

如果无法修改调用方，可以评估以下参数：

```text
-XX:+DisableExplicitGC
```

对于某些需要通过显式 GC 加速直接内存清理的应用，禁用显式 GC 可能产生副作用。

使用 G1 时，也可以根据 JDK 版本评估：

```text
-XX:+ExplicitGCInvokesConcurrent
```

任何参数调整都应结合当前垃圾收集器、JDK 版本和应用特征进行验证。

## 22. 元空间触发 Full GC 排查

如果 GC 原因显示：

```text
Metadata GC Threshold
```

应检查元空间和类加载情况。

查看类加载统计：

```bash
jstat -class <PID> 1000 10
```

查看类加载器：

```bash
jcmd <PID> VM.classloader_stats
```

查看元空间：

```bash
jcmd <PID> GC.heap_info
```

重点关注：

- 已加载类数量是否持续增长。
- 卸载类数量是否很少。
- ClassLoader 数量是否持续增加。
- 是否频繁使用动态代理。
- 是否发生热部署。
- `MaxMetaspaceSize` 是否过小。
- 是否存在类加载器泄漏。

如果类数量稳定，只是元空间限制偏小，可以适当调整限制。如果类和 ClassLoader 持续增长，应优先修复泄漏。

## 23. G1 Full GC 排查

G1 的目标是通过并发标记和 Mixed GC 回收老年代，理想情况下应尽量避免 Full GC。

G1 发生 Full GC 的常见原因包括：

- 并发标记启动过晚。
- 老年代增长速度过快。
- Mixed GC 来不及回收。
- Humongous Object 过多。
- Evacuation Failure。
- To-space Exhausted。
- 堆空间不足。
- 内存碎片。
- 显式 GC。
- 元空间不足。

GC 日志中应重点查找：

```text
Pause Full
G1 Compaction Pause
to-space exhausted
Evacuation Failure
Humongous
Concurrent Start
Concurrent Mark Cycle
```

相关参数可能包括：

```text
-XX:InitiatingHeapOccupancyPercent
-XX:G1ReservePercent
-XX:G1HeapRegionSize
-XX:MaxGCPauseMillis
```

这些参数之间会相互影响，不建议一次修改多个参数。应先明确 Full GC 的直接原因，再进行小范围调整和压力测试。

## 24. CMS Full GC 排查

CMS 常见问题包括：

```text
concurrent mode failure
```

```text
promotion failed
```

`concurrent mode failure` 表示 CMS 并发回收尚未完成，老年代已经无法满足分配需求，只能退化为 Stop The World 的 Full GC。

`promotion failed` 表示 Young GC 时对象无法顺利晋升到老年代。

常见处理方向包括：

- 适当增大老年代。
- 提前启动 CMS 回收。
- 降低对象晋升速度。
- 减少大对象。
- 降低对象分配速率。
- 检查内存泄漏。
- 评估迁移到当前 JDK 支持的其他垃圾收集器。

CMS 已在较新的 JDK 中被移除，因此还应结合 JDK 升级计划评估。

## 25. Parallel GC Full GC 排查

Parallel GC 更关注吞吐量，Full GC 通常使用并行方式压缩老年代。

常见原因包括：

- 老年代空间不足。
- 大量对象晋升。
- 堆设置过小。
- 显式 GC。
- 元空间不足。
- 大对象分配。
- 内存泄漏。

如果业务更加关注低延迟，而 Full GC 停顿无法接受，可以评估 G1、ZGC 或 Shenandoah，但切换垃圾收集器前必须完成压力测试，不能仅依据理论特性直接上线。

## 26. 检查线程和 ThreadLocal

ThreadLocal 泄漏可能导致对象长期被线程池线程持有，从而进入老年代。

线程 Dump：

```bash
jcmd <PID> Thread.print -l > /tmp/thread.txt
```

查看线程数量：

```bash
ps -o nlwp= -p <PID>
```

Heap Dump 中常见引用路径：

```text
Thread
  → threadLocals
  → ThreadLocalMap
  → Entry
  → value
```

正确使用方式：

```java
threadLocal.set(context);
try {
    process();
} finally {
    threadLocal.remove();
}
```

如果线程数量持续增长，除了线程栈占用本地内存外，线程持有的 ThreadLocal、任务对象和上下文也可能增加堆内存压力。

## 27. 检查本地缓存

常见无界缓存示例：

```java
private static final Map<String, Object> CACHE = new ConcurrentHashMap<>();
```

排查时应确认：

- 缓存是否有最大容量。
- 是否设置过期时间。
- 是否设置淘汰策略。
- Key 是否持续变化。
- 缓存值是否过大。
- 缓存是否保存完整文件。
- 缓存对象是否可以通过外部缓存替代。
- 缓存命中率是否值得其内存成本。

建议监控：

- 缓存条目数量。
- 缓存占用估算。
- 命中率。
- 淘汰数量。
- 加载耗时。
- 加载失败次数。

## 28. 检查队列和任务堆积

无界队列可能导致大量任务对象长期存活并晋升到老年代。

常见结构包括：

- `LinkedBlockingQueue` 未设置容量。
- `ConcurrentLinkedQueue`。
- 异步日志队列。
- 消息发送队列。
- 线程池任务队列。
- 事件队列。
- 批处理结果队列。

应检查：

- 生产速度是否大于消费速度。
- 下游服务是否变慢。
- 线程池是否阻塞。
- 队列是否有容量上限。
- 队列满后的拒绝策略。
- 单个任务对象大小。
- 失败任务是否重复入队。
- 队列长度是否与老年代增长同步。

## 29. 检查批量任务和大查询

以下操作可能快速产生大量长期对象：

- 数据库查询不分页。
- 一次性读取全部文件。
- 批量导出全量数据。
- 全量缓存预热。
- 大型报表生成。
- 批量图片和 PDF 处理。
- 一次消费大量消息。
- 全量索引重建。

优化方向包括：

- 分页查询。
- 流式读取和写入。
- 分批提交。
- 控制任务并发度。
- 减少中间对象。
- 及时清理批次引用。
- 将大任务拆分到独立实例。
- 限制单次数据规模。

## 30. 检查容器内存限制

查看 Kubernetes 资源配置：

```bash
kubectl get pod <pod-name> -n <namespace> -o yaml
```

查看内存使用：

```bash
kubectl top pod <pod-name> -n <namespace>
```

容器内存限制应覆盖：

```text
Java Heap
+ Metaspace
+ Direct Memory
+ Thread Stack
+ Code Cache
+ GC Native Memory
+ JNI 与本地库
```

如果容器内存较小，JVM 堆也会被限制。堆空间过小可能导致 Full GC 频繁。

如果堆配置过大，虽然 Full GC 可能减少，但容器可能因为总内存超过限制而发生 `OOMKilled`。

因此，堆大小不能脱离容器总内存单独配置。

## 31. Full GC 频率是否正常

没有适用于所有应用的固定 Full GC 正常阈值。

判断是否异常需要结合：

- 业务延迟要求。
- 单次 Full GC 停顿时间。
- Full GC 间隔。
- 堆容量。
- 对象分配速率。
- 回收前后内存变化。
- 应用吞吐量。
- 业务高峰和低峰。
- 垃圾收集器类型。
- 是否影响健康检查和请求。

例如：

- 每天一次、停顿 100 毫秒，可能影响很小。
- 每分钟一次、每次停顿 5 秒，通常已经严重影响业务。
- Full GC 很少，但单次停顿几十秒，也需要处理。
- Full GC 频繁，但应用使用小堆且停顿极短，仍需结合业务判断。

## 32. 常见临时处理措施

当 Full GC 频繁已经严重影响业务时，可以采取：

1. 对高内存接口进行限流。
2. 暂停大文件和批量任务。
3. 降低消息消费并发度。
4. 暂停异常定时任务。
5. 清理可以安全清理的业务队列。
6. 关闭异常增长的本地缓存入口。
7. 增加应用实例分担流量。
8. 将部分流量切换到健康实例。
9. 临时提高容器内存限制。
10. 保存 GC 日志和 Heap Dump 后滚动重启。
11. 禁止无限重试和任务重复提交。
12. 对非核心功能进行降级。

临时措施用于恢复业务，后续仍需分析 Full GC 的根本原因。

## 33. 常见永久解决方案

### 33.1 修复内存泄漏

- 清理无效长期引用。
- 限制静态集合大小。
- 为缓存增加容量和过期时间。
- 正确清理 ThreadLocal。
- 修复 ClassLoader 泄漏。
- 注销无效监听器。
- 关闭未释放的资源。
- 清理无效 Session。
- 限制队列长度。

### 33.2 降低对象分配

- 减少重复序列化。
- 避免不必要的字符串拼接。
- 避免创建无意义的临时集合。
- 对大型结果进行分页。
- 使用流式文件处理。
- 减少对象重复复制。
- 降低批量处理并发度。
- 优化高频代码路径。

### 33.3 调整堆内存

如果确认是正常容量不足，可以根据实际存活对象容量、业务峰值和容器限制合理增加：

```text
-Xms
-Xmx
```

生产环境通常可以将 `-Xms` 和 `-Xmx` 设置为相同值，以减少运行过程中堆扩容带来的影响，但是否采用仍需结合部署环境和资源利用策略。

### 33.4 调整代际配置

根据对象生命周期和 GC 日志调整：

- 新生代大小。
- Survivor 区。
- 晋升年龄。
- 老年代容量。
- G1 Region。
- 并发标记启动阈值。

代际参数相互影响，应一次只调整少量参数并进行对比测试。

### 33.5 选择合适的垃圾收集器

常见选择方向包括：

- Parallel GC：更关注吞吐量。
- G1：平衡吞吐量和停顿时间。
- ZGC：适合大堆和低停顿场景。
- Shenandoah：关注低停顿。

垃圾收集器选择与 JDK 版本、堆大小、CPU 资源、对象分配特征和延迟要求有关，必须通过实际压力测试决定。

## 34. GC 参数调整原则

GC 调优应遵循以下原则：

1. 先确认应用是否存在内存泄漏。
2. 先优化代码和对象生命周期。
3. 明确吞吐量和停顿时间目标。
4. 保留完整的调优前基线。
5. 一次只调整少量参数。
6. 使用相同流量和数据规模对比。
7. 同时观察 CPU、内存、GC 和业务延迟。
8. 不直接复制其他项目的 JVM 参数。
9. 不使用过多相互冲突的参数。
10. 优先使用垃圾收集器的自适应能力。

## 35. 不建议直接执行的操作

### 35.1 不建议只增大堆内存

如果存在内存泄漏，增加堆只能推迟问题发生。更大的堆还可能带来更长的 Full GC 停顿和更大的 Heap Dump。

### 35.2 不建议主动调用 System.gc()

主动调用 `System.gc()` 可能直接触发 Full GC，增加停顿，并不能解决对象被长期引用的问题。

### 35.3 不建议盲目禁用 Full GC

Full GC 是 JVM 在内存压力下的保护机制。隐藏或延迟 Full GC 不代表问题已经解决，最终可能直接发生 OOM。

### 35.4 不建议一次修改大量参数

同时修改堆大小、新生代比例、晋升年龄和垃圾收集器后，即使结果变化，也很难判断哪个参数真正产生作用。

### 35.5 不建议忽略业务指标

GC 数据正常不代表业务一定正常，GC 数据异常也不一定已经影响业务。必须结合接口延迟、吞吐量、超时率和任务处理速度分析。

## 36. 监控与预防建议

建议持续监控以下指标：

- Java 堆使用量。
- 新生代使用率。
- 老年代使用率。
- 元空间使用率。
- Young GC 次数和耗时。
- Full GC 次数和耗时。
- GC 总耗时占比。
- Full GC 后老年代使用量。
- 对象分配速率。
- 对象晋升速率。
- Humongous Object 数量。
- Java 进程 RSS。
- 容器内存使用率。
- 线程数量。
- 本地缓存条目数量。
- 线程池队列长度。
- 消息积压数量。
- 接口响应时间。
- 请求超时率。
- OOM 次数。

推荐告警条件包括：

- Full GC 在短时间内连续发生。
- Full GC 单次停顿时间超过业务要求。
- Full GC 后老年代仍高于危险阈值。
- 老年代最低使用率持续升高。
- 元空间使用率持续增长。
- GC 总耗时占比明显升高。
- 对象分配或晋升速度突然上升。
- Full GC 与接口超时同步出现。
- 容器内存接近限制。
- Heap Dump 自动生成失败。

## 37. 推荐排查流程

JVM Full GC 频繁时，可以按照以下顺序排查：

1. 使用 `jcmd` 确认 JDK 版本和垃圾收集器。
2. 使用 `jstat -gcutil` 确认 Full GC 是否持续增加。
3. 使用 `jstat -gccause` 查看近期 GC 原因。
4. 收集并分析 GC 日志。
5. 统计 Full GC 频率和停顿时间。
6. 对比 Full GC 前后的堆内存变化。
7. 观察 Full GC 后老年代最低点是否持续升高。
8. 使用 `jcmd GC.heap_info` 查看堆布局。
9. 使用对象直方图查看高占用对象。
10. 必要时生成 Heap Dump。
11. 使用 MAT 分析 Dominator Tree 和引用链。
12. 判断是否存在内存泄漏。
13. 检查缓存、队列、ThreadLocal 和 ClassLoader。
14. 检查对象分配速度和大对象。
15. 检查元空间、显式 GC 和晋升失败。
16. 检查业务流量、批量任务和最近发布。
17. 检查容器内存限制和 JVM 堆配置。
18. 采取限流、暂停任务、扩容或滚动重启等临时措施。
19. 修复代码或调整 JVM 参数。
20. 使用压力测试和持续监控验证效果。

## 38. 常用排查命令汇总

```bash
# 查看 Java 进程
jps -lv
ps -ef | grep java

# 查看目标 JVM 版本
jcmd <PID> VM.version

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

# 查看堆各区域容量
jstat -gc <PID> 1000 10

# 查看类加载统计
jstat -class <PID> 1000 10

# 查看对象直方图
jcmd <PID> GC.class_histogram

# 保存对象直方图
jcmd <PID> GC.class_histogram > /tmp/histogram.txt

# 生成 Heap Dump
jcmd <PID> GC.heap_dump /data/dump/heap.hprof

# 查看类加载器统计
jcmd <PID> VM.classloader_stats

# 查看线程栈
jcmd <PID> Thread.print -l > /tmp/thread.txt

# 查看线程数量
ps -o nlwp= -p <PID>

# 启动 JFR 采样
jcmd <PID> JFR.start name=allocation settings=profile duration=60s filename=/tmp/allocation.jfr

# 查看系统内存
free -h

# 查看 Java 进程内存
cat /proc/<PID>/status

# 查看高内存进程
ps -eo pid,user,%mem,rss,vsz,cmd --sort=-rss | head -20

# 查看容器资源
docker stats

# 查看 Kubernetes Pod 资源
kubectl top pod <pod-name> -n <namespace>

# 查看 Pod 资源限制
kubectl get pod <pod-name> -n <namespace> -o yaml

# 查看磁盘空间
df -hT
```

## 39. 排查结论模板

### 故障现象

Java 应用接口周期性出现响应超时，监控显示老年代使用率长期超过 90%，Full GC 每分钟发生多次，单次停顿时间达到数秒。

### 故障确认

通过 `jstat -gcutil` 确认 `FGC` 数值快速增长，GC 日志显示每次 Full GC 后老年代只能从约 95% 降低到 90%，回收效果很差。

### 根本原因

通过 Heap Dump 的 Dominator Tree 和 Path to GC Roots 分析发现，一个静态缓存 Map 持有大量报表结果对象。缓存没有设置最大容量和过期时间，不同查询条件不断产生新的缓存条目，使老年代中的长期存活对象持续增加，最终导致频繁 Full GC。

### 临时处理

暂停报表查询入口，对相关接口进行限流，在保留 Heap Dump 和 GC 日志后滚动重启异常实例，恢复核心业务。

### 永久修复

将无界缓存替换为具有最大容量和过期淘汰策略的缓存组件，限制单条报表缓存数据大小，并对大结果集使用分页处理。同时增加缓存条目数、老年代使用率和 Full GC 频率监控。

### 验证结果

修复后使用相同数据规模进行持续压力测试，缓存容量保持在配置上限内，老年代使用率能够在 GC 后正常回落，测试期间未发生 Full GC，接口响应时间恢复稳定。

## 40. 总结

JVM Full GC 频繁排查的重点不是单纯减少 Full GC 次数，而是明确 Full GC 为什么发生、回收了多少内存以及是否影响业务。

首先应通过 `jstat` 和 GC 日志确认 Full GC 的频率、停顿时间和触发原因，再观察 Full GC 前后的老年代变化。如果 Full GC 后内存下降很少，并且最低使用率持续升高，应重点怀疑内存泄漏；如果 Full GC 能释放大量内存，但内存很快再次增长，则应检查堆容量、对象分配速率、大对象和业务并发。

对象直方图可以帮助快速发现高占用类型，Heap Dump 则可以通过 Dominator Tree、Retained Heap 和 GC Root 引用链定位对象无法释放的根本原因。对于 G1、CMS 和 Parallel GC，还需要结合各自的日志特征判断并发回收失败、晋升失败、大对象和堆碎片问题。

临时扩容、限流和重启可以恢复服务，但最终应通过修复内存泄漏、限制缓存和队列、优化对象分配、调整批量任务以及合理配置 JVM 内存解决问题。任何 GC 参数调整都应建立在真实监控和压力测试基础上。