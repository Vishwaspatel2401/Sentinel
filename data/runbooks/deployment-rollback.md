# Deployment Regression and Rollback

## Symptoms
- Error rate spike starting within 5-15 minutes of a deployment
- New error types in logs that did not exist before the deploy
- Latency increase immediately after deployment completes
- Health check failures on newly deployed pods
- Crash loop on new pods while old pods were healthy
- Feature flag enabled and errors started immediately after

## Root Cause
Deploy regressions occur when a code or config change introduces a bug
that only manifests under production load or with production data.
Common causes:
  - Environment variable missing or wrong in production (worked in staging)
  - Database migration ran but is incompatible with the new code
  - Dependency version bumped with a breaking change
  - Config value changed (pool_size, timeout, thread count) without load testing
  - Feature flag enabled that has a bug with production data shape
  - New code path that works in unit tests but fails on real traffic patterns

The strongest signal is timing: if errors started within 15 minutes of a deploy,
the deploy is the most likely cause regardless of what changed.

## Fix
Option A (fastest — rollback immediately):
    1. Roll back the deployment:
       kubectl rollout undo deployment/<service-name>
    2. Verify rollback completed:
       kubectl rollout status deployment/<service-name>
    3. Confirm error rate is dropping:
       watch -n 5 kubectl top pods -l app=<service-name>
    4. Open an incident ticket to investigate root cause before re-deploying

Option B (if rollback is not safe — e.g. migration already ran):
    1. Do NOT roll back the code — the old code may be incompatible with the migrated schema
    2. Write a hotfix that fixes the bug in the new version
    3. Deploy the hotfix as a new version
    4. Monitor for 15 minutes before closing the incident

Option C (feature flag regression):
    1. Disable the feature flag immediately
    2. Verify error rate drops
    3. Investigate the bug before re-enabling

## How to identify which deploy caused it
    kubectl rollout history deployment/<service-name>
    # Shows all recent deployments with revision numbers
    kubectl rollout undo deployment/<service-name> --to-revision=<n>
    # Roll back to a specific revision if the latest undo isn't enough

## Prevention
- Always correlate error rate graphs with deploy timestamps
- Deploy during low-traffic windows when possible
- Use canary deployments — route 5% of traffic to new version first
- Load test config changes (pool_size, thread counts, timeouts) before deploying
- Run database migrations separately from code deploys
- Keep feature flags off by default — enable explicitly after verifying health
