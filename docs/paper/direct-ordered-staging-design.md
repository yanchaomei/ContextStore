# Experimental design: direct-to-ordered private staging for multiple Rails

**Date:** 2026-10-09. **Branch:** `research/direct-ordered-staging`, forked from the verified research baseline `c2716dc`. This is a proposed internal optimization, not an accepted paper contribution or a replacement for the existing default path.

## Why this experiment

After removing a redundant one-Rail copy, the ten-block Soft-RoCE matrix no longer shows a reliable two-Rail benefit: 64 MiB/concurrency 1 gives 0.904× two/one throughput with all ten blocks favoring one Rail. The current two-Rail path receives into a private vector per Rail, allocates a second object-sized vector, then copies every stripe into it. The candidate keeps caller-buffer isolation but has Rails write directly into disjoint ranges of one private, object-ordered staging vector. It is useful only if the extra QP/control costs and source bandwidth permit a gain; the experiment must preserve a negative outcome if they do not.

[Mooncake's FAST '25 paper](https://www.usenix.org/system/files/fast25-qin.pdf) already describes one transfer sliced across multiple RDMA NICs, topology-aware path selection, endpoint pooling and physical multi-NIC bandwidth measurements (§3.2.3, §5.4). Neither multiple NICs nor zero-copy transfer is claimed as new here. The narrow question is whether ContextStore's versioned striped-object and late-WRITE safety contract can be implemented with less private-copy overhead.

## Considered implementations

1. **Register the whole staging vector on every Rail and send disjoint WRITE ranges.** This preserves the existing greedy stripe mapping, but requires overlapping MRs from separate devices, raw writable pointers across threads, a stronger unsafe aliasing argument and `R × object_size` registered bytes. The rdma-core `ibv_reg_mr` manual does not explicitly guarantee the behavior we need for every provider. Defer.
2. **Contiguous weighted stripe groups, safe disjoint slices (chosen).** Only when all stripes have the same advertised owner endpoint and at least two eligible Rails, partition consecutive stripes among Rails in proportion to configured weights. Allocate one private object vector, split it into nonoverlapping mutable slices, give one slice to each scoped worker, and register that slice on its local device. Each worker also registers a separate one-stripe dummy slice for SGE entries it does not own. This uses ordinary Rust borrows and keeps registered bytes near `D + R×stripe_size`. The planner may be less balanced than interleaved greedy scheduling if stripe read times differ, so evaluate both under identical conditions.
3. **Retain per-Rail packed buffers and pool/reuse them.** This could reduce allocation cost but retains the full assembly copy, adds idle-memory accounting and increases memory-reuse obligations after uncertain completions. It does not answer the direct-staging question.

## Preconditions and fallback

The direct mode is opt-in in an internal limit/configuration field and in the research benchmark CLI. It is used only for a striped descriptor with at least two chosen Rails, a complete validated placement, one actual owner/advertised endpoint for all stripes, and enough stripes to give each Rail at least one. Other cases use the existing validated greedy planner and packed-buffer assembly. The disk layout, descriptor identity, server SGE protocol and upper `read_multi_rail_into` interface stay unchanged.

The contiguous planner first performs the existing descriptor/placement validation. It grants one stripe to each participating Rail, apportions remaining stripes by positive route weights, breaks ties by route order, then assigns consecutive index runs. Each task's packed offset is its position within its own run; object offsets remain the original layout. A validation pass proves that the runs partition `[0,D)` exactly, do not overlap, and each stripe's local span lies within its Rail slice. If any condition fails, the direct plan is rejected before a QP or MR is opened.

## Module interface and lifetime

The internal `RailTransport` seam gains a `fetch_into(route, task, descriptor, target: &mut [u8])` operation. The Mock adapter's default implementation may fetch a packed vector and copy into the supplied slice; the Verbs adapter registers `target` and a separate dummy stripe MR, builds one SGE entry per descriptor stripe with the appropriate address/rkey, and receives the requested subset directly into the slice. The shared staged vector is never exposed to the caller during transfer. Each scoped worker owns an exclusive nonoverlapping slice, QP/CQ, two MRs and dummy buffer. It destroys/retire its QP before dropping its MRs and returning the slice. The parent joins every worker before reading any staged byte, performing checksum/version checks or freeing the vector. If any worker fails, all workers still quiesce and no publication occurs. The final caller-buffer copy and cancellation mutex are unchanged.

The shared budget must count one object-sized private vector, all dummy buffers, the total target-plus-dummy registered bytes, aggregate in-flight object bytes, and per-Rail active/byte limits. A conservative over-reservation is safe for the first prototype but measured registered/peak memory must be reported separately from reservations; no unbounded pool is introduced.

## Claim-driven validation

1. **Planner red/green:** a two-Rail, 16-stripe known fixture must yield two consecutive 8-stripe runs with complete object coverage and disjoint target ranges; unequal weights must allocate more stripes to the higher-weight route. A multi-owner fixture must fall back to the old planner.
2. **Mock read seam:** through `RailReader::read_into_with`, two Rails reconstruct the exact object; a second-Rail failure, cancellation and checksum/version mismatch leave a sentinel destination unchanged. Resource reservations must return to zero after failure.
3. **Real Verbs smoke:** on two isolated RXE paths, one Worker reads the same 64 MiB/16-stripe object with exact hash and 32 MiB per Rail. Disable/fault each path independently, inject a late second-Rail WRITE, and verify no caller-buffer corruption or stale CQ reuse. Keep the existing path as the control.
4. **Paired performance:** compare original two-Rail assembly, direct two-Rail staging, and optimized one Rail on the same binary/server/object/cache state. Use independent AB/BA blocks, report per-Rail bytes, CPU, RSS, registered/in-flight bytes, and stage timings. Stop expansion if the direct path does not beat the fair one-Rail control or if its extra MR registration offsets saved copies.

This branch should not enter PR #32 until safety, compatibility and resource tests pass on real Verbs. It cannot prove physical HCA aggregation without two independent online HCA paths and a workload that saturates one path.
