# Safety contract and proof obligations for one multi-Rail object read

**Status:** implementation-linked argument and test map, not a machine-checked proof. **Scope:** a single process that remains alive, one object read through the current ContextStore Verbs protocol, and orderly QP teardown after any local error. Process crashes, NIC firmware defects and unannounced in-place mutation of stored bytes are outside this argument.

## State and linearization points

For request `q`, let `V0=(handle,generation,ETag,layout_version,placement)` be the identity returned by its first `LookupObject`; `V1` is the identity returned after every Rail worker returns. Let `C` be the caller-owned destination, `P_r` a private registered receive buffer for Rail `r`, `Q_r` its RC QP/CQ, and `S_r` any server-side source extent/MR that can still feed a posted WRITE. `W_r` is the set of WRITE work requests whose termination is not yet established. The request has states:

```text
NEW → PLANNED → TRANSFERRING → QUIESCED → VERIFIED → RECHECKED → PUBLISHED
                                    ↘ FAILED/RETIRED ↗
```

The state `QUIESCED` means all started workers have joined; every client QP that might still accept data into `P_r` is retired before `P_r` is deregistered or freed. It does **not** mean a failed request's bytes are valid. `PUBLISHED` is reached only while the cancellation publication mutex is held; `cancel()` uses the same mutex. If publication acquires the mutex first, cancellation linearizes afterward. If cancellation acquires it first, no copy occurs. The second metadata lookup is an identity check at that instant; the design does not claim a linearizable read at a later arbitrary time.

## Invariants

1. **Caller-buffer isolation.** In `NEW` through `RECHECKED` and on every failure, `C` equals its entry value. Only the final publication copy may change `C`.
2. **Version and coverage.** Publication requires `V0=V1`, exact object size, one nonoverlapping destination range for every planned stripe, the expected completed stripe count/byte count from each Rail, and successful verification for every supplied stripe checksum. The storage system must assign a new identity for a new version and must not mutate a version's bytes in place without changing identity or failing the enabled checksum.
3. **Client receive lifetime.** While a remote WRITE can target any byte of `P_r`, that byte is allocated, registered, and owned by request `q`. No other request may reuse it. On uncertain completion, `Q_r` is retired before the MR/`P_r` is released; any late completion belongs to the retired QP/CQ rather than a later request.
4. **Server source lifetime.** While a posted WRITE may read `S_r`, the source remains pinned and registered. If polling cannot establish termination, the server retains its slab extent or `(MR, Bytes)` source until QP destruction and does not reuse that connection.
5. **Bounded admission.** A new transfer is admitted only after reserving aggregate staging, registered and in-flight byte budgets plus per-Rail request/byte limits. A failed reservation creates no QP or registered target for that request.

The memory-lifetime statements rely on the Verbs QP-destruction and MR-deregistration ordering supported by the provider. The present real-RXE/HCA fault runs exercise this ordering; they are not a proof for every provider, firmware version or process-crash scenario.

## Implementation correspondence

| Obligation | Implementation point | Evidence |
| --- | --- | --- |
| Plan/validate descriptor, stripe range, ownership and route before data movement | `rail_read.rs` planner and `RailReader::read_staged_with`; server stripe-subset validation | `rail_read.rs` unit suite and RXE object reconstruction |
| Admit with finite total/per-Rail capacity | `RailReader::reserve` and `BudgetGuard` | resource-limit unit tests, per-Rail snapshots |
| Keep all started workers until safe return | scoped worker creation/join in `read_staged_with` | cancellation and partial-failure tests |
| Move a complete one-Rail receive vector into private staged ownership without an extra copy | `read_staged_with` checks one task, exact object length and packed/object offset equality; Verbs fetch has already retired QP/MR | pointer-identity red/green unit test, single-Rail checksum sentinel, physical single-HCA revision ablation |
| Retire client QP before receive MR and buffer | `VerbsTransport::fetch` explicitly drops `RdmaClient` before `RegisteredBuffer`; `RdmaClient::drop` destroys its QP | late-WRITE RXE injection and timeout tests |
| Retain uncertain server source and retire connection | `server/src/rdma/server.rs` subset and legacy paths; `server/src/rdma/qp.rs` retained sources and QP drop | injected delayed WRITE, CQ timeout and legacy-path tests |
| Check complete payload/version before touching caller | `commit_if_unchanged` after post-transfer `LookupObject` in `KvClient::read_multi_rail_into` | version-change, checksum, missing-stripe and sentinel tests |
| Serialize cancellation with publication | `RailCancel::cancel` and `publish_if_live` share the mutex across the complete copy | cancellation during one/two-Rail reads and buffer-reuse tests |

## Argument by transition

At admission, `C` is never registered for the new RDMA transfer; each `P_r` belongs only to `q`. Transfers can corrupt an incomplete `P_r` after a timeout, but that buffer is private and cannot affect `C`. Workers either observe complete responses or return errors. Scoped joining prevents the parent from releasing any worker-owned memory before workers have returned. Each worker retires its QP before dropping its receive MR, so a later WRITE cannot address a reused application allocation on a live connection. The server takes the symmetric precaution for uncertain source lifetime. Multi-Rail assembly copies only returned, exact-length Rail buffers into a new private object buffer; when one Rail owns every ordered stripe, its complete private receive vector is moved directly into staged ownership. Stripe checksums and the second version lookup gate publication in both cases. On an error, there is no path to `commit_if_unchanged`; on success, the final copy is guarded against concurrent `cancel()`.

This is an inductive *sketch*: it assumes the actual Verbs operations obey the QP retirement contract, every error path follows the same destructor ordering, and the descriptor/placement identity captures every version-changing write. Those assumptions require further provider-level testing and a systematic check of all error branches before the argument can be called a proof.

## Counterexamples avoided and remaining gaps

- **Direct destination WRITE:** if a Rail writes into `C` and a peer fails, the error path has already changed the caller's buffer. Private `P_r` prevents this trace.
- **Free on timeout:** if the client frees `P_r` while `Q_r` is still live, a delayed WRITE can hit an allocator-reused address. QP retirement before deregistration/free prevents this trace under the stated Verbs contract.
- **Early source reuse:** if the server releases its slab source while a WRITE is pending, the NIC may read unrelated bytes. Retained source ownership until QP destruction prevents this trace.
- **Version race:** if metadata changes between `V0` and `V1`, the request fails without publishing; a rewrite after `V1` can still be consistent with a snapshot observed at `V1`, but the API does not promise that the object remains current after return.
- **Unchecked bytes:** when descriptors omit checksums, the design proves range/length/version matching but cannot detect arbitrary silent bit corruption. A paper claim of full data integrity must require checksums or an end-to-end digest.

Next research step: encode the transitions above in a bounded state explorer or TLA+ specification, including all failure branches and two independently delayed Rails, then link counterexample traces to the actual fault-injection harness. Until that is complete, describe the result as tested invariants plus an implementation-linked argument.
