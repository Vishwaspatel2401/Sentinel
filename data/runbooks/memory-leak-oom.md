# Memory Leak and OOMKilled

## Symptoms
- Pod restarting repeatedly (CrashLoopBackOff)
- OOMKilled exit code (137) in pod logs
- Heap memory growing steadily over hours without dropping
- GC pause times increasing (>500ms is a warning, >2s is critical)
- RSS memory usage climbing past container memory limit
- "java.lang.OutOfMemoryError: Java heap space" or equivalent in logs

## Root Cause
Memory leaks occur when objects are allocated but never released.
Common causes:
  - Unbounded in-memory cache (no TTL, no max size)
  - Connection objects not closed after use (DB, HTTP, Redis)
  - Event listeners registered but never deregistered
  - Static collections that grow indefinitely
  - Large response bodies buffered entirely in memory

OOMKilled means the container exceeded its memory limit and the kernel killed it.
The pod restarts, memory climbs again, and the cycle repeats.

## Fix
Option A (immediate): Increase memory limit to buy time
    kubectl set resources deployment/<service> --limits=memory=2Gi
    This stops the crash loop but does NOT fix the leak.

Option B (correct): Identify and fix the leak
    1. Take a heap dump before memory climbs too high:
       kubectl exec -it <pod> -- jmap -dump:format=b,file=/tmp/heap.hprof 1
    2. Copy the dump locally:
       kubectl cp <pod>:/tmp/heap.hprof ./heap.hprof
    3. Open in Eclipse MAT or VisualVM — look for objects with unexpectedly high retained heap
    4. Fix the root cause (close connections, add cache eviction, fix listener leaks)
    5. Redeploy and monitor memory growth rate over 1 hour

Option C (fastest rollback): If leak started after a deploy
    kubectl rollout undo deployment/<service>

## Prevention
- Set memory limits on all containers — never run without limits
- Add memory usage to dashboards with alert at 80% of limit
- Load test new features that introduce caching or connection pooling
- Use weak references for large in-memory caches
- Always close connections in finally blocks or use context managers
