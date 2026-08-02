# Branch cleanup trigger

This temporary marker triggers the main-only cleanup workflow after the complete HostPanel stack was integrated in PR #110.

The workflow archives all refs into a private 90-day Git bundle artifact before deleting stale `agent/`, `backup/`, and `validation/` branches that are not referenced by an open pull request.
