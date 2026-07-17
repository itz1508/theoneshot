from __future__ import annotations

import re
from typing import Any

from aflow.schemas.validator import issues_for


_MISSING = re.compile(r"^'([^']+)' is a required property$")


def admit(request: Any) -> dict[str, Any]:
    analysis_id = request.get("analysis_id", "analysis.invalid") if isinstance(request, dict) else "analysis.invalid"
    errors = []
    for index, issue in enumerate(issues_for(request, "analysis-request.schema.json"), start=1):
        instance_path = issue.instance_path
        match = _MISSING.match(issue.message)
        if match:
            token = match.group(1).replace("~", "~0").replace("/", "~1")
            instance_path = f"{instance_path}/{token}" if instance_path else f"/{token}"
        errors.append(
            {
                "error_id": f"schema.error.{index}",
                "keyword": issue.keyword,
                "instance_path": instance_path,
                "schema_path": issue.schema_path,
                "message": issue.message,
                "blocking": True,
            }
        )
    return {"schema_version": "1.0.0", "analysis_id": analysis_id, "valid": not errors, "errors": errors}

