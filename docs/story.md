# One Worker, two rails, one complete object

The object stays in its existing disk stripes. A single Worker reads those stripes through two independently controlled RDMA paths and exposes the result only after every required byte, checksum and object-version check succeeds.

## 1. Conflict

Concurrent disks can supply a large KV-cache object faster than one network path can carry it. Splitting the transfer across connections is tempting, but direct writes into the caller's buffer make a partial result visible. A timed-out RDMA WRITE or late CQE can outlive the request that owns its destination or source memory.

## 2. Insight

The placement already describes the object's physical stripes and owning storage endpoint. It does not require a new disk layout to select a network path. The server can optionally advertise additional fabric/listener capabilities in `LookupObject`; the client matches these to locally configured Verbs devices, assigns whole stripes by weighted bytes, and receives each rail into private registered memory. Older servers remain usable through explicit route mapping. The caller's buffer remains untouched until the full object is validated.

## 3. Mechanism

`KvClient::read_multi_rail_into` performs lookup and placement validation, asks one shared `RailReader` to plan and transfer the stripes, checks completeness and optional per-stripe xxh3, repeats the lookup to compare descriptor and placement identity, then copies the complete object under the cancellation gate. Each rail owns its QP, CQ and MR. Resource reservations cover both concurrent requests and individual rails. An uncertain WRITE completion retires the connection while its source remains pinned until QP destruction. One configured rail follows the same path and remains supported.

The transport state machine is deterministic. An LLM or agent is not placed on the latency-sensitive data path because scheduling, bounds and memory ownership need explicit, testable rules. A future operator agent could diagnose metrics or suggest placement changes without controlling the safety path.

## 4. Evidence

- [Independent draft PR #32](https://github.com/DaoCloud/ContextStore/pull/32) starts from official `main`, keeps the existing object layout, and contains the implementation, tests and reusable deployment scripts.
- Two isolated KVM guests with two separate tap/bridge/RXE paths completed a 64 MiB / 16-stripe object read over real Verbs; each rail carried 32 MiB. The [four-minute demo and raw live command record](https://github.com/yanchaomei/ContextStore/releases/tag/multi-rail-softroce-demo-2026-10-02) show link shutdown/recovery, corruption/recovery and late second-rail completion.
- The first [ten-block software-RoCE evaluation](paper/evaluation-results.md) showed modest apparent two-Rail gains, but its one-Rail path copied a complete object needlessly. After a safe one-Rail handoff, the [repeated matrix](paper/baseline-ablation-results.md) found 0.904× dual/single throughput at 64 MiB/concurrency 1 (all ten blocks favor one Rail); the other three cells' intervals cross parity. This is a baseline-sensitive negative result on two KVM guests sharing one host and virtual disk.
- A separate [single-Rail physical HCA code-revision ablation](paper/baseline-ablation-results.md) confirms the copy cost: removing it improved the same 64 MiB read by 1.426× across ten paired blocks and reduced CPU/RSS. A link-capacity calibration reaches 97.94 Gb/s, so the measured application load is not demonstrated to be NIC-limited. There is no physical dual-HCA aggregation measurement.

## 5. Next decision

Maintainer review should settle whether the explicit listener map belongs in an extended placement protocol and whether checksum verification should become a migration default. Once two independent physical HCA paths and independently measured disk supply are available, measure HCA counters, per-rail utilization, CPU, NUMA/PCIe placement and disk bandwidth under the same single/dual object and concurrency conditions. Those results decide whether the next bottleneck is network, disk, memory or software overhead.

**Evidence boundary:** Two RXE rails share one host and one virtual disk. They prove real Verbs scheduling and safety behavior, not HCA offload, separate NVMe bandwidth or physical multi-NIC aggregation. Community approval, official CI execution and competition submission are separate gates.
