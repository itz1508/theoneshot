from audisor_backend.schemas.fix.models import EvaluatedPlan, FixScopedManifest, SandboxResult


class LocalSandbox:
    """Provider seam only; candidate execution is intentionally injected."""
    def __init__(self, runner):
        self.runner = runner

    def simulate(self, manifest: FixScopedManifest, evaluated_plan: EvaluatedPlan) -> SandboxResult:
        return self.runner(manifest, evaluated_plan)

