# 实体双 HCA Rail 可用性复核（只读）

**采集时间：** 2026-10-09。对 SKV 节点 1–5 直连、节点 6–7 经 SKV_1 跳转，分别读取 `ibv_devinfo -l` 与 `rdma link show`；未更改网卡、地址、链路或服务配置。[七份原始输出及 SHA-256 审计](../../kv-service/verification/results/physical-topology-20261009/audit.json)可逐节点复核，另一台物理 SKV 节点的 [Python 3.8 重算结果](../../kv-service/verification/results/independent-skv2/hca-independent.json)与本地审计完全一致。

| 节点 | Verbs 枚举名称 | `mlx5_0/1` | `mlx5_1/1` | 同一 Worker 可用实体 Rail 数 |
| --- | --- | --- | --- | ---: |
| skv-node1–4 | `mlx5_0`, `mlx5_1` | DOWN / DISABLED (`ens4f0`) | ACTIVE / LINK_UP (`ens4f1`) | 1 |
| skv-node5 | `mlx5_0`, `mlx5_1` | DOWN / DISABLED (`enp175s0f0np0`) | ACTIVE / LINK_UP (`enp175s0f1np1`) | 1 |
| skv-node6–7 | `mlx5_0`, `mlx5_1` | DOWN / DISABLED (`ens3f0`) | ACTIVE / LINK_UP (`ens3f1`) | 1 |

`ibv_devinfo` 把两个 Verbs 设备名称枚举出来，**不等于两个端口都已具备可传输链路**。七个节点本次都只有 `mlx5_1/1` 同时处于 `ACTIVE/LINK_UP`。因此现有集群仍不能在**一个客户端 Worker**上构成两条同时可用的独立实体 HCA Rail，也不能用跨两个客户端进程或两台机器分别做单轨读来替代赛题要求。此前的物理结果只是一条 100 Gb/s HCA 路径的基线/容量校准；双轨正确性与当前性能矩阵来自独立虚拟网口上的 Soft‑RoCE。

若管理员后续使另一个实体端口具备真实链路，先重新采集端口状态、GID/IP、交换机端口、PCIe/NUMA 与磁盘源带宽，再在不改变对象、条带布局、服务端和并发条件的前提下执行：单 HCA 一 QP、单 HCA 两 QP、双独立 HCA。必须先证明单轨应用负载接近实际端口上限，且双轨时两端口字节计数与源端读取带宽匹配，才能把吞吐提升归因于网络聚合。本次只读盘点既没有尝试启用停用端口，也不预测未来维护后的可用性。
