# High CPU Usage and Throttling

## Symptoms
- Response latency increasing (p99 > 2x baseline)
- CPU usage sustained above 80% on one or more pods
- "CPU throttling" alerts firing in Prometheus
- Requests timing out despite the service being up
- Thread pool exhaustion — "no threads available" in logs
- Runaway process consuming entire CPU core

## Root Cause
Common causes of CPU spikes:
  - Inefficient algorithm suddenly hitting large input (N² loop on growing dataset)
  - Regex with catastrophic backtracking on user-supplied input
  - Tight polling loop with no sleep (busy-wait)
  - Unexpected traffic spike — load higher than capacity
  - JSON serialisation of very large objects on every request
  - Missing database index — full table scan on every query
  - Cryptographic operation (TLS handshake, bcrypt) blocking the event loop

CPU throttling happens when a container hits its CPU limit — requests slow down
rather than the process being killed (unlike OOMKilled).

## Fix
Option A (immediate — buy time): Scale out horizontally
    kubectl scale deployment/<service> --replicas=<current+2>
    This distributes load across more pods while you investigate.

Option B (find the hot function):
    1. Get CPU profile from a running pod:
       kubectl exec -it <pod> -- py-spy top --pid 1
       (or equivalent profiler for your language)
    2. Identify the function consuming most CPU time
    3. Check git log for recent changes to that function
    4. Optimise or revert

Option C (missing index — DB causing CPU load):
    1. Check slow query log: SELECT * FROM pg_stat_statements ORDER BY total_time DESC LIMIT 10;
    2. Add index for the slow query
    3. Monitor CPU after index is created

Option D (rollback if post-deploy):
    kubectl rollout undo deployment/<service>

## Prevention
- Set CPU requests AND limits on all containers
- Add CPU usage alerts at 70% sustained for >5 minutes
- Profile before deploying algorithms that process unbounded input
- Always add database indexes for columns used in WHERE clauses on large tables
- Use connection pooling to avoid per-request TLS handshakes
