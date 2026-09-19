# OPA sidecar for the policy engine

When `OPA_URL` is set, the server's policy engine port (`auth/policy.py`) is
backed by Open Policy Agent instead of the in-process LocalPolicyEngine.

Run a sidecar with the sample policy:

```bash
docker run -d --name opa -p 8181:8181 \
  -v $(pwd)/deployments/opa:/policies:ro \
  openpolicyagent/opa:latest-static \
  run --server --addr :8181 /policies/hub_authz.rego
```

Then: `OPA_URL=http://localhost:8181` (+ optionally `OPA_POLICY_PACKAGE`,
`OPA_FAIL_CLOSED=false` to degrade to local semantics when OPA is down).

The document contract is documented in `auth/opa_policy.py`: `allow` (bool)
for point decisions, `filter` (AccessFilter-shaped object) for list narrowing.
