# Redis 服务不可用排查

## 1. 问题概述

Redis 服务不可用是企业应用运行过程中常见的缓存与中间件故障类型，通常表现为应用无法连接 Redis、缓存读取失败、接口响应异常、健康检查失败以及大量业务请求降级等问题。

在微服务架构中，Redis 通常承担缓存、分布式锁、Session 管理、消息队列以及热点数据存储等职责。当 Redis 服务不可用时，上层应用可能出现缓存穿透、接口延迟升高、业务失败甚至服务不可用等问题。

常见故障表现包括：

- 应用启动失败，提示 Redis 连接异常。
- Spring Boot 健康检查中 Redis 状态为 DOWN。
- 接口大量返回 Redis connection refused。
- RedisTemplate 或 Lettuce 客户端无法获取连接。
- 分布式锁获取失败。
- Prometheus 监控指标显示 Redis 探测失败。

常见异常：

```text
Connection refused

Unable to connect to Redis server

NOAUTH Authentication required

ERR max number of clients reached

OOM command not allowed when used memory > maxmemory
```

Redis 服务不可用通常涉及 Redis 进程状态、网络连接、认证配置、连接资源、内存资源等多个方面，需要按照由外到内的方式进行排查。


# 2. 常见原因分析


## 2.1 Redis 服务停止

Redis 服务停止是导致不可用最直接的原因。

可能原因：

- 手动停止 Redis 服务。
- Docker 容器异常退出。
- 服务器重启后 Redis 未自动启动。
- Redis 启动失败。
- 配置文件错误导致 Redis 无法加载。


常见现象：

应用连接 Redis 报错：

```text
Connection refused
```


检查 Redis 服务状态：

Linux：

```bash
systemctl status redis
```


Docker 环境：

```bash
docker ps -a
```


如果 Redis 容器停止：

```bash
docker start redis-container
```


查看 Redis 日志：

```bash
docker logs redis-container
```


重点关注：

- 配置文件加载失败。
- 数据文件恢复失败。
- 端口绑定失败。
- 权限错误。



# 3. Redis 网络连接异常排查


如果 Redis 服务正常运行，但是应用无法访问，需要检查网络连接。


## 3.1 检查 Redis 端口监听


Redis 默认端口：

```text
6379
```


Linux：

```bash
netstat -tunlp | grep 6379
```


或者：

```bash
ss -tunlp | grep 6379
```


正常情况下：

```text
LISTEN *:6379
```


如果没有监听：

说明 Redis 服务没有正常启动或者端口配置异常。


## 3.2 测试 Redis 连通性


客户端执行：

```bash
telnet redis_host 6379
```


或者：

```bash
nc -vz redis_host 6379
```


如果：

```text
Connection refused
```

说明 Redis 端口没有服务监听。


如果：

```text
Connection timeout
```

可能原因：

- 防火墙限制。
- 网络不可达。
- Docker 网络配置错误。


## 3.3 使用 redis-cli 测试连接


执行：

```bash
redis-cli -h redis_host -p 6379
```


正常：

```text
127.0.0.1:6379>
```


如果出现：

```text
NOAUTH Authentication required
```

说明 Redis 开启密码认证，需要提供密码：

```bash
redis-cli -h redis_host -p 6379 -a password
```



# 4. Redis 配置检查


应用通常通过配置文件连接 Redis。


Spring Boot 示例：

```yaml
spring:
  redis:
    host: localhost
    port: 6379
    password: password
```


重点检查：

## 4.1 Redis 地址

确认：

- host 是否正确。
- port 是否正确。
- Docker 映射端口是否正确。


例如：

错误：

```yaml
port: 6380
```


实际：

```text
6379
```


会导致连接失败。


## 4.2 Redis 密码配置


密码错误：

```text
NOAUTH Authentication required
```


检查 Redis 配置：

```conf
requirepass password
```


确认应用密码一致。


# 5. Redis 最大连接数耗尽


Redis 默认支持一定数量客户端连接。

当大量客户端连接 Redis 时，可能导致：

```text
ERR max number of clients reached
```


查看最大连接数：

```bash
redis-cli CONFIG GET maxclients
```


查看当前连接：

```bash
redis-cli CLIENT LIST
```


重点关注：

- 大量长期连接。
- 异常客户端。
- 连接泄漏。


修改最大连接数：

```bash
redis-cli CONFIG SET maxclients 10000
```


永久修改：

redis.conf：

```conf
maxclients 10000
```



# 6. Redis 内存不足导致不可用


Redis 内存达到限制后可能拒绝写入。


典型异常：

```text
OOM command not allowed when used memory > maxmemory
```


查看 Redis 内存：

```bash
redis-cli INFO memory
```


重点关注：

```text
used_memory

used_memory_peak

maxmemory
```


如果：

```text
used_memory >= maxmemory
```


可能导致：

- 写入失败。
- 缓存淘汰。
- 业务异常。


处理方式：

删除无效 Key：

```bash
redis-cli KEYS *
```


设置合理淘汰策略：

```conf
maxmemory-policy allkeys-lru
```


扩容 Redis 内存。


# 7. Redis 服务不可用完整排查流程


```text
Redis不可用告警
        |
        ↓
检查Redis进程状态
        |
        +----------------+
        |                |
      正常              异常
        |                |
        ↓                ↓
检查网络连接        启动Redis服务
        |
        ↓
检查6379端口
        |
        ↓
检查认证配置
        |
        ↓
检查连接数量
        |
        ↓
检查Redis内存状态
        |
        ↓
恢复服务并验证
```


# 8. Redis 监控指标建议


为了及时发现 Redis 故障，需要重点监控以下指标。


## 服务状态

```text
redis_up
```


判断 Redis 是否正常运行。


## 客户端连接数

```text
redis_connected_clients
```


观察连接压力。


## 内存使用率

计算：

```text
used_memory / maxmemory
```


判断 Redis 内存压力。


## 命令执行失败数量

关注：

```text
redis_commands_failed_total
```


判断业务操作失败情况。


# 9. 故障恢复建议


## Redis 服务停止

启动：

```bash
systemctl start redis
```


Docker：

```bash
docker start redis-container
```


## 连接耗尽

查看连接：

```bash
CLIENT LIST
```


关闭异常连接：

```bash
CLIENT KILL ip:port
```


## 内存不足

处理：

- 删除无效缓存。
- 扩容 Redis。
- 调整淘汰策略。


## 配置错误

修改配置后：

```bash
systemctl restart redis
```



# 10. AIOps 自动化诊断建议


在 AIOps 场景中，Redis 不可用诊断应该结合：

- Prometheus 指标。
- Redis INFO 信息。
- 应用异常日志。
- 健康检查接口。
- Runbook 知识库。


形成完整证据链：


```text
Redis不可用告警
        |
        ↓
检查 redis_up 指标
        |
        ↓
查询 Redis 连接状态
        |
        ↓
分析认证、网络、资源指标
        |
        ↓
定位服务停止/连接耗尽/内存不足
        |
        ↓
生成诊断报告
```


通过以上流程，可以快速定位 Redis 服务不可用原因，并降低缓存故障对业务系统造成的影响。