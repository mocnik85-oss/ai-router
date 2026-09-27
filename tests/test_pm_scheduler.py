"""MVP-003 focused tests — dependency graph and deterministic batch scheduler.

Evidence produced here (offline only — no network, model, provider, or
real backend call):

- the dependency graph validates fail-closed: unknown dependencies,
  self-dependencies, and multi-node cycles are all refused before any
  plan is produced;
- readiness is derived from authoritative task state: a completed
  dependency releases its dependent, an unfinished dependency prevents
  readiness;
- independent tasks are scheduled together;
- the topological ordering is deterministic and independent of
  equivalent input orderings;
- dependency-safe batch formation is deterministic: every task in a
  batch has all dependencies satisfied by earlier batches or by
  already-completed tasks;
- repeated scheduling over equivalent inputs produces identical plans;
- the scheduler exposes no lifecycle-transition API and rewrites no
  PM-owned register — PM ownership of state transitions is preserved.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pm import (
    DependencyCycleError,
    DependencyGraph,
    ExecutionPlan,
    PMRuntime,
    PMBatch,
    ProjectContext,
    ScheduledBatch,
    SelfDependencyError,
    UnknownDependencyError,
    schedule_execution,
)
from pm.errors import PMRuntimeError
from protocol.artifacts import (
    ArtifactValidationError,
    ProjectArtifact,
    TaskArtifact,
    TaskState,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

PROJECT_ID = "AI Router / JEV"
PROJECT_VERSION = "v1.1-migration"
PROTOCOL_VERSION = "V1.1"

TASK_A = "TASK-A"
TASK_B = "TASK-B"
TASK_C = "TASK-C"
TASK_D = "TASK-D"
TASK_E = "TASK-E"


def _task(task_id: str, dependencies: tuple[str, ...] = ()) -> TaskArtifact:
    return TaskArtifact(
        task_id=task_id,
        task_version="v1",
        requirement_version=PROTOCOL_VERSION,
        state=TaskState.PENDING,
        dependencies=dependencies,
    )


def _states(*pairs: tuple[str, TaskState]) -> dict[str, TaskState]:
    return dict(pairs)


def _all_pending(*task_ids: str) -> dict[str, TaskState]:
    return {task_id: TaskState.PENDING for task_id in task_ids}


# ------------------------------------------------------------------
# Valid dependency graph
# ------------------------------------------------------------------


class TestValidDependencyGraph:
    """A well-formed dependency graph is accepted and queried."""

    def test_empty_dependencies_produces_single_batch(self) -> None:
        tasks = {TASK_A: _task(TASK_A)}
        plan = schedule_execution(tasks, _all_pending(TASK_A))

        assert plan.topological_order == (TASK_A,)
        assert len(plan.batches) == 1
        assert plan.batches[0].task_ids == (TASK_A,)

    def test_linear_chain_produces_ordered_batches(self) -> None:
        tasks = {
            TASK_A: _task(TASK_A),
            TASK_B: _task(TASK_B, (TASK_A,)),
            TASK_C: _task(TASK_C, (TASK_B,)),
        }
        plan = schedule_execution(tasks, _all_pending(TASK_A, TASK_B, TASK_C))

        assert plan.topological_order == (TASK_A, TASK_B, TASK_C)
        assert [b.task_ids for b in plan.batches] == [
            (TASK_A,),
            (TASK_B,),
            (TASK_C,),
        ]

    def test_diamond_dependency_batches_correctly(self) -> None:
        tasks = {
            TASK_A: _task(TASK_A),
            TASK_B: _task(TASK_B, (TASK_A,)),
            TASK_C: _task(TASK_C, (TASK_A,)),
            TASK_D: _task(TASK_D, (TASK_B, TASK_C)),
        }
        plan = schedule_execution(
            tasks, _all_pending(TASK_A, TASK_B, TASK_C, TASK_D)
        )

        assert plan.batches[0].task_ids == (TASK_A,)
        assert plan.batches[1].task_ids == (TASK_B, TASK_C)
        assert plan.batches[2].task_ids == (TASK_D,)

    def test_graph_exposes_sorted_task_ids(self) -> None:
        tasks = {
            TASK_C: _task(TASK_C),
            TASK_A: _task(TASK_A),
            TASK_B: _task(TASK_B),
        }
        graph = DependencyGraph(tasks)

        assert graph.task_ids == (TASK_A, TASK_B, TASK_C)

    def test_graph_dependencies_of_returns_sorted(self) -> None:
        tasks = {
            TASK_A: _task(TASK_A),
            TASK_B: _task(TASK_B),
            TASK_C: _task(TASK_C, (TASK_B, TASK_A)),
        }
        graph = DependencyGraph(tasks)

        assert graph.dependencies_of(TASK_C) == (TASK_A, TASK_B)

    def test_graph_topological_order_is_deterministic(self) -> None:
        tasks = {
            TASK_A: _task(TASK_A),
            TASK_B: _task(TASK_B, (TASK_A,)),
            TASK_C: _task(TASK_C),
            TASK_D: _task(TASK_D, (TASK_B, TASK_C)),
        }
        graph = DependencyGraph(tasks)

        order = graph.topological_order
        assert order.index(TASK_A) < order.index(TASK_B)
        assert order.index(TASK_B) < order.index(TASK_D)
        assert order.index(TASK_C) < order.index(TASK_D)


# ------------------------------------------------------------------
# Unknown dependency
# ------------------------------------------------------------------


class TestUnknownDependency:
    """A dependency on a task not in the graph fails closed."""

    def test_unknown_dependency_rejected(self) -> None:
        tasks = {TASK_A: _task(TASK_A, ("TASK-X",))}

        with pytest.raises(UnknownDependencyError):
            schedule_execution(tasks, _all_pending(TASK_A))

    def test_unknown_dependency_rejected_in_graph(self) -> None:
        tasks = {TASK_A: _task(TASK_A, ("TASK-X",))}

        with pytest.raises(UnknownDependencyError):
            DependencyGraph(tasks)

    def test_unknown_dependency_after_valid_tasks_rejected(self) -> None:
        tasks = {
            TASK_A: _task(TASK_A),
            TASK_B: _task(TASK_B, (TASK_A,)),
            TASK_C: _task(TASK_C, ("TASK-MISSING",)),
        }

        with pytest.raises(UnknownDependencyError):
            schedule_execution(tasks, _all_pending(TASK_A, TASK_B, TASK_C))


# ------------------------------------------------------------------
# Self-dependency
# ------------------------------------------------------------------


class TestSelfDependency:
    """A task depending on itself fails closed."""

    def test_self_dependency_rejected(self) -> None:
        tasks = {TASK_A: _task(TASK_A, (TASK_A,))}

        with pytest.raises(SelfDependencyError):
            schedule_execution(tasks, _all_pending(TASK_A))

    def test_self_dependency_rejected_in_graph(self) -> None:
        tasks = {TASK_A: _task(TASK_A, (TASK_A,))}

        with pytest.raises(SelfDependencyError):
            DependencyGraph(tasks)


# ------------------------------------------------------------------
# Multi-node cycle
# ------------------------------------------------------------------


class TestMultiNodeCycle:
    """Arbitrary dependency cycles fail closed."""

    def test_two_node_cycle_rejected(self) -> None:
        tasks = {
            TASK_A: _task(TASK_A, (TASK_B,)),
            TASK_B: _task(TASK_B, (TASK_A,)),
        }

        with pytest.raises(DependencyCycleError):
            schedule_execution(tasks, _all_pending(TASK_A, TASK_B))

    def test_three_node_cycle_rejected(self) -> None:
        tasks = {
            TASK_A: _task(TASK_A, (TASK_C,)),
            TASK_B: _task(TASK_B, (TASK_A,)),
            TASK_C: _task(TASK_C, (TASK_B,)),
        }

        with pytest.raises(DependencyCycleError):
            schedule_execution(tasks, _all_pending(TASK_A, TASK_B, TASK_C))

    def test_cycle_with_valid_prefix_rejected(self) -> None:
        tasks = {
            TASK_A: _task(TASK_A),
            TASK_B: _task(TASK_B, (TASK_A, TASK_D)),
            TASK_C: _task(TASK_C, (TASK_B,)),
            TASK_D: _task(TASK_D, (TASK_C,)),
        }

        with pytest.raises(DependencyCycleError):
            schedule_execution(
                tasks, _all_pending(TASK_A, TASK_B, TASK_C, TASK_D)
            )

    def test_cycle_error_message_names_the_cycle(self) -> None:
        tasks = {
            TASK_A: _task(TASK_A, (TASK_B,)),
            TASK_B: _task(TASK_B, (TASK_A,)),
        }

        with pytest.raises(DependencyCycleError, match="TASK-A"):
            schedule_execution(tasks, _all_pending(TASK_A, TASK_B))


# ------------------------------------------------------------------
# Readiness from authoritative state
# ------------------------------------------------------------------


class TestReadinessFromState:
    """Readiness is derived from the authoritative task state."""

    def test_completed_dependency_releases_dependent(self) -> None:
        tasks = {
            TASK_A: _task(TASK_A),
            TASK_B: _task(TASK_B, (TASK_A,)),
        }
        states = _states(
            (TASK_A, TaskState.COMPLETED),
            (TASK_B, TaskState.PENDING),
        )
        plan = schedule_execution(tasks, states)

        # TASK_A is already done; only TASK_B is scheduled.
        assert plan.topological_order == (TASK_B,)
        assert plan.batches[0].task_ids == (TASK_B,)

    def test_unfinished_dependency_prevents_readiness(self) -> None:
        tasks = {
            TASK_A: _task(TASK_A),
            TASK_B: _task(TASK_B, (TASK_A,)),
        }
        states = _states(
            (TASK_A, TaskState.IMPLEMENTING),
            (TASK_B, TaskState.PENDING),
        )
        plan = schedule_execution(tasks, states)

        # TASK_A must be scheduled first; TASK_B waits.
        assert plan.batches[0].task_ids == (TASK_A,)
        assert plan.batches[1].task_ids == (TASK_B,)

    def test_all_completed_produces_empty_plan(self) -> None:
        tasks = {
            TASK_A: _task(TASK_A),
            TASK_B: _task(TASK_B, (TASK_A,)),
        }
        states = _states(
            (TASK_A, TaskState.COMPLETED),
            (TASK_B, TaskState.COMPLETED),
        )
        plan = schedule_execution(tasks, states)

        assert plan.is_empty
        assert plan.topological_order == ()

    def test_missing_state_fails_closed(self) -> None:
        tasks = {
            TASK_A: _task(TASK_A),
            TASK_B: _task(TASK_B, (TASK_A,)),
        }
        states = {TASK_A: TaskState.COMPLETED}

        with pytest.raises(PMRuntimeError, match="TASK-B"):
            schedule_execution(tasks, states)

    def test_non_v11_state_rejected(self) -> None:
        tasks = {TASK_A: _task(TASK_A)}
        states = {"TASK-A": "DONE"}

        with pytest.raises(ArtifactValidationError):
            schedule_execution(tasks, states)


# ------------------------------------------------------------------
# Independent tasks
# ------------------------------------------------------------------


class TestIndependentTasks:
    """Tasks with no dependencies are scheduled together."""

    def test_independent_tasks_share_one_batch(self) -> None:
        tasks = {
            TASK_A: _task(TASK_A),
            TASK_B: _task(TASK_B),
            TASK_C: _task(TASK_C),
        }
        plan = schedule_execution(tasks, _all_pending(TASK_A, TASK_B, TASK_C))

        assert len(plan.batches) == 1
        assert plan.batches[0].task_ids == (TASK_A, TASK_B, TASK_C)

    def test_independent_tasks_sorted_in_batch(self) -> None:
        tasks = {
            TASK_C: _task(TASK_C),
            TASK_A: _task(TASK_A),
            TASK_B: _task(TASK_B),
        }
        plan = schedule_execution(tasks, _all_pending(TASK_A, TASK_B, TASK_C))

        assert plan.batches[0].task_ids == (TASK_A, TASK_B, TASK_C)


# ------------------------------------------------------------------
# Deterministic topological ordering
# ------------------------------------------------------------------


class TestDeterministicTopologicalOrdering:
    """Equivalent input orderings produce identical topological orders."""

    def test_topological_order_independent_of_input_order(self) -> None:
        tasks_forward = {
            TASK_A: _task(TASK_A),
            TASK_B: _task(TASK_B, (TASK_A,)),
            TASK_C: _task(TASK_C),
            TASK_D: _task(TASK_D, (TASK_B, TASK_C)),
        }
        tasks_reversed = {
            TASK_D: _task(TASK_D, (TASK_B, TASK_C)),
            TASK_C: _task(TASK_C),
            TASK_B: _task(TASK_B, (TASK_A,)),
            TASK_A: _task(TASK_A),
        }
        states = _all_pending(TASK_A, TASK_B, TASK_C, TASK_D)

        graph_forward = DependencyGraph(tasks_forward)
        graph_reversed = DependencyGraph(tasks_reversed)

        assert graph_forward.topological_order == graph_reversed.topological_order

    def test_topological_order_breaks_ties_by_task_id(self) -> None:
        tasks = {
            TASK_A: _task(TASK_A),
            TASK_B: _task(TASK_B),
            TASK_C: _task(TASK_C),
        }
        graph = DependencyGraph(tasks)

        # All independent: the order is purely alphabetical.
        assert graph.topological_order == (TASK_A, TASK_B, TASK_C)


# ------------------------------------------------------------------
# Deterministic dependency-safe batch formation
# ------------------------------------------------------------------


class TestDeterministicBatchFormation:
    """Equivalent input orderings produce identical batches."""

    def test_batches_independent_of_input_order(self) -> None:
        tasks_forward = {
            TASK_A: _task(TASK_A),
            TASK_B: _task(TASK_B, (TASK_A,)),
            TASK_C: _task(TASK_C, (TASK_A,)),
            TASK_D: _task(TASK_D, (TASK_B, TASK_C)),
        }
        tasks_reversed = {
            TASK_D: _task(TASK_D, (TASK_B, TASK_C)),
            TASK_C: _task(TASK_C, (TASK_A,)),
            TASK_B: _task(TASK_B, (TASK_A,)),
            TASK_A: _task(TASK_A),
        }
        states = _all_pending(TASK_A, TASK_B, TASK_C, TASK_D)

        plan_forward = schedule_execution(tasks_forward, states)
        plan_reversed = schedule_execution(tasks_reversed, states)

        assert plan_forward == plan_reversed
        assert plan_forward.batches == plan_reversed.batches

    def test_batch_numbers_are_consecutive_and_indexed(self) -> None:
        tasks = {
            TASK_A: _task(TASK_A),
            TASK_B: _task(TASK_B, (TASK_A,)),
            TASK_C: _task(TASK_C, (TASK_B,)),
        }
        plan = schedule_execution(tasks, _all_pending(TASK_A, TASK_B, TASK_C))

        assert [b.batch_number for b in plan.batches] == [1, 2, 3]

    def test_batch_of_returns_correct_number(self) -> None:
        tasks = {
            TASK_A: _task(TASK_A),
            TASK_B: _task(TASK_B, (TASK_A,)),
            TASK_C: _task(TASK_C, (TASK_A,)),
        }
        plan = schedule_execution(tasks, _all_pending(TASK_A, TASK_B, TASK_C))

        assert plan.batch_of(TASK_A) == 1
        assert plan.batch_of(TASK_B) == 2
        assert plan.batch_of(TASK_C) == 2

    def test_batch_of_unknown_task_raises(self) -> None:
        tasks = {TASK_A: _task(TASK_A)}
        plan = schedule_execution(tasks, _all_pending(TASK_A))

        with pytest.raises(KeyError):
            plan.batch_of("TASK-X")


# ------------------------------------------------------------------
# Repeated scheduling produces identical output
# ------------------------------------------------------------------


class TestRepeatedScheduling:
    """Scheduling is a pure function: same input, same output."""

    def test_repeated_scheduling_produces_identical_plans(self) -> None:
        tasks = {
            TASK_A: _task(TASK_A),
            TASK_B: _task(TASK_B, (TASK_A,)),
            TASK_C: _task(TASK_C),
            TASK_D: _task(TASK_D, (TASK_B, TASK_C)),
            TASK_E: _task(TASK_E, (TASK_D,)),
        }
        states = _states(
            (TASK_A, TaskState.COMPLETED),
            (TASK_B, TaskState.PENDING),
            (TASK_C, TaskState.PENDING),
            (TASK_D, TaskState.PENDING),
            (TASK_E, TaskState.PENDING),
        )

        plan_one = schedule_execution(tasks, states)
        plan_two = schedule_execution(tasks, states)
        plan_three = schedule_execution(tasks, states)

        assert plan_one == plan_two == plan_three
        assert plan_one.topological_order == plan_two.topological_order
        assert plan_one.batches == plan_two.batches

    def test_repeated_scheduling_with_shuffled_input_identical(self) -> None:
        tasks = {
            TASK_A: _task(TASK_A),
            TASK_B: _task(TASK_B, (TASK_A,)),
            TASK_C: _task(TASK_C),
            TASK_D: _task(TASK_D, (TASK_B, TASK_C)),
        }
        states = _all_pending(TASK_A, TASK_B, TASK_C, TASK_D)

        plan_one = schedule_execution(tasks, states)

        # Shuffle the mapping order.
        shuffled_tasks = dict(reversed(list(tasks.items())))
        plan_two = schedule_execution(shuffled_tasks, states)

        assert plan_one == plan_two


# ------------------------------------------------------------------
# PM ownership and lifecycle preservation
# ------------------------------------------------------------------


class TestPMOwnership:
    """The scheduler never transitions lifecycle state."""

    def test_scheduler_exposes_no_lifecycle_transition_api(self) -> None:
        for name in (
            "transition",
            "set_state",
            "apply_decision",
            "complete_task",
            "update_register",
            "interpret",
            "decide",
            "reconcile",
            "dispatch",
        ):
            assert not hasattr(ExecutionPlan, name), name
            assert not hasattr(ScheduledBatch, name), name
            assert not hasattr(DependencyGraph, name), name

    def test_schedule_execution_is_pure(self) -> None:
        tasks = {
            TASK_A: _task(TASK_A),
            TASK_B: _task(TASK_B, (TASK_A,)),
        }
        states = _all_pending(TASK_A, TASK_B)
        tasks_before = {k: v for k, v in tasks.items()}
        states_before = dict(states)

        schedule_execution(tasks, states)

        assert tasks == tasks_before
        assert states == states_before


# ------------------------------------------------------------------
# ScheduledBatch and ExecutionPlan validation
# ------------------------------------------------------------------


class TestPlanValidation:
    """Malformed plans are refused at construction."""

    def test_empty_batch_rejected(self) -> None:
        with pytest.raises(ArtifactValidationError):
            ScheduledBatch(batch_number=1, task_ids=())

    def test_unsorted_batch_rejected(self) -> None:
        with pytest.raises(ArtifactValidationError):
            ScheduledBatch(batch_number=1, task_ids=(TASK_B, TASK_A))

    def test_repeated_task_in_batch_rejected(self) -> None:
        with pytest.raises(ArtifactValidationError):
            ScheduledBatch(batch_number=1, task_ids=(TASK_A, TASK_A))

    def test_zero_batch_number_rejected(self) -> None:
        with pytest.raises(ArtifactValidationError):
            ScheduledBatch(batch_number=0, task_ids=(TASK_A,))

    def test_non_consecutive_batch_numbers_rejected(self) -> None:
        with pytest.raises(ArtifactValidationError):
            ExecutionPlan(
                batches=(
                    ScheduledBatch(batch_number=1, task_ids=(TASK_A,)),
                    ScheduledBatch(batch_number=3, task_ids=(TASK_B,)),
                )
            )

    def test_task_in_multiple_batches_rejected(self) -> None:
        with pytest.raises(ArtifactValidationError):
            ExecutionPlan(
                batches=(
                    ScheduledBatch(batch_number=1, task_ids=(TASK_A,)),
                    ScheduledBatch(batch_number=2, task_ids=(TASK_A,)),
                )
            )


# ------------------------------------------------------------------
# Integration: schedule_batches creates PMBatch records
# ------------------------------------------------------------------


class TestScheduleBatchesIntegration:
    """PMRuntime.schedule_batches turns a plan into PMBatch records."""

    @pytest.fixture
    def root(self, tmp_path: Path) -> Path:
        (tmp_path / "project").mkdir()
        (tmp_path / "project" / "PROJECT_STATE.md").write_text(
            f"# AI Router — Project State\n\n"
            f"Protocol Version: {PROTOCOL_VERSION}\n"
            f"Protocol Status: IMPLEMENTATION_READY\n\n"
            f"Project: {PROJECT_ID}\n"
            f"Current Project State: IMPLEMENTING\n\n"
            f"Migration Branch:\nprotocol-v1.1-migration\n",
            encoding="utf-8",
        )
        (tmp_path / "project" / "TASKS.md").write_text(
            f"# AI Router — V1.1 PM Task Register\n\n"
            f"Protocol Version: {PROTOCOL_VERSION}\n\n"
            f"## TASK-001 — First\n\nState: PENDING\n\n"
            f"---\n\n"
            f"## TASK-002 — Second\n\nState: PENDING\n",
            encoding="utf-8",
        )
        (tmp_path / "MASTER_PLAN.md").write_text(
            f"# AI Router / JEV — Master Plan\n\n"
            f"- **Project version:** {PROJECT_VERSION}\n",
            encoding="utf-8",
        )
        return tmp_path

    def _propose(
        self, runtime: PMRuntime, task_id: str, request: str
    ) -> None:
        proposal = runtime.propose(request)
        # Rewrite the task_id so the proposal maps to the scheduled task.
        from dataclasses import replace

        task = replace(proposal.task, task_id=task_id)
        updated = replace(proposal, task=task)
        runtime.store.save(updated)

    def test_schedule_batches_creates_pm_batches(self, root: Path) -> None:
        runtime = PMRuntime(root=root)
        self._propose(runtime, "TASK-001", "Implement task one")
        self._propose(runtime, "TASK-002", "Implement task two")

        tasks = {
            "TASK-001": _task("TASK-001"),
            "TASK-002": _task("TASK-002", ("TASK-001",)),
        }
        plan = schedule_execution(
            tasks, _all_pending("TASK-001", "TASK-002")
        )
        batches = runtime.schedule_batches(plan)

        assert len(batches) == 2
        assert all(isinstance(b, PMBatch) for b in batches)
        assert batches[0].proposal_ids != batches[1].proposal_ids
        # Each batch has exactly one member (linear chain).
        assert all(len(b.proposals) == 1 for b in batches)

    def test_schedule_batches_fails_closed_on_missing_proposal(
        self, root: Path
    ) -> None:
        runtime = PMRuntime(root=root)
        self._propose(runtime, "TASK-001", "Implement task one")

        tasks = {
            "TASK-001": _task("TASK-001"),
            "TASK-002": _task("TASK-002", ("TASK-001",)),
        }
        plan = schedule_execution(
            tasks, _all_pending("TASK-001", "TASK-002")
        )

        with pytest.raises(PMRuntimeError, match="TASK-002"):
            runtime.schedule_batches(plan)

    def test_schedule_batches_creates_no_approval(self, root: Path) -> None:
        runtime = PMRuntime(root=root)
        self._propose(runtime, "TASK-001", "Implement task one")
        self._propose(runtime, "TASK-002", "Implement task two")

        tasks = {
            "TASK-001": _task("TASK-001"),
            "TASK-002": _task("TASK-002", ("TASK-001",)),
        }
        plan = schedule_execution(
            tasks, _all_pending("TASK-001", "TASK-002")
        )
        batches = runtime.schedule_batches(plan)

        assert all(not b.is_approved for b in batches)

    def test_schedule_batches_rewrites_no_register(self, root: Path) -> None:
        runtime = PMRuntime(root=root)
        self._propose(runtime, "TASK-001", "Implement task one")
        self._propose(runtime, "TASK-002", "Implement task two")

        before = ProjectContext.load(root)

        tasks = {
            "TASK-001": _task("TASK-001"),
            "TASK-002": _task("TASK-002", ("TASK-001",)),
        }
        plan = schedule_execution(
            tasks, _all_pending("TASK-001", "TASK-002")
        )
        runtime.schedule_batches(plan)

        after = ProjectContext.load(root)
        assert after == before
