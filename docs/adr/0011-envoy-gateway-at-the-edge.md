# 0011: Envoy Gateway at the edge

**Status:** accepted

## Context

Clients reached the gateway through a NodePort: kube-proxy picks a pod per TCP connection, so a client with a
kept-alive connection used one gateway pod and left the other idle (TIK-76). There was no TLS, and HTTP/2 needs TLS
for browsers (ALPN). ingress-nginx, the usual answer, was retired in March 2026.

## Decision

- **Envoy Gateway v1.10.0** (Kubernetes 1.34 to 1.37, Gateway API v1.6.1) runs Envoy at the edge, configured with the
  Gateway API: a `Gateway` with one HTTPS listener and an `HTTPRoute` to the gateway's Service. Envoy ends TLS, speaks
  HTTP/1.1 or HTTP/2 with clients, and picks a gateway pod for every request (least request), over HTTP/1.1.
- **Installed from the release's `install.yaml`**, checked against its sha256 (`infra/k8s/edge/install-envoy-gateway.sh`):
  4 MB of CRDs and the controller, too much to review or to keep in the repository. Helm would add nothing we use.
- **Two Envoy pods**, as every HTTP service has (ADR 0009). Their Service takes the NodePort 30080 that kind maps to
  `localhost:8000`, set with a patch, as the API has no field for it. The gateway's Service is a ClusterIP.
- **A self-signed certificate** for `localhost`, made by `deploy.sh` once, kept only in the Secret `edge-tls`.
- **HTTP/2 only at the edge.** Inside, kube-proxy balances connections: one HTTP/2 connection from the gateway to
  catalog would put a gateway pod's whole load on one catalog pod, the problem TIK-76 just fixed. uvicorn speaks
  HTTP/1.1 only, and the gateway's own server too.

| Option | Why not |
|---|---|
| ingress-nginx | Retired |
| Istio's gateway | A service mesh for the sake of one entry point |
| The Helm chart of Envoy Gateway | Same result; more knobs we do not need |
| HTTP/2 from Envoy to the gateway (h2c) | Few connections inside the cluster either way; the gateway's connection limits and header timeout are written for HTTP/1 |

## Consequences

- One kept-alive client now loads both gateway pods equally (50/50 against 100/0 through the NodePort).
- Envoy costs about 0.5 ms at the median and 1 to 7 % of throughput at one and eight clients, and about 0.1 core per
  Envoy pod at 540 requests a second; 45 MiB each, 66 MiB for the controller.
- The hop in front of Envoy is still L4: one HTTP/2 connection lands on one Envoy pod, which then carries all of it.
  In a real cloud the load balancer in front behaves the same; Envoy's connection age limits are the usual answer.
- Clients use `https://localhost:8000` and need the certificate (`edge.crt`), or they refuse it.
- `infra/check-k8s.sh` installs Envoy Gateway in its throwaway cluster, so the dry run knows the Gateway API kinds.
