# 并发覆盖期间的 Generation 切换见证

**证据层级：** 两条独立 RXE 路径上的单轮真实 Verbs 执行；20 次 8 MiB 双轨读取、15 次唯一内容覆盖。它补充了[两轮 SDK 调用区间实验](rewrite-race-and-server-failstop-2026-10-09.md)，不与其样本数合并。

## 需要回答的问题

先前的时间戳证明读方法与客户端 PUT 方法在时间上相交，但没有定位服务端元数据提交。本轮使用**同一次读取的两次对象身份查询**作见证：Rust SDK 的 [`read_multi_rail_into`](../../kv-service/client-rs/src/lib.rs)按“初次 `LookupObject` → 双轨传输到私有缓冲区 → 再次 `LookupObject` → 身份相同才发布”的顺序执行。打开 `CS_RAIL_TIMING=1` 时，`VersionChanged` 拒绝额外记录初查与复查的 Generation；写端先准备 payload，再为 `put_stream_chunks` 调用计时。

本实验只有一个顺序读客户端和一个覆盖写客户端，不删除对象。服务端的 [`prepare_write_meta`](../../kv-service/server/src/storage_tier.rs)经元数据后端分配递增 Generation；流式写入在条带任务完成后调用 [`replace_block`](../../kv-service/server/src/api/service.rs)，Redis 实现用一段脚本原子替换该键的元数据。[`LookupObject`](../../kv-service/server/src/api/service.rs)从该元数据后端查询。因此，在这些条件下，若同一次读取记录 `G_before < G_after`，则至少一次元数据替换发生在两次查询观察点之间，也就在该读方法执行期间。这个推断**不定位具体提交时刻，也不证明提交与 RDMA WRITE 在途时间相交**。

## 原始结果与归属

| 读尝试序号 | 初查 Generation | 复查 Generation |
| ---: | ---: | ---: |
| 1 | 1 | 2 |
| 6 | 4 | 5 |
| 7 | 5 | 6 |
| 12 | 8 | 9 |
| 13 | 9 | 10 |
| 15 | 11 | 12 |
| 16 | 12 | 13 |
| 17 | 13 | 14 |

该轮 20 次读取均与至少一次 SDK PUT 方法调用区间相交：10 次返回完整内容，且内容编码的 Generation 与 SDK 接受身份一致；10 次保持目标缓冲区哨兵并拒绝。其中 **8 次**为上表的明确 Generation 增长，另 **2 次**在条带传输阶段报告 Descriptor stale，未形成“初查—复查”见证；0 次对象缺失。两条 Rail 都传输了非零字节，末态在途请求、在途字节和注册预约归零。原始日志、启动前二进制哈希、服务端运行映像及两端 RXE 拓扑见[保存的收据](../../kv-service/verification/results/unique-generation-transition-20261012/README.md)；[主审计 JSON](../../kv-service/verification/results/unique-generation-transition-20261012/result.json)与[第二物理节点重算结果](../../kv-service/verification/results/independent-skv2/transition-independent.json)逐字节相同。

Linux release 构建收据保存了 **20 份实际源码快照**，逐文件核对 SHA-256 以及提交 `f1a3fbf4861918f68376ce7c09d287a331ff29bf` 的 Git blob 身份；构建输出 SHA-256 与执行前清单一致。源码快照使审计不依赖本机旧 Git blob 是否被云同步卸载。服务端 PID、`/proc/PID/exe` 哈希和配置哈希在该轮前后一致。人为篡改快照的负控被构建审计拒绝。

## 主张边界

这 8 次是**实际观察到的元数据版本切换**，并同时验证拒绝不污染调用方缓冲区。它们不代表全部交错时序、实体 HCA 的 CQE/WR 生命周期或真实提供者故障。为了保护隔离测试虚拟机的磁盘余量，本轮只运行一个进程；没有从这 20 次读取估计故障概率或吞吐变化。进一步的顶会级验证需要可重复的多进程、多线程覆盖，给服务端提交点与条带传输加同一请求关联标识，并在真实双 HCA 与故障条件下复核。
