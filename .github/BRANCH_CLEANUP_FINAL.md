# Final branch cleanup trigger

This temporary marker triggers the trusted `pull_request_target` cleanup after the full HostPanel stack was merged and validated.

The cleanup archives every ref into a private Git bundle artifact before deleting stale working and backup branches not referenced by an open pull request.
