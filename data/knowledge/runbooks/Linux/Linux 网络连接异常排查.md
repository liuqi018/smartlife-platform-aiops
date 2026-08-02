# Linux 网络连接异常排查

## 1. 问题概述

Linux 网络连接异常是指客户端、服务器或不同服务之间无法正常建立连接，或者连接虽然能够建立，但存在延迟过高、频繁断开、丢包、超时、访问不稳定等问题。

网络连接涉及应用程序、操作系统、网络接口、路由、DNS、防火墙、负载均衡、代理服务器和远端服务等多个环节。任何一个环节出现异常，都可能导致网络请求失败。

常见的网络连接异常包括：

1. 无法访问目标服务器。
2. 可以访问 IP，但无法通过域名访问。
3. 可以 Ping 通，但端口无法连接。
4. 端口已经监听，但远程客户端无法访问。
5. 本机访问正常，其他服务器访问失败。
6. TCP 连接建立速度缓慢。
7. 网络请求频繁超时。
8. 连接建立后很快断开。
9. 大量连接处于 `TIME_WAIT`、`CLOSE_WAIT` 或 `SYN_RECV` 状态。
10. 网络延迟、丢包率或重传率持续升高。
11. 容器内部无法访问宿主机或外部网络。
12. 服务之间偶发连接失败。

排查网络问题时，应按照从底层到上层、从本机到远端、从局部到整体的顺序逐步定位，避免一开始就把问题归因于防火墙、网络设备或远端服务。

## 2. 常见错误信息

网络连接异常时，应用程序或命令行中可能出现以下错误。

### 2.1 Connection refused

```text
Connection refused
```

通常表示已经到达目标主机，但目标端口没有程序监听，或者防火墙主动拒绝了连接。

常见原因包括：

- 目标服务未启动。
- 服务监听了错误的端口。
- 服务只监听 `127.0.0.1`。
- 防火墙使用 REJECT 规则拒绝连接。
- 服务启动后立即退出。
- 请求访问了错误的 IP 或端口。

### 2.2 Connection timed out

```text
Connection timed out
```

通常表示连接请求在规定时间内没有得到响应。

常见原因包括：

- 网络链路不通。
- 防火墙静默丢弃数据包。
- 安全组未放行端口。
- 路由配置错误。
- 目标服务器不可达。
- 网络设备或负载均衡异常。
- 目标服务负载过高，无法及时响应。

### 2.3 No route to host

```text
No route to host
```

通常表示本机没有到达目标地址的有效路由，或者中间设备返回了主机不可达信息。

常见原因包括：

- 路由表配置错误。
- 网关不可达。
- 网卡未启动。
- 目标网段不可达。
- 防火墙返回不可达错误。
- VLAN、VPN 或隧道配置异常。

### 2.4 Network is unreachable

```text
Network is unreachable
```

通常表示本机无法找到前往目标网络的路由。

### 2.5 Name or service not known

```text
Name or service not known
```

通常表示域名解析失败，可能与 DNS 配置、域名拼写或 DNS 服务器状态有关。

### 2.6 Connection reset by peer

```text
Connection reset by peer
```

表示连接被对端主动重置。常见原因包括：

- 对端服务异常退出。
- 对端应用主动关闭连接。
- 请求不符合协议要求。
- 负载均衡或防火墙重置空闲连接。
- 应用连接池复用了失效连接。
- 中间代理主动终止连接。

### 2.7 Too many open files

```text
Too many open files
```

表示进程或系统的文件描述符已经达到限制。由于网络 Socket 也占用文件描述符，因此该问题可能导致无法建立新连接。

### 2.8 Cannot assign requested address

```text
Cannot assign requested address
```

常见原因包括：

- 客户端临时端口耗尽。
- 程序绑定了不存在的本地 IP。
- 本地地址配置错误。
- 大量短连接导致端口无法及时复用。

## 3. 网络连接异常的常见原因

### 3.1 目标服务未启动

目标服务器虽然可以访问，但应用程序没有启动，或者启动后异常退出，导致目标端口无人监听。

### 3.2 服务监听地址错误

服务可能只监听：

```text
127.0.0.1
```

此时只能从本机访问，其他服务器无法连接。

如果需要允许外部访问，通常应根据安全要求监听指定网卡地址或：

```text
0.0.0.0
```

监听 `0.0.0.0` 表示接受所有 IPv4 网卡上的连接，但是否能够从外部访问，还受到防火墙、安全组和网络路由限制。

### 3.3 端口配置错误

客户端访问的端口与服务实际监听端口不一致，或者服务配置修改后客户端仍然使用旧端口。

### 3.4 防火墙或安全组拦截

常见拦截位置包括：

- 本机 firewalld。
- iptables。
- nftables。
- 云服务器安全组。
- Kubernetes NetworkPolicy。
- 公司网络访问控制策略。
- 负载均衡访问控制。
- 网络出口防火墙。
- 中间路由设备 ACL。

### 3.5 DNS 解析异常

域名解析到错误 IP、DNS 缓存未更新、DNS 服务器不可用或域名过期，都可能导致服务无法访问。

### 3.6 路由配置异常

本机缺少到目标网段的路由，默认网关错误，策略路由异常，或者回程路由不一致，都可能导致连接失败。

### 3.7 网卡或链路异常

网卡未启用、网线断开、虚拟网卡异常、MTU 不一致、双工模式异常等问题，都可能导致网络不可用或性能下降。

### 3.8 连接数达到上限

应用程序、操作系统、Nginx、数据库或负载均衡器可能设置了连接数上限。当连接数达到限制后，新连接可能被拒绝或超时。

### 3.9 文件描述符耗尽

每个 TCP 连接都需要占用文件描述符。如果进程或系统达到文件描述符限制，将无法继续创建新连接。

### 3.10 临时端口耗尽

客户端建立大量短连接时，会不断使用本地临时端口。大量连接进入 `TIME_WAIT` 后，可能造成临时端口不足。

### 3.11 网络丢包或延迟过高

链路拥塞、网卡错误、交换机异常、带宽占满和跨地域网络抖动，都可能造成丢包、重传和请求超时。

### 3.12 负载均衡或代理异常

Nginx、HAProxy、Ingress、API Gateway 或云负载均衡配置错误，可能导致部分节点无法访问、请求转发失败或连接被重置。

### 3.13 应用程序连接管理不合理

常见问题包括：

- 连接池过小。
- 连接池中的连接失效。
- 请求超时时间过短。
- 没有正确关闭连接。
- 重试次数过多。
- 创建大量短连接。
- 服务端处理速度过慢。
- 请求线程被占满。

## 4. 排查原则

建议按照以下层次逐步排查：

```text
应用配置
    ↓
服务进程与监听端口
    ↓
本机网络接口
    ↓
本机路由
    ↓
本机防火墙
    ↓
中间网络链路
    ↓
目标主机防火墙
    ↓
目标服务
```

排查时需要明确以下信息：

- 连接发起方是谁。
- 目标服务器的 IP 和端口是什么。
- 使用的是 TCP 还是 UDP。
- 所有客户端都失败，还是只有部分客户端失败。
- 连接始终失败，还是偶发失败。
- 通过 IP 和域名访问是否存在差异。
- 本机访问和远程访问是否存在差异。
- 问题从什么时候开始。
- 问题发生前是否修改过网络、服务或防火墙配置。

## 5. 第一步：确认本机网络配置

查看网络接口：

```bash
ip addr
```

简化查看：

```bash
ip -br addr
```

重点检查：

- 网卡状态是否为 `UP`。
- IP 地址是否正确。
- 子网掩码是否正确。
- 是否存在预期的 IPv4 或 IPv6 地址。
- 是否出现重复 IP。
- 虚拟网卡和物理网卡是否符合预期。

查看链路状态：

```bash
ip link
```

如果网卡状态为 `DOWN`，可以在确认配置后启用：

```bash
ip link set <网卡名称> up
```

如果使用 NetworkManager，可以查看：

```bash
nmcli device status
```

查看连接配置：

```bash
nmcli connection show
```

## 6. 第二步：检查本机到网关的连通性

查看路由表：

```bash
ip route
```

示例：

```text
default via 192.168.1.1 dev eth0
192.168.1.0/24 dev eth0 proto kernel scope link src 192.168.1.100
```

重点检查：

- 是否存在默认路由。
- 默认网关是否正确。
- 目标网段是否存在更具体的路由。
- 路由出口网卡是否正确。
- 源 IP 是否正确。
- 是否存在冲突或错误的静态路由。

测试网关：

```bash
ping -c 4 <网关IP>
```

如果本机无法访问网关，应重点检查：

- 网卡状态。
- IP 地址和子网掩码。
- 网关配置。
- VLAN 配置。
- 虚拟网络配置。
- 物理链路。
- 云服务器网络接口状态。

## 7. 第三步：测试目标主机连通性

执行：

```bash
ping -c 4 <目标IP>
```

示例：

```bash
ping -c 4 192.168.1.200
```

`ping` 使用 ICMP 协议，只能辅助判断网络连通性。

需要注意：

- Ping 通不代表目标端口一定可访问。
- Ping 不通不代表目标服务一定不可访问。
- 部分服务器或防火墙会禁止 ICMP，但仍允许 TCP 连接。
- 某些网络设备会对 ICMP 请求进行限速。

因此，不能只根据 Ping 结果判断网络是否正常。

## 8. 第四步：测试目标端口

### 8.1 使用 nc

测试 TCP 端口：

```bash
nc -vz <目标IP> <端口>
```

例如：

```bash
nc -vz 192.168.1.200 8080
```

设置超时时间：

```bash
nc -vz -w 3 192.168.1.200 8080
```

测试 UDP 端口：

```bash
nc -vzu <目标IP> <端口>
```

由于 UDP 没有 TCP 那样的连接建立过程，UDP 测试结果需要结合服务日志和抓包进一步判断。

### 8.2 使用 telnet

```bash
telnet <目标IP> <端口>
```

例如：

```bash
telnet 192.168.1.200 3306
```

### 8.3 使用 curl

测试 HTTP 服务：

```bash
curl -v http://<目标IP>:<端口>/
```

测试 HTTPS 服务：

```bash
curl -vk https://<域名>/
```

只查看响应头：

```bash
curl -I http://<域名>/
```

设置连接超时：

```bash
curl --connect-timeout 3 --max-time 10 -v http://<域名>/
```

通过 `curl -v` 可以观察：

- DNS 解析结果。
- TCP 连接建立情况。
- TLS 握手过程。
- HTTP 请求和响应。
- 重定向信息。
- 连接超时位置。

## 9. 第五步：检查目标服务是否监听端口

在目标服务器上执行：

```bash
ss -lntp
```

查看指定端口：

```bash
ss -lntp | grep ':8080'
```

查看 UDP 端口：

```bash
ss -lnup
```

如果系统没有 `ss`，可以使用：

```bash
netstat -lntp
```

重点检查：

- 服务是否监听预期端口。
- 服务监听的是 `127.0.0.1`、指定 IP 还是 `0.0.0.0`。
- 端口是否被其他进程占用。
- 服务进程 PID 是否正确。
- TCP 和 UDP 协议是否配置正确。
- 是否只监听 IPv6 地址。

常见监听地址含义如下：

```text
127.0.0.1:8080
```

表示仅允许本机通过 IPv4 访问。

```text
0.0.0.0:8080
```

表示监听所有 IPv4 网络接口。

```text
[::]:8080
```

表示监听 IPv6 地址。是否同时接受 IPv4 连接取决于系统和应用配置。

## 10. 第六步：检查服务进程状态

使用 systemd 管理的服务可以执行：

```bash
systemctl status <服务名称>
```

查看服务日志：

```bash
journalctl -u <服务名称> -n 200
```

实时查看日志：

```bash
journalctl -u <服务名称> -f
```

查看进程：

```bash
ps -ef | grep <进程名称>
```

重点检查：

- 服务是否处于 `active (running)` 状态。
- 服务是否频繁重启。
- 服务是否启动后立即退出。
- 服务是否因为端口冲突而启动失败。
- 服务是否因为配置错误无法监听端口。
- 服务是否达到连接数或线程数上限。
- 服务是否因为内存不足被系统终止。

## 11. 第七步：检查 DNS 解析

查看 DNS 配置：

```bash
cat /etc/resolv.conf
```

常见内容如下：

```text
nameserver 192.168.1.10
search example.com
```

测试域名解析：

```bash
getent hosts <域名>
```

使用 `dig`：

```bash
dig <域名>
```

指定 DNS 服务器：

```bash
dig @<DNS服务器IP> <域名>
```

使用 `nslookup`：

```bash
nslookup <域名>
```

只测试 DNS 查询：

```bash
dig <域名> +short
```

排查时需要对比：

```bash
curl -v http://<域名>:<端口>/
```

和：

```bash
curl -v http://<目标IP>:<端口>/
```

如果通过 IP 可以访问，但通过域名不能访问，应重点检查：

- DNS 是否解析失败。
- DNS 是否解析到错误 IP。
- `/etc/hosts` 是否存在错误配置。
- 域名是否过期。
- DNS 缓存是否未刷新。
- 是否存在内外网 DNS 解析差异。
- 应用是否使用了旧的 DNS 缓存。

查看本机 hosts：

```bash
cat /etc/hosts
```

解析顺序可查看：

```bash
grep '^hosts:' /etc/nsswitch.conf
```

## 12. 第八步：检查防火墙

### 12.1 检查 firewalld

查看 firewalld 状态：

```bash
systemctl status firewalld
```

查看当前区域：

```bash
firewall-cmd --get-active-zones
```

查看已开放端口和服务：

```bash
firewall-cmd --list-all
```

查看指定端口：

```bash
firewall-cmd --query-port=8080/tcp
```

### 12.2 检查 iptables

查看规则：

```bash
iptables -L -n -v
```

查看 NAT 规则：

```bash
iptables -t nat -L -n -v
```

按照实际执行顺序查看：

```bash
iptables-save
```

重点关注：

- INPUT 链是否允许目标端口。
- OUTPUT 链是否限制外部访问。
- FORWARD 链是否影响容器或路由转发。
- 是否存在 DROP 或 REJECT 规则。
- 规则顺序是否正确。
- NAT 转换是否正确。

### 12.3 检查 nftables

```bash
nft list ruleset
```

### 12.4 检查云安全组

如果使用云服务器，还应检查：

- 入方向安全组规则。
- 出方向安全组规则。
- 允许访问的源 IP 范围。
- 目标端口和协议。
- 网络 ACL。
- 云负载均衡监听器和后端健康检查。

关闭本机防火墙并不能排除云安全组或上级网络防火墙的影响。

## 13. 第九步：检查路由和链路路径

查看到目标 IP 的实际路由：

```bash
ip route get <目标IP>
```

示例：

```bash
ip route get 192.168.1.200
```

该命令可以显示：

- 使用的出口网卡。
- 下一跳网关。
- 使用的源 IP。
- 匹配的路由。

查看链路路径：

```bash
traceroute <目标IP>
```

如果没有 `traceroute`，可以使用：

```bash
tracepath <目标IP>
```

对于 TCP 端口，可以使用：

```bash
traceroute -T -p 443 <目标IP>
```

路径中某个节点不响应不一定表示该节点发生故障。有些路由器会禁止或限制 ICMP 响应，但仍然正常转发后续数据包。

排查时应重点关注：

- 路径是否在某一跳后完全中断。
- 去程和回程路由是否一致。
- 是否走了错误的网关。
- 是否经过 VPN、代理或专线。
- 是否存在跨地域高延迟。
- 是否出现明显的路径变化。

## 14. 第十步：检查网络延迟和丢包

持续测试网络质量：

```bash
ping -c 20 <目标IP>
```

重点关注：

- 平均延迟。
- 最大延迟。
- 延迟抖动。
- 丢包率。

使用 `mtr` 综合查看路径延迟和丢包：

```bash
mtr -r -c 100 <目标IP>
```

使用 TCP 模式测试：

```bash
mtr -T -P 443 -r -c 100 <目标IP>
```

需要注意，中间节点显示丢包不一定代表真实转发丢包。如果后续节点和最终目标没有同样的丢包，应考虑中间路由设备可能只是限制了 ICMP 响应。

如果最终目标也出现持续丢包，应继续检查：

- 本机网卡错误。
- 网络带宽是否占满。
- 中间链路是否拥塞。
- 交换机或路由器是否异常。
- 目标服务器是否负载过高。
- 云网络是否存在抖动。
- 网络存储链路是否稳定。

## 15. 第十一步：检查 TCP 连接状态

查看 TCP 连接汇总：

```bash
ss -s
```

查看全部 TCP 连接：

```bash
ss -ant
```

统计各 TCP 状态数量：

```bash
ss -ant | awk 'NR>1 {count[$1]++} END {for (state in count) print state, count[state]}'
```

常见 TCP 状态包括：

- `LISTEN`：服务正在监听端口。
- `ESTAB`：连接已经建立。
- `SYN-SENT`：客户端已经发送 SYN，等待服务端响应。
- `SYN-RECV`：服务端收到 SYN，等待连接完成。
- `TIME-WAIT`：主动关闭连接的一方等待连接彻底结束。
- `CLOSE-WAIT`：对端已经关闭连接，本地应用尚未关闭。
- `FIN-WAIT-1`：本地已发送 FIN，等待对端确认。
- `FIN-WAIT-2`：本地 FIN 已确认，等待对端关闭。
- `LAST-ACK`：等待最后的 ACK。
- `CLOSING`：双方几乎同时关闭连接。

不同 TCP 状态异常增多，通常对应不同问题，不能使用同一种方式处理。

## 16. TIME_WAIT 过多排查

`TIME_WAIT` 是 TCP 正常关闭过程中的一种状态，主要用于避免旧连接的数据包影响新连接，并确保对端可以收到最后的 ACK。

查看 `TIME_WAIT` 数量：

```bash
ss -ant state time-wait | wc -l
```

查看哪些远端地址或端口产生较多 `TIME_WAIT`：

```bash
ss -ant state time-wait
```

常见原因包括：

- 应用频繁创建短连接。
- HTTP 没有启用 Keep-Alive。
- 数据库连接池配置不合理。
- 服务之间没有复用连接。
- 客户端请求量过大。
- 代理服务器频繁主动关闭连接。

优化建议包括：

- 使用连接池。
- 启用 HTTP Keep-Alive。
- 减少不必要的短连接。
- 合理设置客户端和服务端超时。
- 优化连接复用机制。
- 检查临时端口范围。

不能简单地认为 `TIME_WAIT` 多就是异常。只有当它导致临时端口耗尽、文件描述符压力或连接失败时，才需要进行针对性优化。

## 17. CLOSE_WAIT 过多排查

`CLOSE_WAIT` 表示对端已经关闭连接，但本地应用程序尚未调用关闭操作。

查看数量：

```bash
ss -ant state close-wait | wc -l
```

查看对应进程：

```bash
ss -antp state close-wait
```

大量 `CLOSE_WAIT` 通常说明应用程序没有正确关闭 Socket，常见原因包括：

- 代码没有在异常情况下关闭连接。
- HTTP 响应流没有关闭。
- 数据库连接没有归还连接池。
- 网络客户端存在资源泄漏。
- 应用线程阻塞，无法执行连接关闭逻辑。

`CLOSE_WAIT` 问题通常需要修改应用程序代码，仅通过调整内核参数无法从根本上解决。

## 18. SYN_RECV 过多排查

查看半连接状态：

```bash
ss -ant state syn-recv
```

大量 `SYN_RECV` 可能表示：

- 短时间内有大量新连接。
- 服务端处理能力不足。
- 半连接队列已满。
- 客户端网络异常，ACK 无法返回。
- 存在 SYN Flood 攻击。
- 防火墙或负载均衡丢弃后续报文。

查看相关内核统计：

```bash
netstat -s | grep -iE "listen|SYN"
```

查看半连接队列相关参数：

```bash
sysctl net.ipv4.tcp_max_syn_backlog
sysctl net.core.somaxconn
```

调整参数前，应先确认是正常业务增长、队列配置不足还是网络攻击，不能只通过扩大队列掩盖应用处理能力不足的问题。

## 19. 检查连接数和监听队列

查看指定端口连接数：

```bash
ss -ant | grep ':8080' | wc -l
```

查看已建立连接：

```bash
ss -ant state established | wc -l
```

查看监听队列：

```bash
ss -lnt
```

示例：

```text
State  Recv-Q Send-Q Local Address:Port
LISTEN 128    128    0.0.0.0:8080
```

对于监听状态：

- `Recv-Q` 可能表示当前等待应用接受的连接数量。
- `Send-Q` 可能表示监听队列上限。

如果监听队列长期接近或达到上限，说明应用接受连接的速度不足，可能与线程池耗尽、服务负载过高或队列配置过小有关。

查看系统监听队列上限：

```bash
sysctl net.core.somaxconn
```

应用程序自身也可能设置 Backlog，实际队列能力受应用和内核参数共同影响。

## 20. 检查文件描述符限制

查看当前 Shell 限制：

```bash
ulimit -n
```

查看进程限制：

```bash
cat /proc/<PID>/limits
```

查看进程已使用的文件描述符数量：

```bash
ls /proc/<PID>/fd | wc -l
```

查看系统级文件描述符使用情况：

```bash
cat /proc/sys/fs/file-nr
```

查看系统级最大值：

```bash
sysctl fs.file-max
```

如果进程日志中出现：

```text
Too many open files
```

应检查：

- 进程文件描述符上限。
- systemd 服务的 `LimitNOFILE`。
- 应用连接是否正确关闭。
- 连接池配置是否合理。
- 是否存在文件描述符泄漏。
- 系统级文件描述符限制是否足够。

仅提高文件描述符上限不能解决连接泄漏问题。如果文件描述符数量持续增长，应继续定位未关闭的 Socket 或文件。

## 21. 检查临时端口范围

查看本地临时端口范围：

```bash
sysctl net.ipv4.ip_local_port_range
```

示例：

```text
net.ipv4.ip_local_port_range = 32768 60999
```

查看到指定目标的连接：

```bash
ss -ant dst <目标IP>
```

大量短连接可能使可用临时端口不足，从而出现：

```text
Cannot assign requested address
```

排查和优化方向包括：

- 使用长连接和连接池。
- 减少主动创建短连接。
- 检查 `TIME_WAIT` 数量。
- 扩大临时端口范围。
- 将请求分散到多个目标地址。
- 合理设置连接超时。
- 修复没有复用连接的代码。

内核参数调整应在理解 TCP 行为和业务模式后进行，不能直接复制不适合当前系统的优化模板。

## 22. 检查网卡流量和错误

查看网卡统计：

```bash
ip -s link
```

查看 `/proc` 网络统计：

```bash
cat /proc/net/dev
```

使用 `sar` 查看网卡流量：

```bash
sar -n DEV 1 10
```

重点关注：

- 接收和发送流量。
- 丢包数量。
- 错误包数量。
- Drop 数量。
- Overrun。
- Carrier Error。
- Collisions。

查看网卡详细信息：

```bash
ethtool <网卡名称>
```

查看网卡统计：

```bash
ethtool -S <网卡名称>
```

查看速率和双工模式：

```bash
ethtool <网卡名称> | grep -iE "Speed|Duplex|Link detected"
```

如果网卡流量接近带宽上限，可能出现排队、延迟和丢包。网卡错误计数持续增长时，应检查物理链路、虚拟化平台、驱动程序和交换机端口。

## 23. 检查带宽是否占满

使用 `sar`：

```bash
sar -n DEV 1 10
```

如果安装了 `iftop`，可以查看实时连接流量：

```bash
iftop -i <网卡名称>
```

使用 `nload` 查看总体流量：

```bash
nload <网卡名称>
```

使用 `nethogs` 查看进程网络流量：

```bash
nethogs <网卡名称>
```

如果出口带宽持续接近上限，应继续确认：

- 哪些进程占用带宽。
- 是否存在大文件传输。
- 是否执行备份或同步任务。
- 是否出现异常流量。
- 是否发生流量攻击。
- 是否需要限速或扩容带宽。
- 是否可以将任务调整到业务低峰期。

## 24. 检查 MTU 问题

MTU 不一致可能导致部分网络请求异常，尤其是小数据包正常、大数据包失败的情况。

查看网卡 MTU：

```bash
ip link show <网卡名称>
```

测试不允许分片的数据包：

```bash
ping -M do -s 1472 -c 4 <目标IP>
```

对于标准 1500 字节 MTU，ICMP 数据部分通常可以从 1472 开始测试，因为还需要加上 IP 和 ICMP 头。

如果大包失败、小包成功，应检查：

- 本机网卡 MTU。
- VPN 或隧道 MTU。
- 云网络 MTU。
- 容器网络 MTU。
- 中间设备是否允许 ICMP Fragmentation Needed。
- 是否存在 Path MTU Discovery 失败。

不应在没有确认链路要求的情况下随意修改 MTU。

## 25. 使用 tcpdump 抓包分析

当基础命令无法定位问题时，可以使用 `tcpdump` 抓取网络报文。

抓取指定主机：

```bash
tcpdump -i any host <目标IP>
```

抓取指定端口：

```bash
tcpdump -i any port 8080
```

抓取指定主机和端口：

```bash
tcpdump -i any host <目标IP> and port 8080
```

保存为文件：

```bash
tcpdump -i any host <目标IP> and port 8080 -w /tmp/network.pcap
```

限制抓包数量：

```bash
tcpdump -i any host <目标IP> and port 8080 -c 1000 -w /tmp/network.pcap
```

通过抓包可以判断：

- 客户端是否发出了 SYN。
- 服务端是否返回 SYN-ACK。
- 客户端是否发送 ACK。
- 是否存在 TCP 重传。
- 哪一方发送了 RST。
- 是否存在大量零窗口。
- TLS 握手停在哪一步。
- DNS 请求是否得到响应。
- 数据包是否从正确的网卡进出。

生产环境抓包时应设置明确的主机、端口、数量和文件大小限制，避免生成过大的抓包文件或采集无关敏感数据。

## 26. TCP 三次握手问题判断

正常 TCP 三次握手如下：

```text
客户端 → 服务端：SYN
服务端 → 客户端：SYN-ACK
客户端 → 服务端：ACK
```

不同抓包结果通常可以说明不同问题。

### 26.1 只有 SYN，没有 SYN-ACK

可能原因包括：

- 请求没有到达服务端。
- 服务端防火墙丢弃请求。
- 服务端没有返回路由。
- 中间网络设备丢包。
- 目标服务或主机没有响应。

### 26.2 收到 RST

可能原因包括：

- 目标端口没有监听。
- 防火墙主动拒绝。
- 应用主动重置连接。
- 中间代理发送重置包。

### 26.3 服务端发送 SYN-ACK，但客户端没有收到

可能原因包括：

- 回程路由异常。
- 客户端防火墙丢弃响应。
- 中间链路丢包。
- 服务端使用了错误的源地址。
- 存在非对称路由问题。

### 26.4 握手成功但应用无响应

说明 TCP 连接已经建立，问题可能位于：

- 应用线程池。
- 数据库或下游服务。
- HTTP 协议处理。
- TLS 握手。
- 应用逻辑。
- 服务端负载。
- 连接池或资源池。

## 27. 检查 TCP 重传

查看 TCP 汇总统计：

```bash
netstat -s
```

只查看重传：

```bash
netstat -s | grep -i retrans
```

使用 `sar`：

```bash
sar -n TCP,ETCP 1 10
```

重点关注：

- `retrans/s`。
- 主动连接数。
- 被动连接数。
- TCP 重置数量。
- 连接失败数量。

TCP 重传持续增加可能与以下原因有关：

- 网络丢包。
- 链路拥塞。
- 网卡或交换机异常。
- 目标服务响应缓慢。
- 接收窗口过小。
- MTU 问题。
- 跨地域网络质量较差。
- 云网络或网络存储抖动。

## 28. 检查 IPv4 与 IPv6 问题

部分域名同时存在 IPv4 和 IPv6 记录。如果服务器 IPv6 配置不完整，应用可能优先尝试 IPv6，导致连接等待或失败。

查看域名记录：

```bash
dig A <域名>
```

```bash
dig AAAA <域名>
```

强制使用 IPv4：

```bash
curl -4 -v https://<域名>/
```

强制使用 IPv6：

```bash
curl -6 -v https://<域名>/
```

如果 IPv4 正常而 IPv6 失败，应检查：

- IPv6 地址配置。
- IPv6 默认路由。
- 防火墙 IPv6 规则。
- DNS AAAA 记录。
- 应用对 IPv6 的支持。
- 云服务器 IPv6 网络配置。

## 29. 检查代理配置

查看代理环境变量：

```bash
env | grep -i proxy
```

常见变量包括：

```text
HTTP_PROXY
HTTPS_PROXY
ALL_PROXY
NO_PROXY
```

代理配置错误可能导致：

- 内网请求被错误发送到外部代理。
- 目标域名不在 `NO_PROXY` 中。
- 代理服务器无法访问。
- HTTP 与 HTTPS 代理协议配置错误。
- 应用继承了不正确的代理环境变量。

测试时可以临时忽略代理：

```bash
curl --noproxy '*' -v http://<目标地址>/
```

是否使用该选项应根据实际网络环境判断。

## 30. 排查 Nginx 或负载均衡问题

如果客户端可以访问负载均衡，但请求无法正常到达后端，需要分别测试：

```text
客户端 → 负载均衡
负载均衡 → 后端服务
```

在负载均衡节点测试后端：

```bash
curl -v http://<后端IP>:<端口>/
```

检查 Nginx 配置：

```bash
nginx -t
```

查看 Nginx 错误日志：

```bash
tail -n 200 /var/log/nginx/error.log
```

常见错误包括：

```text
connect() failed (111: Connection refused) while connecting to upstream
upstream timed out
no live upstreams
connection reset by peer
```

常见原因包括：

- 后端服务未启动。
- Upstream 地址或端口错误。
- 后端健康检查失败。
- 代理超时时间过短。
- 后端连接数达到上限。
- Nginx 工作进程文件描述符不足。
- 后端响应速度过慢。
- 负载均衡算法导致部分节点压力过高。

## 31. 排查 Docker 网络问题

查看 Docker 网络：

```bash
docker network ls
```

查看网络详情：

```bash
docker network inspect <网络名称>
```

查看容器网络配置：

```bash
docker inspect <容器名称或ID>
```

进入容器测试：

```bash
docker exec -it <容器名称或ID> sh
```

在容器内检查：

```bash
ip addr
ip route
cat /etc/resolv.conf
```

常见 Docker 网络问题包括：

- 容器不在同一个 Docker 网络。
- 使用了错误的容器地址。
- 宿主机端口没有正确映射。
- 服务只监听容器内部的 `127.0.0.1`。
- Docker iptables 规则异常。
- 容器 DNS 解析失败。
- Docker 网段与公司内网网段冲突。
- 宿主机防火墙阻止转发。
- IP Forward 未启用。

查看端口映射：

```bash
docker port <容器名称或ID>
```

查看 IP 转发：

```bash
sysctl net.ipv4.ip_forward
```

## 32. 排查 Kubernetes 网络问题

查看 Pod：

```bash
kubectl get pods -n <namespace> -o wide
```

查看 Service：

```bash
kubectl get svc -n <namespace>
```

查看 Endpoint：

```bash
kubectl get endpoints -n <namespace>
```

查看 EndpointSlice：

```bash
kubectl get endpointslice -n <namespace>
```

进入 Pod 测试：

```bash
kubectl exec -it <pod-name> -n <namespace> -- sh
```

检查 DNS：

```bash
nslookup <service-name>
```

检查端口：

```bash
nc -vz <service-name> <端口>
```

常见 Kubernetes 网络问题包括：

- Service Selector 与 Pod Label 不匹配。
- Service 没有 Endpoint。
- Pod 没有通过 Readiness Probe。
- NetworkPolicy 阻止访问。
- CoreDNS 异常。
- CNI 插件异常。
- Ingress 配置错误。
- Service 端口与 TargetPort 不一致。
- Pod 服务只监听 `127.0.0.1`。
- 节点路由或转发规则异常。

## 33. 常见处理措施

### 33.1 服务未监听端口

处理建议：

- 启动目标服务。
- 检查服务启动日志。
- 修正监听端口。
- 修正监听地址。
- 解决端口冲突。
- 检查服务是否频繁退出。

### 33.2 防火墙或安全组拦截

处理建议：

- 根据最小权限原则开放必要的端口。
- 限制允许访问的源 IP 范围。
- 同时检查本机防火墙和云安全组。
- 检查规则顺序。
- 检查 TCP 与 UDP 协议是否正确。
- 修改后重新验证实际连接。

### 33.3 DNS 解析异常

处理建议：

- 修正 DNS 服务器配置。
- 修正错误的域名记录。
- 清理错误的 `/etc/hosts` 配置。
- 检查域名是否过期。
- 检查内外网 DNS 是否一致。
- 刷新应用和系统 DNS 缓存。

### 33.4 连接数过高

处理建议：

- 使用连接池和长连接。
- 修复连接泄漏。
- 合理调整最大连接数。
- 增加服务实例。
- 配置负载均衡。
- 优化服务处理速度。
- 限制异常客户端连接。
- 设置合理的连接超时。

### 33.5 TIME_WAIT 过多

处理建议：

- 启用连接复用。
- 使用 HTTP Keep-Alive。
- 优化数据库连接池。
- 减少短连接。
- 合理扩大临时端口范围。
- 避免盲目修改 TCP 回收参数。

### 33.6 CLOSE_WAIT 过多

处理建议：

- 检查代码是否关闭响应流和 Socket。
- 检查异常处理路径。
- 检查连接池归还逻辑。
- 设置合理的连接和读取超时。
- 修复文件描述符或连接泄漏。

### 33.7 网络丢包或带宽不足

处理建议：

- 暂停非核心大流量任务。
- 对备份和同步任务进行限速。
- 扩容网络带宽。
- 优化跨地域访问路径。
- 检查网卡和交换机错误。
- 联系云平台或网络管理人员排查链路。
- 增加重试时使用指数退避，避免重试放大流量。

## 34. 不建议直接执行的操作

### 34.1 不建议直接关闭所有防火墙

直接关闭防火墙可能扩大安全风险。应先查看规则和命中计数，再按最小权限原则开放需要的端口。

### 34.2 不建议盲目修改 TCP 内核参数

网络优化参数与内核版本、业务连接模式和网络环境有关。直接复制网络优化模板可能造成连接异常或稳定性问题。

### 34.3 不建议通过重启掩盖连接泄漏

重启服务可能暂时清除 `CLOSE_WAIT` 和文件描述符占用，但无法修复应用没有正确关闭连接的根本问题。

### 34.4 不建议只根据 Ping 结果判断网络

ICMP 和 TCP、UDP 可能经过不同的访问控制规则。Ping 只能作为辅助检查手段。

### 34.5 不建议无限增加超时时间和重试次数

超时时间过长会导致线程、连接和请求长期占用资源；重试次数过多会放大故障流量，使下游服务更加难以恢复。

## 35. 监控与预防建议

建议持续监控以下指标：

- 网卡接收和发送流量。
- 网络带宽使用率。
- 网络丢包率。
- TCP 重传率。
- TCP 连接总数。
- `ESTABLISHED` 数量。
- `TIME_WAIT` 数量。
- `CLOSE_WAIT` 数量。
- `SYN_RECV` 数量。
- 连接建立失败次数。
- DNS 解析耗时和失败率。
- 接口响应时间。
- 服务端连接队列。
- 文件描述符使用率。
- 网卡错误包和丢弃包。
- 负载均衡后端健康状态。
- 容器网络和服务发现状态。
- 关键下游服务可用性。

告警应结合业务正常基线设置，例如：

- 网络丢包率持续超过正常范围。
- TCP 重传率快速升高。
- `CLOSE_WAIT` 数量持续增长。
- 文件描述符使用率超过 80%。
- 连接失败率持续升高。
- DNS 解析耗时明显增加。
- 网卡带宽长时间接近上限。
- 负载均衡健康节点数量减少。
- 关键端口连续探测失败。

## 36. 推荐排查流程

发生 Linux 网络连接异常时，可以按照以下顺序排查：

1. 明确客户端、目标 IP、目标端口和协议。
2. 确认问题是持续发生还是偶发发生。
3. 使用 `ip addr` 检查本机 IP 和网卡状态。
4. 使用 `ip route` 检查默认网关和目标路由。
5. 测试本机到网关的连通性。
6. 使用 `ping` 辅助测试目标主机。
7. 使用 `nc`、`telnet` 或 `curl` 测试目标端口。
8. 在目标服务器上使用 `ss` 检查端口监听。
9. 检查目标服务进程和服务日志。
10. 分别使用域名和 IP 测试，判断是否为 DNS 问题。
11. 检查 firewalld、iptables、nftables 和云安全组。
12. 使用 `ip route get` 和 `traceroute` 检查路由路径。
13. 使用 `mtr` 检查延迟、丢包和链路质量。
14. 使用 `ss` 检查 TCP 连接状态。
15. 检查连接数、文件描述符和临时端口。
16. 检查网卡流量、错误包和带宽使用。
17. 必要时使用 `tcpdump` 抓包分析。
18. 如果涉及代理、负载均衡、容器或 Kubernetes，继续检查对应转发链路。
19. 根据定位结果修复服务、防火墙、路由、DNS 或应用连接管理问题。
20. 修复后从客户端重新验证完整访问链路。

## 37. 常用排查命令汇总

```bash
# 查看 IP 地址
ip addr
ip -br addr

# 查看网卡状态
ip link
nmcli device status

# 查看路由
ip route

# 查看访问目标使用的路由
ip route get <目标IP>

# 测试网络连通性
ping -c 4 <目标IP>

# 测试 TCP 端口
nc -vz -w 3 <目标IP> <端口>

# 测试 HTTP 服务
curl -v --connect-timeout 3 http://<目标地址>/

# 测试 HTTPS 服务
curl -vk --connect-timeout 3 https://<目标地址>/

# 查看 TCP 监听端口
ss -lntp

# 查看 UDP 监听端口
ss -lnup

# 查看 TCP 连接
ss -ant

# 查看 TCP 状态汇总
ss -s

# 统计各 TCP 状态
ss -ant | awk 'NR>1 {count[$1]++} END {for (state in count) print state, count[state]}'

# 查看 TIME_WAIT
ss -ant state time-wait

# 查看 CLOSE_WAIT
ss -antp state close-wait

# 查看 SYN_RECV
ss -ant state syn-recv

# 查看服务状态
systemctl status <服务名称>

# 查看服务日志
journalctl -u <服务名称> -n 200

# 查看 DNS 配置
cat /etc/resolv.conf

# 测试域名解析
getent hosts <域名>
dig <域名>
nslookup <域名>

# 检查 hosts
cat /etc/hosts

# 查看 firewalld 规则
firewall-cmd --list-all

# 查看 iptables 规则
iptables -L -n -v
iptables -t nat -L -n -v

# 查看 nftables 规则
nft list ruleset

# 查看网络路径
traceroute <目标IP>
tracepath <目标IP>

# 检查链路质量
mtr -r -c 100 <目标IP>

# 查看进程文件描述符限制
cat /proc/<PID>/limits

# 查看进程已使用的文件描述符
ls /proc/<PID>/fd | wc -l

# 查看临时端口范围
sysctl net.ipv4.ip_local_port_range

# 查看网卡流量和错误
ip -s link
cat /proc/net/dev

# 查看实时网卡流量
sar -n DEV 1 10

# 查看 TCP 重传
sar -n TCP,ETCP 1 10
netstat -s | grep -i retrans

# 查看网卡信息
ethtool <网卡名称>
ethtool -S <网卡名称>

# 抓取指定主机和端口的数据包
tcpdump -i any host <目标IP> and port <端口>

# 保存抓包文件
tcpdump -i any host <目标IP> and port <端口> -c 1000 -w /tmp/network.pcap

# 查看代理配置
env | grep -i proxy

# 查看 Docker 网络
docker network ls
docker network inspect <网络名称>

# 查看 Kubernetes Service 和 Endpoint
kubectl get svc,endpoints -n <namespace>
```

## 38. 排查结论模板

### 故障现象

应用服务器访问下游服务时频繁出现连接超时，部分请求失败，但下游服务进程仍然处于运行状态。

### 故障确认

通过 `curl` 和 `nc` 测试发现应用服务器无法连接下游服务端口，但可以 Ping 通目标主机。在目标服务器上使用 `ss -lntp` 确认服务已经监听正确端口。

### 根本原因

目标服务器的防火墙规则仅允许原有应用节点访问。应用扩容后新增节点的 IP 地址未加入允许列表，导致新增节点发出的连接请求被防火墙静默丢弃，最终表现为连接超时。

### 临时处理

在确认来源 IP 和业务用途后，将新增应用节点 IP 加入目标端口的防火墙允许规则，并重新加载防火墙配置。

### 永久修复

将应用节点网段统一纳入访问控制配置，完善服务扩容检查流程，并增加从各应用节点到关键下游端口的自动连通性监控。

### 验证结果

防火墙规则更新后，使用 `nc` 和 `curl` 测试均可以正常连接。持续观察应用错误率、连接耗时和下游服务监控，未再次出现连接超时。

## 39. 总结

Linux 网络连接异常排查的关键是明确连接链路，并逐层判断问题发生在客户端、网络、目标服务器还是应用程序内部。

排查时应先确认本机网卡、IP、网关和路由是否正常，再使用 `nc`、`curl` 等工具测试目标端口。在目标服务器上，需要确认服务进程是否运行、端口是否监听、监听地址是否正确，并检查防火墙、安全组和访问控制规则。

如果基础连通性正常，但仍然存在超时、断开或访问缓慢，应继续检查 DNS、网络路径、丢包、TCP 重传、连接状态、文件描述符、临时端口、负载均衡和应用连接池。必要时可以使用 `tcpdump` 抓包，观察请求和响应实际经过的链路。

网络问题通常涉及多个系统，不能只根据 Ping 结果或单台服务器状态下结论。应通过客户端和服务端的日志、连接状态、抓包结果及网络设备监控相互验证，最终确定故障发生的准确位置。

完成修复后，应从实际客户端重新验证完整访问链路，并建立关键端口探测、DNS 解析、TCP 重传、连接状态和网络带宽等监控，降低类似问题再次发生的风险。