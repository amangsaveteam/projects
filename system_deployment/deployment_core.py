"""Small, dependency-free transaction runner for system deployment.

This is adapted from the robot deployment flow so this project can stage,
activate and roll back system files without depending on a module runtime.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional, Sequence, Tuple


class DeploymentPhase(str, Enum):
    PREFLIGHT = "preflight"
    STAGE = "stage"
    ACTIVATE = "activate"
    START = "start"


class DeploymentError(RuntimeError):
    """Raised when an action explicitly reports failure."""


Action = Callable[[], Optional[bool]]
Rollback = Callable[[BaseException, Tuple[DeploymentPhase, ...]], None]


@dataclass(frozen=True)
class DeploymentAction:
    phase: DeploymentPhase
    run: Action


@dataclass(frozen=True)
class DeploymentResult:
    ok: bool
    completed: Tuple[DeploymentPhase, ...]
    error: Optional[BaseException] = None


class DeploymentRunner:
    """Run each action once, rolling back completed work on failure."""

    def __init__(self, actions: Sequence[DeploymentAction], rollback: Optional[Rollback] = None) -> None:
        if not actions:
            raise ValueError("DeploymentRunner requires at least one action")
        phases = tuple(action.phase for action in actions)
        if len(set(phases)) != len(phases):
            raise ValueError("Each deployment phase may appear only once")
        self._actions = tuple(actions)
        self._rollback = rollback

    def run(self) -> DeploymentResult:
        completed = []
        try:
            for action in self._actions:
                result = action.run()
                if result is False:
                    raise DeploymentError("Deployment phase '{}' reported failure".format(action.phase.value))
                completed.append(action.phase)
            return DeploymentResult(ok=True, completed=tuple(completed))
        except Exception as error:
            completed_phases = tuple(completed)
            if self._rollback is not None:
                self._rollback(error, completed_phases)
            return DeploymentResult(ok=False, completed=completed_phases, error=error)
