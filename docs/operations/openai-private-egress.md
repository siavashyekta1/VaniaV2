# Private OpenAI egress operations

The application opt-in `OPENAI_PROXY_URL=http://10.0.0.1:13129` applies only to the official OpenAI host. It does not change model IDs, API credentials, other providers, or general HTTP traffic.

## Transport

Frankfurt initiates a persistent reverse SSH connection to the application host. HTTPS CONNECT reaches a loopback-only Squid instance in Frankfurt. OpenAI TLS remains end-to-end; neither API keys nor application payloads are logged by the proxy.

- Application host: `vania-openai-bridge.service`, Docker-only `10.0.0.1:13129` to loopback `127.0.0.1:13130`.
- Frankfurt: `vania-openai-proxy.service` (loopback `127.0.0.1:3129`) and `vania-openai-reverse-tunnel.service` (automatic reconnect).
- SSH account on application host: `vania-openai-egress`. Source-IP restricted, public-key only, remote forwarding only to `127.0.0.1:13130`; no shell/session, TTY, agent forwarding, or public listener.
- Frankfurt SSH identity: `/etc/vania-openai-proxy/reverse_id_ed25519`; keep private. The application host key is pinned in `reverse_known_hosts`.
- Squid accepts CONNECT only to `api.openai.com:443`; all other destinations/methods are denied. Existing unrelated proxies/services are not modified.
- There is no direct fallback when the configured proxy is unavailable.

## Checks and recovery

Check service status and recent journal entries on the respective hosts. Never print application environment values or API keys. Confirm listeners are private, then run authenticated SDK checks from an application container using its existing environment. A public health endpoint alone does not prove model access.

A lost SSH connection is detected with server-alive probes and restarted by systemd. Restart `vania-openai-reverse-tunnel.service` on Frankfurt if necessary. If the application host's pinned SSH key changes, verify the new fingerprint through a trusted channel before replacing the pin.

## Rollback

Remove `OPENAI_PROXY_URL` from the affected Coolify application's runtime environment and redeploy the previously recorded commit. Without the variable, SDK configuration retains its original direct route (which may still be region-restricted). Do not disable the shared tunnel until all dependent applications have been rolled back. Do not delete databases, Docker volumes, or unrelated services.

The setting is runtime-only, not a build argument. Roll out Stage first, verify actual sync/async responses and embeddings, then deploy production environments serially. Preserve environment-specific frontend API origins and image tags.

Regional/provider eligibility is not changed by transport routing; administrators must ensure their account and usage comply with provider requirements.

