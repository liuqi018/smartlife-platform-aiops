# Linux 内存不足与 OOM 排查

## 1. 问题概述

Linux 内存不足是指系统可用内存持续下降，已经无法满足操作系统或应用程序新的内存分配请求。OOM 是 Out of Memory 的缩写，表示系统或进程已经没有足够的内存可以使用。

当物理内存和 Swap 交换空间都无法满足新的内存分配请求时，Linux 内核可能触发 OOM Killer。OOM Killer 会根据进程的内存占用、运行状态、优先级和 `oom_score_adj` 等信息，选择一个或多个进程强制终止，从而释放内存，尽量保证操作系统能够继续运行。

需要注意，内存不足不一定会立即触发 OOM。在发生 OOM 之前，系统通常会先出现可用内存持续下降、Swap 使用率升高、磁盘 IO 增大、接口响应变慢、进程频繁卡顿等现象。因此，排查时既要确认是否已经发生 OOM，也要关注系统是否正处于持续的内存压力之中。

## 2. 常见问题现象

Linux 系统出现内存不足或 OOM 时，通常会出现以下现象：

1. 应用程序突然退出或被系统强制终止。
2. 服务日志中出现 `Killed`、`Out of memory`、`Cannot allocate memory` 等信息。
3. 执行命令时提示无法分配内存。
4. SSH 登录、命令执行或接口响应明显变慢。
5. 系统负载升高，但 CPU 使用率不一定很高。
6. Swap 使用率持续升高，并伴随频繁的磁盘读写。
7. Java 服务出现 `java.lang.OutOfMemoryError`。
8. Docker 或 Kubernetes 容器状态显示 `OOMKilled`。
9. `dmesg` 或系统日志中出现 OOM Killer 相关记录。
10. 数据库、中间件或业务服务频繁重启。

系统发生 OOM 时，内核日志中可能出现以下内容：

```text
Out of memory: Kill process 12345 (java) score 987 or sacrifice child
Killed process 12345 (java) total-vm:8192000kB, anon-rss:6291456kB
```

某些情况下，终端或应用日志中可能只出现：

```text
Killed
```

需要注意，单独出现 `Killed` 并不能完全说明发生了 OOM，也可能是进程被管理员、监控脚本或其他程序发送了 `SIGKILL` 信号。因此，还需要结合内核日志、系统日志和进程退出码进一步确认。

## 3. Linux 内存指标说明

排查内存问题前，需要理解 Linux 中几个常见的内存指标。

### 3.1 total

`total` 表示系统可以管理的物理内存总量。该值通常略小于服务器实际安装的内存，因为部分内存会被内核、硬件映射和其他系统组件占用。

### 3.2 used

`used` 表示已经被使用的内存，但不能简单认为 `used` 越高，系统内存就越紧张。Linux 会尽可能利用空闲内存作为文件缓存，以提高文件访问性能，因此较高的内存使用率不一定代表系统存在异常。

### 3.3 free

`free` 表示完全没有被使用的物理内存。Linux 中 `free` 较低是一种常见现象，不能仅根据该指标判断系统是否内存不足。

### 3.4 buff/cache

`buff/cache` 表示缓冲区和文件缓存占用的内存。当应用程序需要更多内存时，其中一部分通常可以被操作系统回收。

### 3.5 available

`available` 表示在不发生大量 Swap 的情况下，系统预计还可以提供给新进程使用的内存。判断系统是否存在内存压力时，`available` 比 `free` 更有参考价值。

### 3.6 Swap

Swap 是磁盘上的交换空间。当物理内存紧张时，Linux 可以将暂时不活跃的内存页写入 Swap。

Swap 能够缓解短期内存压力，但其读写性能远低于物理内存。如果 Swap 使用量持续增长，并且系统频繁进行换入和换出，说明系统已经存在明显的内存压力。

## 4. 内存不足与 OOM 的常见原因

### 4.1 应用程序存在内存泄漏

内存泄漏是最常见的原因之一。应用程序不断申请内存，但使用结束后没有正确释放，导致进程占用的内存持续增长。

常见场景包括：

- Java 对象长期被引用，无法被垃圾回收。
- C 或 C++ 程序申请内存后没有释放。
- Python 程序长期保存大量对象或集合数据。
- 线程池、连接池或队列中的对象不断堆积。
- 本地缓存没有设置容量上限和淘汰策略。
- 请求上下文、日志对象或会话数据没有及时清理。
- 文件流、数据库连接或网络连接没有正常关闭。

### 4.2 瞬时流量或任务量过大

业务流量突然增加时，并发请求、线程、连接和临时对象的数量都会增长，可能导致短时间内内存占用快速上升。

常见场景包括：

- 大量用户同时访问系统。
- 批量导入、导出或报表任务集中执行。
- 消息队列积压后集中消费。
- 一次性读取过大的文件或数据库查询结果。
- 接口没有限制上传文件大小。
- 异步任务提交速度远高于处理速度。
- 大量请求在内存队列中等待处理。

### 4.3 应用程序内存参数配置不合理

应用程序配置的内存上限超过服务器实际可用内存，也可能触发系统级 OOM。

例如，服务器共有 8GB 内存，却为 Java 堆配置：

```bash
-Xms6g -Xmx6g
```

除了 Java 堆，JVM 还需要使用元空间、线程栈、直接内存、Code Cache、GC 数据结构和本地库内存。因此，不能将绝大部分物理内存全部配置给 Java 堆。

### 4.4 进程或线程数量过多

每个进程和线程都需要占用一定内存。如果应用程序无限创建线程，或者服务器上同时运行了过多服务，即使单个线程占用的内存不高，总内存也可能被耗尽。

Java 中每个线程通常都需要分配线程栈，线程栈大小由 `-Xss` 参数控制。线程数量过多时，仅线程栈就可能占用大量内存。

### 4.5 文件缓存占用较高

Linux 会使用空闲内存作为 Page Cache，以提高文件访问性能。正常情况下，这部分内存可以根据系统压力自动回收。

但是，如果应用频繁读写大量文件，或者存在脏页回写不及时、内存映射文件使用异常等情况，也可能造成明显的内存压力。

### 4.6 Swap 未配置或空间不足

如果服务器没有配置 Swap，物理内存耗尽后更容易直接触发 OOM。Swap 过小或已经耗尽，也会降低系统应对突发内存使用的能力。

但是，Swap 只能作为短期缓冲手段，不能代替物理内存。长期依赖 Swap 会导致系统性能严重下降。

### 4.7 容器内存限制过小

Docker 或 Kubernetes 容器可能设置独立的内存限制。即使宿主机还有可用内存，只要容器内存使用量达到限制，容器中的进程仍然可能被终止。

例如：

```yaml
resources:
  requests:
    memory: 512Mi
  limits:
    memory: 1Gi
```

如果 Java 服务实际需要 1.5GB 内存，该容器就可能出现 `OOMKilled`。

### 4.8 内核内存占用异常

除了普通用户进程，Linux 内核也会占用内存，例如 Slab、页表、网络缓冲区和内核模块等。某些驱动、网络连接或内核模块异常时，可能造成内核内存持续增长。

## 5. 排查前的注意事项

发生内存不足时，不要立即重启服务器或随意清理缓存。重启虽然可能暂时恢复服务，但会丢失重要的故障现场信息，使后续无法准确定位根本原因。

建议优先保存以下信息：

```bash
date
uptime
free -h
vmstat 1 10
ps aux --sort=-%mem | head -20
top -b -n 1 | head -40
dmesg -T | tail -200
```

如果系统已经严重卡顿，应先保证核心业务可用，再进行深入分析。对于非核心且内存占用异常的进程，可以在确认业务影响后执行限流、停止任务或重启服务。

## 6. 查看系统整体内存使用情况

首先执行：

```bash
free -h
```

示例输出：

```text
               total        used        free      shared  buff/cache   available
Mem:            15Gi         12Gi       500Mi       200Mi       2.5Gi       1.8Gi
Swap:          2.0Gi        1.8Gi       200Mi
```

重点关注以下内容：

- `available` 是否持续偏低。
- Swap 是否已经大量使用。
- 内存是否在短时间内快速下降。
- 内存紧张是否与业务高峰时间一致。
- `buff/cache` 是否可以被正常回收。

如果 `free` 很低，但是 `available` 较高，通常不代表存在严重的内存不足。如果 `available` 很低，同时 Swap 使用率很高，则说明系统内存压力较大。

## 7. 确认是否发生 OOM

### 7.1 查看内核日志

执行：

```bash
dmesg -T | grep -iE "out of memory|oom|killed process"
```

CentOS 或 RHEL 系统可以查看：

```bash
grep -iE "out of memory|oom|killed process" /var/log/messages
```

Ubuntu 或 Debian 系统可以查看：

```bash
grep -iE "out of memory|oom|killed process" /var/log/syslog
```

使用 systemd 的系统可以执行：

```bash
journalctl -k | grep -iE "out of memory|oom|killed process"
```

查看指定时间范围内的内核日志：

```bash
journalctl -k --since "2026-07-21 10:00:00" --until "2026-07-21 11:00:00"
```

如果日志中同时包含 `Out of memory` 和 `Killed process`，基本可以确认发生了系统级 OOM。

### 7.2 确认被终止的进程

重点关注 OOM 日志中的以下信息：

- 被终止进程的 PID。
- 进程名称。
- OOM 发生的具体时间。
- `anon-rss`，即匿名常驻内存。
- `file-rss`，即文件映射常驻内存。
- `shmem-rss`，即共享内存。
- `oom_score_adj`，即 OOM 评分调整值。

通过这些信息可以判断哪个进程被终止，以及该进程在 OOM 发生时占用了多少内存。

需要注意，被 OOM Killer 终止的进程不一定是导致内存不足的唯一原因。服务器上可能存在多个高内存进程，只是其中一个进程的 OOM 评分较高，因此被优先终止。

## 8. 定位高内存进程

### 8.1 使用 ps 查看高内存进程

执行：

```bash
ps aux --sort=-%mem | head -20
```

也可以按照 RSS 排序：

```bash
ps -eo pid,ppid,user,%mem,rss,vsz,cmd --sort=-rss | head -20
```

其中：

- `RSS` 表示进程实际驻留在物理内存中的大小。
- `VSZ` 表示进程可以访问的虚拟内存总量。
- `%MEM` 表示进程占物理内存的比例。

不能只根据 VSZ 判断进程是否真实占用了大量内存。某些程序会预留较大的虚拟地址空间，但不一定全部映射到物理内存。排查时应重点关注 RSS、PSS 和进程内存的变化趋势。

### 8.2 使用 top 查看进程

执行：

```bash
top
```

进入 `top` 后，可以按大写字母 `M`，按照内存使用率进行排序。

如果系统安装了 `htop`，也可以执行：

```bash
htop
```

`htop` 可以更加直观地查看进程、线程和内存使用情况。

### 8.3 查看指定进程的内存信息

执行：

```bash
cat /proc/<PID>/status
```

例如：

```bash
cat /proc/12345/status
```

重点查看：

```text
VmPeak
VmSize
VmHWM
VmRSS
RssAnon
RssFile
RssShmem
VmSwap
Threads
```

各指标含义如下：

- `VmPeak`：进程历史虚拟内存峰值。
- `VmSize`：进程当前虚拟内存大小。
- `VmHWM`：进程历史物理内存峰值。
- `VmRSS`：进程当前实际物理内存占用。
- `VmSwap`：该进程使用的 Swap 大小。
- `Threads`：进程当前线程数量。

还可以执行：

```bash
pmap -x <PID> | tail -20
```

该命令可以查看进程的内存映射以及各内存区域的占用情况。

## 9. 观察进程内存变化趋势

一次内存快照只能说明当前状态，无法证明进程是否存在内存泄漏。因此，需要连续观察进程 RSS 的变化。

```bash
watch -n 5 'ps -eo pid,user,%mem,rss,vsz,cmd --sort=-rss | head -20'
```

也可以执行：

```bash
pidstat -r -p <PID> 5
```

排查时重点关注：

- 进程 RSS 是否持续增长且长期不下降。
- 内存增长是否与请求量、任务量或数据量相关。
- 业务流量下降后内存是否能够回落。
- 是否在固定的定时任务执行后出现内存突增。
- 进程重启后内存是否从较低值重新持续增长。
- Full GC 后内存是否能够明显下降。

如果进程内存随着时间持续增长，并且在业务低峰期也没有下降，应重点怀疑内存泄漏、任务积压或无上限缓存。

## 10. 检查 Swap 和内存换页

查看当前 Swap：

```bash
swapon --show
```

查看 Swap 总体使用情况：

```bash
free -h
```

观察内存换入和换出：

```bash
vmstat 1 10
```

重点关注以下指标：

- `si`：每秒从 Swap 读入物理内存的数据量。
- `so`：每秒从物理内存写入 Swap 的数据量。
- `r`：等待 CPU 的进程数量。
- `b`：处于不可中断睡眠状态的进程数量。
- `wa`：CPU 等待磁盘 IO 的时间比例。

如果 `si` 和 `so` 长时间持续不为 0，说明系统正在频繁进行内存交换，可能已经发生 Swap 抖动。此时系统即使没有触发 OOM，也可能出现严重卡顿。

Swap 已经被使用并不代表当前一定存在内存不足。Linux 将内存页写入 Swap 后，即使后续物理内存已经恢复充足，也不一定会立即将所有数据换回。因此，应结合 `si`、`so` 和 `MemAvailable` 判断当前是否仍然存在内存压力。

## 11. 检查 Page Cache 和内核内存

查看详细内存信息：

```bash
cat /proc/meminfo
```

重点关注：

```text
MemAvailable
Buffers
Cached
SwapCached
Active
Inactive
Dirty
Writeback
Slab
SReclaimable
SUnreclaim
PageTables
Committed_AS
CommitLimit
```

如果 `Slab` 或 `SUnreclaim` 异常高，可以进一步执行：

```bash
slabtop
```

如果 `Dirty` 和 `Writeback` 长期较高，可能存在大量脏页等待写回磁盘，需要结合磁盘 IO、文件系统和存储性能继续排查。

不建议将以下命令作为常规的内存问题解决方案：

```bash
echo 3 > /proc/sys/vm/drop_caches
```

该操作会清理 Page Cache、目录项和 inode 缓存，可能造成后续磁盘 IO 突增和性能下降。Linux 会根据系统内存压力自动回收可回收缓存，除非在明确的测试或特殊故障处理中，否则一般不应手动清理缓存。

## 12. 排查 Java 进程内存问题

如果高内存进程是 Java 服务，需要同时排查 Java 堆内存和堆外内存。

### 12.1 查看 JVM 启动参数

```bash
jcmd <PID> VM.flags
```

```bash
jcmd <PID> VM.command_line
```

重点关注：

```text
-Xms
-Xmx
-Xss
-XX:MaxMetaspaceSize
-XX:MaxDirectMemorySize
```

### 12.2 查看 Java 堆内存

```bash
jcmd <PID> GC.heap_info
```

也可以执行：

```bash
jstat -gcutil <PID> 1000 10
```

重点观察：

- 新生代和老年代使用率。
- Full GC 次数及增长速度。
- Full GC 的执行频率。
- 垃圾回收后内存是否能够下降。
- 老年代使用率是否持续接近上限。

如果 Full GC 后老年代占用仍然很高，并且随着时间持续增长，可能存在对象泄漏或长期存活对象过多的问题。

### 12.3 查看对象占用情况

```bash
jcmd <PID> GC.class_histogram
```

可以通过对象统计结果查看哪些类型的对象数量最多、占用内存最大。

如果需要进一步分析，可以生成堆转储文件：

```bash
jcmd <PID> GC.heap_dump /data/dump/heap.hprof
```

建议提前配置：

```bash
-XX:+HeapDumpOnOutOfMemoryError
-XX:HeapDumpPath=/data/dump
```

生成 Heap Dump 前必须检查磁盘剩余空间，并评估对生产服务的影响。生成堆转储可能造成 Stop The World、服务卡顿或磁盘空间被占满。

Heap Dump 可以使用 Eclipse MAT、VisualVM 等工具分析，重点检查：

- 占用内存最多的对象。
- 大型集合对象。
- GC Root 引用链。
- 重复字符串。
- 未淘汰的缓存对象。
- 未释放的会话、连接和请求对象。

### 12.4 检查 Java 堆外内存

如果 Java 进程 RSS 很高，但 Java 堆使用率并不高，应重点检查：

- DirectByteBuffer。
- Netty 直接内存。
- 线程栈。
- Metaspace。
- JNI 或本地库内存。
- 内存映射文件。
- Code Cache。

如果 JVM 启动时启用了 Native Memory Tracking，可以执行：

```bash
jcmd <PID> VM.native_memory summary
```

## 13. 排查 Docker 容器 OOM

查看容器状态：

```bash
docker ps -a
```

查看容器是否因 OOM 被终止：

```bash
docker inspect <容器名称或ID> --format '{{.State.OOMKilled}}'
```

查看容器退出码：

```bash
docker inspect <容器名称或ID> --format '{{.State.ExitCode}}'
```

退出码 `137` 通常表示进程收到了 `SIGKILL` 信号，可能与 OOM 有关，但仍然需要结合 `OOMKilled` 状态和宿主机日志确认。

查看容器实时资源使用情况：

```bash
docker stats
```

查看容器内存限制：

```bash
docker inspect <容器名称或ID> --format '{{.HostConfig.Memory}}'
```

排查时需要区分以下情况：

- 宿主机整体内存不足。
- 单个容器达到内存上限。
- 容器中的应用程序存在内存泄漏。
- Java 堆配置接近或超过容器限制。
- 多个容器同时争抢宿主机内存。

## 14. 排查 Kubernetes Pod OOM

查看 Pod 状态：

```bash
kubectl get pods -n <namespace>
```

查看 Pod 详细信息：

```bash
kubectl describe pod <pod-name> -n <namespace>
```

如果出现以下信息，说明容器曾因超过内存限制而被终止：

```text
Reason: OOMKilled
Exit Code: 137
```

查看 Pod 资源配置：

```bash
kubectl get pod <pod-name> -n <namespace> -o yaml
```

重点检查：

```yaml
resources:
  requests:
    memory: 512Mi
  limits:
    memory: 1Gi
```

`requests` 用于调度时声明需要的内存，`limits` 表示容器可以使用的内存上限。当容器内存达到 `limits` 后，容器中的进程可能被系统终止。

如果集群部署了 Metrics Server，可以执行：

```bash
kubectl top pod -n <namespace>
```

对于 Java 服务，必须保证 JVM 最大堆内存小于容器内存限制，并为堆外内存、线程栈和 JVM 自身开销预留足够空间。

## 15. 常见解决方案

### 15.1 临时止损措施

当系统已经发生严重内存不足时，可以根据业务影响采取以下措施：

1. 限制接口流量或暂停非核心任务。
2. 停止异常的批量任务、报表任务或导入任务。
3. 重启确认存在内存泄漏的非核心服务。
4. 扩容物理内存或提高容器内存上限。
5. 临时增加 Swap，缓解突发内存压力。
6. 降低应用程序并发数、线程数或批处理大小。
7. 将部分服务迁移到其他节点。
8. 对消息消费进行限速，避免任务集中进入内存。
9. 暂停不必要的定时任务。
10. 对非核心服务进行降级或关闭。

临时措施只能用于恢复服务，不能代替根本原因分析。

### 15.2 修复应用程序内存泄漏

应根据内存分析结果定位未释放的对象、集合、缓存、连接或线程，并修改程序逻辑。

常见改进包括：

- 为缓存设置最大容量和过期时间。
- 避免使用无上限的集合和任务队列。
- 及时关闭文件、网络连接和数据库连接。
- 清理无效的会话数据。
- 避免一次性加载全部数据。
- 对大文件采用流式读取。
- 对数据库查询增加分页和结果数量限制。
- 限制异步任务提交速度。
- 避免重复创建线程池和客户端对象。
- 对批量任务设置合理的批次大小。

### 15.3 合理调整 JVM 参数

JVM 参数应结合服务器物理内存、容器限制、线程数和堆外内存综合配置。

例如，容器内存限制为 4GB 时，不建议直接设置：

```bash
-Xmx4g
```

因为 JVM 除了堆内存，还需要使用元空间、直接内存、线程栈、Code Cache 和本地内存。最大堆内存应小于容器内存限制，并为其他内存区域预留足够空间。

### 15.4 合理配置 Swap

检查当前 Swap：

```bash
swapon --show
```

如果系统完全没有 Swap，可以根据服务器用途配置适量 Swap，用于缓解短时间的内存峰值。

但是，对于延迟敏感型应用，不能依赖 Swap 解决长期内存不足。根本解决方案仍然是降低内存使用、修复内存泄漏或增加物理内存。

### 15.5 设置进程资源限制

可以通过 systemd、容器资源限制或 `ulimit` 等方式限制非核心服务的资源使用，避免单个异常进程耗尽整台服务器的内存。

查看进程的 OOM 评分：

```bash
cat /proc/<PID>/oom_score
```

查看进程的 OOM 评分调整值：

```bash
cat /proc/<PID>/oom_score_adj
```

不建议随意将大量进程设置为不允许被 OOM Killer 终止，否则可能导致系统内存耗尽后完全失去响应。

## 16. 监控与预防建议

### 16.1 建立内存监控告警

建议重点监控以下指标：

- `MemAvailable`。
- 物理内存使用率。
- Swap 使用率。
- Swap 换入和换出速率。
- 进程 RSS。
- 容器内存使用率。
- Pod 的 `OOMKilled` 次数。
- Java 堆和非堆内存。
- Full GC 次数和耗时。
- 进程线程数量。
- Page Cache 和 Slab。
- 系统 OOM 日志数量。
- 服务异常重启次数。

告警不能只看内存使用率。Linux 文件缓存可能使内存使用率长期较高，因此应同时关注 `MemAvailable`、Swap 活跃度和应用进程 RSS。

### 16.2 保留历史监控数据

内存泄漏通常需要通过变化趋势判断。建议使用 Prometheus、Grafana、Zabbix 或其他监控系统保存至少数天到数周的历史数据。

重点分析：

- 内存是否呈持续上升趋势。
- 每天是否在固定时间出现内存峰值。
- 服务发布后内存是否开始异常。
- 内存增长是否与流量、消息积压或定时任务相关。
- Full GC 后内存是否能够恢复。
- 容器重启后相同问题是否重复出现。

### 16.3 做好容量规划

上线前应通过压力测试估算：

- 单实例的基础内存占用。
- 单请求的平均内存开销。
- 最大并发下的内存峰值。
- 批量任务允许处理的最大数据规模。
- JVM 堆外内存和线程栈开销。
- 操作系统及其他进程需要预留的内存。
- 突发流量所需的安全余量。

不应让业务服务长期使用接近 100% 的物理内存，应预留一定空间处理流量波动、系统缓存和临时任务。

## 17. 推荐排查流程

发生 Linux 内存不足或 OOM 时，可以按照以下顺序排查：

1. 使用 `free -h` 查看物理内存、可用内存和 Swap。
2. 使用 `dmesg`、`journalctl` 或系统日志确认是否发生 OOM。
3. 根据 OOM 日志确定被终止的进程和发生时间。
4. 使用 `ps`、`top`、`pidstat` 定位高内存进程。
5. 查看 `/proc/<PID>/status`，确认 RSS、Swap 和线程数量。
6. 连续观察进程内存是否持续增长。
7. 检查业务流量、批量任务、消息积压和定时任务。
8. 如果是 Java 服务，检查堆内存、GC、线程和堆外内存。
9. 如果是容器服务，检查容器内存限制和 `OOMKilled` 状态。
10. 检查 Page Cache、Slab、脏页和内核内存。
11. 采取限流、停止异常任务或扩容等临时措施。
12. 根据监控趋势、Heap Dump 或程序日志定位根本原因。
13. 修复问题后进行压力测试和持续监控，确认内存不再异常增长。

## 18. 常用排查命令汇总

```bash
# 查看系统整体内存
free -h

# 查看详细内存信息
cat /proc/meminfo

# 查看内存变化和换页情况
vmstat 1 10

# 查看高内存进程
ps aux --sort=-%mem | head -20

# 按照 RSS 排序
ps -eo pid,ppid,user,%mem,rss,vsz,cmd --sort=-rss | head -20

# 查看指定进程的内存信息
cat /proc/<PID>/status

# 查看进程内存映射
pmap -x <PID>

# 查看进程内存变化
pidstat -r -p <PID> 5

# 查看 OOM 日志
dmesg -T | grep -iE "out of memory|oom|killed process"

# 查看 systemd 内核日志
journalctl -k | grep -iE "out of memory|oom|killed process"

# 查看 Swap
swapon --show

# 查看 Slab 内存
slabtop

# 查看 Docker 容器资源使用
docker stats

# 查看容器是否发生 OOM
docker inspect <容器ID> --format '{{.State.OOMKilled}}'

# 查看 Kubernetes Pod 状态
kubectl describe pod <pod-name> -n <namespace>

# 查看 Kubernetes Pod 内存
kubectl top pod -n <namespace>

# 查看 Java 堆信息
jcmd <PID> GC.heap_info

# 查看 Java 对象统计
jcmd <PID> GC.class_histogram

# 查看 Java GC 情况
jstat -gcutil <PID> 1000 10
```

## 19. 排查结论模板

### 故障现象

服务器可用内存持续下降，业务服务在指定时间异常退出，部分接口出现超时和连接失败。

### 故障确认

通过内核日志确认系统触发了 OOM Killer，被终止的进程为指定业务进程，PID 为指定值。

### 根本原因

业务在执行批量任务时一次性加载大量数据，同时异步任务队列没有设置容量上限，导致任务对象持续堆积。进程 RSS 不断增长，最终耗尽物理内存和 Swap，触发系统 OOM。

### 临时处理

暂停批量任务并重启异常服务，限制接口并发量，同时增加内存监控告警，优先恢复核心业务。

### 永久修复

将全量数据加载改为分页和流式处理，为异步队列设置最大长度，限制任务提交速度，优化 JVM 内存参数，并增加 Heap Dump 和 OOM 日志保留配置。

### 验证结果

修复后使用相同数据量进行压力测试，进程内存保持在合理范围内，Full GC 后内存能够正常回落，未再次出现 OOM。

## 20. 总结

Linux 内存不足与 OOM 的排查重点不是简单查看 `free` 是否较低，而是判断系统当前是否存在真实的内存压力，并找出内存被哪个进程占用、为什么持续增长，以及是在系统、进程还是容器的哪个限制范围内触发 OOM。

排查时应结合系统整体内存、进程 RSS、Swap、内核日志、容器限制、JVM 堆内存和业务运行情况进行综合分析。对于已经发生的 OOM，应优先保存现场信息，避免直接重启导致证据丢失；对于尚未触发 OOM 的内存压力，应通过持续监控判断是否存在内存泄漏、突发流量、任务堆积或配置不合理。

最终解决方案应落实到程序逻辑修复、资源限制调整、容量规划和监控告警上，不能长期依赖重启服务、手动清理缓存或增加 Swap。只有确认内存增长的根本原因并完成针对性修复，才能真正避免相同问题再次发生。