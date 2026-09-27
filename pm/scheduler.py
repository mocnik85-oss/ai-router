"""PM runtime — dependency graph and deterministic batch scheduler
(MVP-003).

This module is the PM-side dependency graph and batch scheduler.  It
consumes the existing V1.1 :class:`~protocol.artifacts.TaskArtifact`
dependency representation and the authoritative task states from the
PM-owned register, and produces a deterministic, dependency-safe
execution plan:

    TaskArtifact dependencies + authoritative TaskState
        -> DependencyGraph            (validated, acyclic)
        -> schedule_execution         (deterministic plan)
        -> ExecutionPlan              (ordered, dependency-safe batches)
        -> PMRuntime.schedule_batches (PMBatch records, PM-owned)

Authority boundaries this module keeps:

- **PM state authority stays with the PM.**  The scheduler never
  writes to ``project/TASKS.md`` or ``project/PROJECT_STATE.md``,
  never transitions a lifecycle state, and never creates or approves
  a :class:`~pm.batch.PMBatch` by itself.  It produces a *plan* —
  ordered batches of task identities — that the Project Manager may
  then turn into PM-owned batch records through
  :meth:`pm.runtime.PMRuntime.schedule_batches`.
- **Fail closed.**  Malformed input, an unknown dependency, a
  self-dependency, or a dependency cycle raises a
  :class:`~pm.errors.DependencyError` before any plan is produced.
- **Deterministic.**  Equivalent input orderings (same tasks, same
  dependencies, same states, supplied in a different order) produce
  byte-identical plans: every tie is broken by task identity.
- **Backend-independent.**  Only the standard library plus
  ``pm.errors`` and ``protocol.artifacts`` is used — no
  ``router``/``providers`` module, no network, no credential access,
  no external action.

The scheduler performs no lifecycle transition and grants no
permission: a scheduled batch is a recommendation the Project Manager
reviews, approves, and hands off through the existing PM gates.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass
from typing import Mapping

from pm.errors import (
    DependencyCycleError,
    DependencyError,
    SelfDependencyError,
    UnknownDependencyError,
)
from protocol.artifacts import (
    ArtifactValidationError,
    TaskArtifact,
    TaskState,
    _require_identity,
)


# ------------------------------------------------------------------
# Scheduled plan
# ------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ScheduledBatch:
    """One dependency-safe batch in a scheduled execution plan.

    ``batch_number`` is 1-indexed.  ``task_ids`` is sorted and
    contains no repeat, so two batches with the same membership are
    always equal.
    """

    batch_number: int
    task_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.batch_number, int) or self.batch_number < 1:
            raise ArtifactValidationError(
                "batch_number must be a positive integer"
            )
        if isinstance(self.task_ids, (str, bytes)) or not isinstance(
            self.task_ids, (tuple, list)
        ):
            raise ArtifactValidationError(
                "task_ids must be a sequence of task identities"
            )
        entries = tuple(self.task_ids)
        for entry in entries:
            if not isinstance(entry, str) or not entry.strip():
                raise ArtifactValidationError(
                    f"task_ids contains an invalid entry: {entry!r}"
                )
        if not entries:
            raise ArtifactValidationError(
                "a scheduled batch must not be empty"
            )
        if len(set(entries)) != len(entries):
            raise ArtifactValidationError(
                "a scheduled batch must not repeat a task"
            )
        if entries != tuple(sorted(entries)):
            raise ArtifactValidationError(
                "a scheduled batch must be sorted by task identity"
            )
        object.__setattr__(self, "task_ids", entries)


@dataclass(frozen=True, slots=True)
class ExecutionPlan:
    """A deterministic, dependency-safe execution plan.

    The plan is an ordered sequence of :class:`ScheduledBatch` records.
    Every task in a batch has all of its dependencies either already
    ``COMPLETED`` (per the authoritative register state) or in an
    earlier batch, so the batches may be executed in order without
    violating any dependency.

    ``topological_order`` is the concatenation of the batches: a
    valid topological order of every scheduled task.
    """

    batches: tuple[ScheduledBatch, ...]

    def __post_init__(self) -> None:
        if isinstance(self.batches, (str, bytes)) or not isinstance(
            self.batches, (tuple, list)
        ):
            raise ArtifactValidationError(
                "batches must be a sequence of ScheduledBatch records"
            )
        entries = tuple(self.batches)
        seen: set[str] = set()
        expected_number = 1
        for batch in entries:
            if not isinstance(batch, ScheduledBatch):
                raise ArtifactValidationError(
                    "batches must contain ScheduledBatch records"
                )
            if batch.batch_number != expected_number:
                raise ArtifactValidationError(
                    "batch numbers must be consecutive and 1-indexed; "
                    f"expected {expected_number}, got {batch.batch_number}"
                )
            expected_number += 1
            for task_id in batch.task_ids:
                if task_id in seen:
                    raise ArtifactValidationError(
                        f"task {task_id!r} appears in more than one batch"
                    )
                seen.add(task_id)
        object.__setattr__(self, "batches", entries)

    @property
    def topological_order(self) -> tuple[str, ...]:
        """Every scheduled task identity in dependency order."""
        return tuple(
            task_id for batch in self.batches for task_id in batch.task_ids
        )

    @property
    def is_empty(self) -> bool:
        """Whether the plan schedules no work."""

        return not self.batches

    def batch_of(self, task_id: str) -> int:
        """The 1-indexed batch number that schedules *task_id*.

        Raises :class:`KeyError` when *task_id* is not scheduled.
        """

        for batch in self.batches:
            if task_id in batch.task_ids:
                return batch.batch_number
        raise KeyError(task_id)


# ------------------------------------------------------------------
# Dependency graph
# ------------------------------------------------------------------


class DependencyGraph:
    """A validated dependency graph over task identities.

    Construction validates the graph fail-closed:

    - every key must be a non-empty identity matching its
      :class:`~protocol.artifacts.TaskArtifact`'s ``task_id``;
    - every referenced dependency must exist in the graph
      (:class:`~pm.errors.UnknownDependencyError`);
    - no task may depend on itself
      (:class:`~pm.errors.SelfDependencyError`);
    - the graph must be acyclic (:class:`~pm.errors.DependencyCycleError`).

    The graph is immutable after construction.
    """

    def __init__(self, tasks: Mapping[str, TaskArtifact]) -> None:
        if isinstance(tasks, (str, bytes)) or not isinstance(tasks, Mapping):
            raise ArtifactValidationError(
                "tasks must be a mapping of task identity to TaskArtifact"
            )
        if not tasks:
            raise ArtifactValidationError(
                "a dependency graph requires at least one task"
            )

        self._tasks: dict[str, TaskArtifact] = {}
        for key, task in tasks.items():
            if not isinstance(key, str) or not key.strip():
                raise ArtifactValidationError(
                    f"task identity must be a non-empty string, got {key!r}"
                )
            if not isinstance(task, TaskArtifact):
                raise ArtifactValidationError(
                    f"task {key!r} must be a TaskArtifact"
                )
            if task.task_id != key:
                raise ArtifactValidationError(
                    f"mapping key {key!r} does not match task_id "
                    f"{task.task_id!r}"
                )
            self._tasks[key] = task

        self._validate()

    def _validate(self) -> None:
        """Fail closed on unknown, self, or cyclic dependencies."""

        for task_id in sorted(self._tasks):
            for dependency in self._tasks[task_id].dependencies:
                if dependency not in self._tasks:
                    raise UnknownDependencyError(
                        f"task {task_id!r} depends on unknown task "
                        f"{dependency!r}"
                    )
                if dependency == task_id:
                    raise SelfDependencyError(
                        f"task {task_id!r} depends on itself"
                    )

        self._reject_cycles()

    def _reject_cycles(self) -> None:
        """DFS-based cycle detection with a precise cycle path."""

        WHITE, GRAY, BLACK = 0, 1, 2
        color: dict[str, int] = {task_id: WHITE for task_id in self._tasks}

        def visit(node: str, path: list[str]) -> None:
            color[node] = GRAY
            for dependency in self._tasks[node].dependencies:
                if color[dependency] == GRAY:
                    cycle_start = path.index(dependency)
                    cycle = path[cycle_start:] + [dependency]
                    raise DependencyCycleError(
                        "dependency cycle detected: " + " -> ".join(cycle)
                    )
                if color[dependency] == WHITE:
                    visit(dependency, path + [dependency])
            color[node] = BLACK

        for task_id in sorted(self._tasks):
            if color[task_id] == WHITE:
                visit(task_id, [task_id])

    @property
    def task_ids(self) -> tuple[str, ...]:
        """Every task identity in the graph, sorted."""

        return tuple(sorted(self._tasks))

    def dependencies_of(self, task_id: str) -> tuple[str, ...]:
        """The direct dependencies of *task_id* (sorted, fail-closed)."""

        try:
            task = self._tasks[task_id]
        except KeyError:
            raise UnknownDependencyError(
                f"task {task_id!r} is not in the dependency graph"
            ) from None
        return tuple(sorted(task.dependencies))

    @property
    def topological_order(self) -> tuple[str, ...]:
        """Deterministic topological order of every task (Kahn's algorithm).

        Ties are broken by task identity (a min-heap always yields the
        smallest ready identity), so the order is unique for a given
        graph and independent of construction order.
        """

        in_degree: dict[str, int] = {task_id: 0 for task_id in self._tasks}
        children: dict[str, list[str]] = {
            task_id: [] for task_id in self._tasks
        }
        for task_id in self._tasks:
            for dependency in self._tasks[task_id].dependencies:
                children[dependency].append(task_id)
                in_degree[task_id] += 1

        ready: list[str] = [
            task_id for task_id, degree in in_degree.items() if degree == 0
        ]
        heapq.heapify(ready)

        order: list[str] = []
        while ready:
            node = heapq.heappop(ready)
            order.append(node)
            for child in children[node]:
                in_degree[child] -= 1
                if in_degree[child] == 0:
                    heapq.heappush(ready, child)

        return tuple(order)


# ------------------------------------------------------------------
# Scheduling
# ------------------------------------------------------------------


def _coerce_states(states: Mapping[str, object]) -> dict[str, TaskState]:
    """Validate the *states* mapping and coerce values to ``TaskState``."""

    if isinstance(states, (str, bytes)) or not isinstance(states, Mapping):
        raise ArtifactValidationError(
            "states must be a mapping of task identity to TaskState"
        )
    coerced: dict[str, TaskState] = {}
    for key, value in states.items():
        if not isinstance(key, str) or not key.strip():
            raise ArtifactValidationError(
                f"state identity must be a non-empty string, got {key!r}"
            )
        if isinstance(value, TaskState):
            coerced[key] = value
        elif isinstance(value, str):
            try:
                coerced[key] = TaskState(value)
            except ValueError as exc:
                raise ArtifactValidationError(
                    f"state for task {key!r} is not a V1.1 task state: "
                    f"{value!r}"
                ) from exc
        else:
            raise ArtifactValidationError(
                f"state for task {key!r} must be a TaskState, got "
                f"{value!r}"
            )
    return coerced


def schedule_execution(
    tasks: Mapping[str, TaskArtifact],
    states: Mapping[str, TaskState | str],
) -> ExecutionPlan:
    """Produce a deterministic, dependency-safe execution plan.

    Steps:

    1. validate the dependency graph fail-closed
       (:class:`DependencyGraph`);
    2. validate that every task has an authoritative state;
    3. treat ``COMPLETED`` tasks as already satisfied — they are
       excluded from the plan but release their dependents;
    4. repeatedly form the next batch from every remaining task whose
       dependencies are all satisfied, until no task remains.

    The result is deterministic: equivalent input orderings produce
    identical plans, and every batch is sorted by task identity.
    """

    graph = DependencyGraph(tasks)
    coerced_states = _coerce_states(states)

    for task_id in graph.task_ids:
        if task_id not in coerced_states:
            raise DependencyError(
                f"no authoritative state recorded for task {task_id!r}"
            )

    satisfied: set[str] = {
        task_id
        for task_id in graph.task_ids
        if coerced_states[task_id] is TaskState.COMPLETED
    }
    remaining: set[str] = set(graph.task_ids) - satisfied

    batches: list[ScheduledBatch] = []
    batch_number = 1
    while remaining:
        ready = {
            task_id
            for task_id in remaining
            if all(
                dependency in satisfied
                for dependency in graph.dependencies_of(task_id)
            )
        }
        if not ready:
            # Unreachable for a validated DAG; fail closed regardless.
            raise DependencyCycleError(
                "scheduling stalled: remaining tasks have unsatisfied "
                f"dependencies: {sorted(remaining)}"
            )
        batches.append(
            ScheduledBatch(
                batch_number=batch_number, task_ids=tuple(sorted(ready))
            )
        )
        satisfied |= ready
        remaining -= ready
        batch_number += 1

    return ExecutionPlan(batches=tuple(batches))
