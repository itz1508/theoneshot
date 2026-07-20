from typing import Protocol

from audisor_backend.schemas.fix.models import EvaluatedPlan, FixScopedManifest, SandboxResult


class SandboxProvider(Protocol):
    def simulate(self, manifest: FixScopedManifest, evaluated_plan: EvaluatedPlan) -> SandboxResult: ...

