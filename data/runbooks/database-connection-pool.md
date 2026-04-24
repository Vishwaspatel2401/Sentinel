# Database Connection Pool Exhaustion

## Symptoms
- High rate of "connection refused" errors
- DB timeout errors on write-heavy endpoints
- Error rate spike shortly after a deployment

## Root Cause
When pool_size is lower than the number of concurrent requests,
connections queue up and eventually time out.
Under 18 req/s load, a pool of 5 connections is insufficient.

## Fix
Option A (fastest): Roll back the deployment
    kubectl rollout undo deployment/<service-name>

Option B (hotfix): Increase pool size in config
    pool_size: 25
    Then redeploy.

## Prevention
Always load test after changing connection pool configuration.
Pool size should be at least 2x your p99 concurrent connection count.
