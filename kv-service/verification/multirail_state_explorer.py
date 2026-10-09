"""Bounded explicit-state check of the stated two-Rail ownership contract.

This is an abstract model, not a model check of libibverbs or Rust code. QP
retirement is assumed to stop further WRITEs through that QP. Negative-control
transitions deliberately remove one guard to show that the checked invariants
can reject the corresponding unsafe execution.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import deque
from dataclasses import dataclass, replace
from pathlib import Path


NEW, TRANSFER, QUIESCED, CHECKED, PUBLISHED, FAILED = range(6)
QP_ABSENT, QP_OPEN, QP_RETIRED = range(3)
MR_ABSENT, MR_REGISTERED, MR_FREED = range(3)
WR_NONE, WR_POSTED, WR_COMPLETE, WR_UNCERTAIN = range(4)
TARGET_PRIVATE, TARGET_REUSABLE = range(2)
SOURCE_RELEASED, SOURCE_PINNED = range(2)
PHASE_NAMES = ("new", "transferring", "quiesced", "checked", "published", "failed")
QP_NAMES = ("absent", "open", "retired")
MR_NAMES = ("absent", "registered", "freed")
WR_NAMES = ("none", "posted", "complete", "uncertain")


@dataclass(frozen=True)
class State:
    phase: int = NEW
    qp: tuple[int, int] = (QP_ABSENT, QP_ABSENT)
    mr: tuple[int, int] = (MR_ABSENT, MR_ABSENT)
    wr: tuple[int, int] = (WR_NONE, WR_NONE)
    covered: tuple[bool, bool] = (False, False)
    target: tuple[int, int] = (TARGET_PRIVATE, TARGET_PRIVATE)
    source: tuple[int, int] = (SOURCE_RELEASED, SOURCE_RELEASED)
    version: int = 0
    snapshot: int = -1
    checked_version: int = -1
    data_valid: bool = True
    checksum_present: bool = True
    staging_valid: bool = True
    joined: bool = False
    cancel_precommit: bool = False
    caller_complete: bool = False
    reused_buffer_corrupt: bool = False


def changed(pair: tuple[int, int], index: int, value: int) -> tuple[int, int]:
    values = list(pair)
    values[index] = value
    return values[0], values[1]


def invariant_violation(state: State) -> str | None:
    if state.caller_complete and state.phase != PUBLISHED:
        return "caller bytes changed without publication"
    if state.phase == PUBLISHED and (
        not state.caller_complete
        or state.wr != (WR_COMPLETE, WR_COMPLETE)
        or state.covered != (True, True)
        or not state.joined
        or state.checked_version != state.snapshot
        or (state.checksum_present and not state.data_valid)
        or not state.staging_valid
        or state.cancel_precommit
    ):
        return "published object is incomplete, unverified, or cancelled"
    for rail in range(2):
        active_write = state.qp[rail] == QP_OPEN and state.wr[rail] in (
            WR_POSTED,
            WR_UNCERTAIN,
        )
        if active_write and state.source[rail] == SOURCE_RELEASED:
            return f"Rail {rail} can READ a released source"
    if state.reused_buffer_corrupt:
        return "late WRITE corrupted another request's buffer"
    return None


def transitions(state: State, unsafe: str | None) -> list[tuple[str, State]]:
    steps: list[tuple[str, State]] = []
    if state.phase == NEW:
        for checksum_present in (True, False):
            steps.append(
                (
                    f"Lookup0 and admit (checksum={checksum_present})",
                    replace(
                        state,
                        phase=TRANSFER,
                        qp=(QP_OPEN, QP_OPEN),
                        mr=(MR_REGISTERED, MR_REGISTERED),
                        source=(SOURCE_PINNED, SOURCE_PINNED),
                        snapshot=state.version,
                        checksum_present=checksum_present,
                    ),
                )
            )
        return steps
    if state.version == 0:
        steps.append(("Rewrite object A to B", replace(state, version=1)))
    if state.phase == TRANSFER and state.data_valid:
        steps.append(("Corrupt one stripe", replace(state, data_valid=False)))
    if state.phase not in (PUBLISHED, FAILED) and not state.cancel_precommit:
        steps.append(
            (
                "Cancel before publication",
                replace(state, phase=FAILED, cancel_precommit=True),
            )
        )
    for rail in range(2):
        if state.phase == TRANSFER and state.qp[rail] == QP_OPEN and state.wr[rail] == WR_NONE:
            steps.append(
                (f"Post WRITE r{rail}", replace(state, wr=changed(state.wr, rail, WR_POSTED)))
            )
        if state.qp[rail] == QP_OPEN and state.wr[rail] == WR_POSTED:
            steps.append(
                (
                    f"Complete WRITE r{rail}",
                    replace(
                        state,
                        wr=changed(state.wr, rail, WR_COMPLETE),
                        covered=changed(state.covered, rail, True),
                    ),
                )
            )
            if state.phase == TRANSFER:
                steps.append(
                    (
                        f"Complete short WRITE r{rail} (missing stripe bytes)",
                        replace(state, wr=changed(state.wr, rail, WR_COMPLETE)),
                    )
                )
            if state.phase == TRANSFER:
                steps.append(
                    (
                        f"Timeout r{rail} (completion uncertain)",
                        replace(state, phase=FAILED, wr=changed(state.wr, rail, WR_UNCERTAIN)),
                    )
                )
        if state.qp[rail] == QP_OPEN and state.wr[rail] == WR_UNCERTAIN:
            steps.append(
                (
                    f"Late WRITE r{rail}",
                    replace(
                        state,
                        wr=changed(state.wr, rail, WR_COMPLETE),
                        covered=changed(state.covered, rail, True),
                        reused_buffer_corrupt=(
                            state.reused_buffer_corrupt or state.target[rail] == TARGET_REUSABLE
                        ),
                    ),
                )
            )
        if state.qp[rail] == QP_OPEN:
            steps.append(
                (f"Retire QP/CQ r{rail}", replace(state, qp=changed(state.qp, rail, QP_RETIRED)))
            )
        can_release_target = state.qp[rail] == QP_RETIRED or (
            unsafe == "reuse" and state.wr[rail] == WR_UNCERTAIN
        )
        can_release_source = state.qp[rail] == QP_RETIRED or (
            unsafe == "source" and state.wr[rail] == WR_UNCERTAIN
        )
        if can_release_target and state.mr[rail] == MR_REGISTERED:
            steps.append(
                (f"Deregister MR r{rail}", replace(state, mr=changed(state.mr, rail, MR_FREED)))
            )
        if can_release_source and state.source[rail] == SOURCE_PINNED:
            steps.append(
                (
                    f"Release source r{rail}",
                    replace(state, source=changed(state.source, rail, SOURCE_RELEASED)),
                )
            )
        # A direct-staging slice remains request-owned after QP retirement.
        # Its bytes are still needed for checksum/identity validation and the
        # final caller publication. Only a terminal request may recycle it.
        can_reuse_staging = (
            state.joined and state.phase in (FAILED, PUBLISHED)
        ) or (unsafe == "staging" and state.phase == CHECKED) or (
            unsafe == "reuse" and state.wr[rail] == WR_UNCERTAIN
        )
        if can_release_target and can_reuse_staging and state.mr[rail] == MR_FREED and state.target[rail] == TARGET_PRIVATE:
            steps.append(
                (
                    f"Reuse target r{rail}",
                    replace(
                        state,
                        target=changed(state.target, rail, TARGET_REUSABLE),
                        staging_valid=state.staging_valid and state.phase in (FAILED, PUBLISHED),
                    ),
                )
            )
    if (
        state.phase == TRANSFER
        and state.qp == (QP_RETIRED, QP_RETIRED)
        and state.wr == (WR_COMPLETE, WR_COMPLETE)
    ):
        steps.append(("Join all Rail workers", replace(state, phase=QUIESCED, joined=True)))
    if (
        state.phase == FAILED
        and not state.joined
        and state.qp == (QP_RETIRED, QP_RETIRED)
    ):
        steps.append(("Join failed Rail workers", replace(state, joined=True)))
    if state.phase == QUIESCED:
        if (
            (not state.checksum_present or state.data_valid)
            and state.version == state.snapshot
            and state.covered == (True, True)
            and state.target == (TARGET_PRIVATE, TARGET_PRIVATE)
        ):
            steps.append(
                (
                    "Check coverage, checksums and Lookup1 identity",
                    replace(state, phase=CHECKED, checked_version=state.version),
                )
            )
        else:
            steps.append(("Reject corrupt or changed object", replace(state, phase=FAILED)))
    if state.phase == CHECKED and not state.cancel_precommit:
        steps.append(
            (
                "Publish complete object under cancel gate",
                replace(state, phase=PUBLISHED, caller_complete=True),
            )
        )
    if (
        unsafe == "partial"
        and state.phase == QUIESCED
        and state.covered != (True, True)
        and state.version == state.snapshot
        and state.data_valid
        and state.target == (TARGET_PRIVATE, TARGET_PRIVATE)
    ):
        steps.append(
            (
                "UNSAFE accept a short completed stripe",
                replace(state, phase=CHECKED, checked_version=state.snapshot),
            )
        )
    if unsafe == "version" and state.phase == QUIESCED and state.version != state.snapshot:
        steps.append(
            ("UNSAFE accept mismatched Lookup1", replace(state, phase=CHECKED, checked_version=state.version))
        )
    if unsafe == "cancel" and state.phase == FAILED and state.cancel_precommit and state.checked_version == state.snapshot and state.covered == (True, True):
        steps.append(
            (
                "UNSAFE publish after cancel returns",
                replace(state, phase=PUBLISHED, caller_complete=True),
            )
        )
    return steps


def describe(state: State) -> dict:
    return {
        "phase": PHASE_NAMES[state.phase],
        "qp": [QP_NAMES[value] for value in state.qp],
        "mr": [MR_NAMES[value] for value in state.mr],
        "wr": [WR_NAMES[value] for value in state.wr],
        "covered": list(state.covered),
        "target_reusable": [value == TARGET_REUSABLE for value in state.target],
        "source_pinned": [value == SOURCE_PINNED for value in state.source],
        "version": state.version,
        "snapshot": state.snapshot,
        "checked_version": state.checked_version,
        "checksum_present": state.checksum_present,
        "data_valid": state.data_valid,
        "staging_valid": state.staging_valid,
        "joined": state.joined,
        "cancel_precommit": state.cancel_precommit,
        "caller_complete": state.caller_complete,
        "reused_buffer_corrupt": state.reused_buffer_corrupt,
    }


def explore(unsafe: str | None, max_states: int) -> dict:
    initial = State()
    queue = deque([initial])
    previous: dict[State, tuple[State, str] | None] = {initial: None}
    while queue:
        state = queue.popleft()
        violation = invariant_violation(state)
        if violation:
            trace = []
            cursor = state
            while previous[cursor] is not None:
                parent, event = previous[cursor]
                trace.append({"event": event, "state": describe(cursor)})
                cursor = parent
            trace.reverse()
            return {
                "unsafe_mode": unsafe,
                "explored_states": len(previous),
                "violation": violation,
                "counterexample": trace,
                "complete": False,
            }
        for event, successor in transitions(state, unsafe):
            if successor not in previous:
                if len(previous) >= max_states:
                    return {
                        "unsafe_mode": unsafe,
                        "explored_states": len(previous),
                        "violation": None,
                        "counterexample": [],
                        "complete": False,
                        "truncated": True,
                    }
                previous[successor] = state, event
                queue.append(successor)
    return {
        "unsafe_mode": unsafe,
        "explored_states": len(previous),
        "violation": None,
        "counterexample": [],
        "complete": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-states", type=int, default=250_000)
    args = parser.parse_args()
    if args.max_states < 1:
        parser.error("max-states must be positive")
    model = Path(__file__)
    runs = [
        explore(mode, args.max_states)
        for mode in (None, "reuse", "staging", "source", "partial", "cancel", "version")
    ]
    if not runs[0]["complete"] or runs[0]["violation"] is not None:
        raise RuntimeError("safe model failed or was truncated")
    if any(run["violation"] is None for run in runs[1:]):
        raise RuntimeError("one unsafe negative control did not produce a counterexample")
    result = {
        "model_sha256": hashlib.sha256(model.read_bytes()).hexdigest(),
        "scope": "one request, two Rails, one stripe per Rail, two object versions, one late WRITE per Rail",
        "provider_assumption": "retiring a QP prevents future WRITEs through it; not proved by this model",
        "interpretation": "abstract state exploration only; not a proof of ContextStore Rust/Verbs implementation or process-crash safety",
        "runs": runs,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(
        args.output,
        [(run["unsafe_mode"], run["explored_states"], run["violation"]) for run in runs],
    )


if __name__ == "__main__":
    main()
