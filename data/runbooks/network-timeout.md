# Network Timeouts and DNS Failures

## Symptoms
- "connection timed out" or "i/o timeout" errors in logs
- "no such host" or "DNS resolution failed" errors
- Requests to downstream services failing intermittently
- Latency spikes to external APIs or internal services
- TLS handshake timeout errors
- "context deadline exceeded" errors (Go) or "ReadTimeout" (Python)
- Partial failures — some requests succeed, others fail (flapping)

## Root Cause
Common causes:
  - DNS misconfiguration after a deploy (wrong service name, wrong namespace)
  - Network policy change blocking traffic between services
  - Downstream service overloaded — accepting connections but not responding
  - TLS certificate expired on the downstream service
  - Firewall rule change blocking port
  - Kubernetes service selector mismatch — service not routing to any pods
  - Connection pool not reusing connections — DNS TTL causing stale IPs
  - Cloud provider networking issue (VPC, security group, NAT gateway)

Intermittent failures (flapping) usually indicate a partially broken downstream
rather than a total outage — some pods are healthy, some are not.

## Fix
Option A (DNS issue):
    1. Test DNS resolution from inside the cluster:
       kubectl exec -it <pod> -- nslookup <service-name>
    2. Check the service exists: kubectl get svc -n <namespace>
    3. Check the selector matches pods: kubectl describe svc <service-name>
    4. Fix the service name in config or environment variables

Option B (TLS certificate expired):
    1. Check cert expiry: echo | openssl s_client -connect <host>:443 2>/dev/null | openssl x509 -noout -dates
    2. Renew certificate (cert-manager: kubectl delete certificate <name> to force renewal)

Option C (downstream overloaded):
    1. Check downstream service health: kubectl top pods -n <namespace>
    2. Scale downstream service: kubectl scale deployment/<downstream> --replicas=<n>
    3. Add circuit breaker / retry with backoff on the calling service

Option D (network policy):
    1. List policies: kubectl get networkpolicy -n <namespace>
    2. Check if a new policy is blocking the connection
    3. Roll back the policy change

Option E (rollback if post-deploy):
    kubectl rollout undo deployment/<service>

## Prevention
- Set explicit timeouts on ALL outbound HTTP calls — never rely on OS defaults
- Implement circuit breakers for calls to external services
- Monitor downstream service latency separately from your own
- Set up TLS certificate expiry alerts at 30 days and 7 days before expiry
- Test network policies in staging before applying to production
