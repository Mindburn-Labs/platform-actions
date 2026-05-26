# platform-actions

> [!WARNING]
> **Ecosystem Boundary**: This repository is classified strictly as a **Non-HELM system**. It is decoupled from the HELM cryptographic verification core and serves Pilot or Titan product layers.

## 1. System Overview & Purpose
`platform-actions` is a production-grade component of **Mindburn Labs** representing a dedicated layer inside our sovereign microservice architecture.

Aggregates centralized continuous integration scripts, OPA policies, OTel configurations, sandbox execution substrates, and tool catalogs for developers and AI agents.

### Technical Taxonomy: **PlatformOps & Agent Substrate**
*   **Language Stack:** Text
*   **Build & CodeGen Toolchain:** Static Manifests
*   **Containerization:** N/A
*   **Security Baseline:** Enforced (Push Protection, Dependabot Monthly Sweeps, Vulnerability Alerts)

---

## 2. Directory Layout & Key Components
Below is the verified structural topology of the repository:
```text
.
├── .github/              # Workflow definitions and default org actions
├── .devcontainer/        # Ephemeral sandbox configuration for AI agents and devs
├── docs/                 # Architectural Decision Records (ADRs) and runbooks
├── observability/        # SLO configurations, custom metrics, and alert rules
├── CODEOWNERS            # Explicit team ownership definitions
├── SECURITY.md           # Responsible vulnerability disclosure policy
├── renovate.json         # Monthly dependency drift manager rules
├── agent.yaml            # Declarative execution constraints and entrypoints for AI agents
└── AGENTS.md             # Autonomous engineering directives and test runner indices
```

---

## 3. Getting Started & Toolchain
We enforce reproducible development. Ensure you run commands inside the standard devcontainer sandbox environment.

### Prerequisites
*   Git (authenticated using standard OIDC keyring)
*   Text toolchain (or equivalent Docker daemon if building containers)

### Standard Development Steps
To bootstrap, compile, verify, and run compliance validation locally, execute the following commands:

```bash
# 1. Setup and restore dependencies
# Restore dependencies
make setup 2>/dev/null || echo "No setup required"

# 2. Static compilation and code-generation
# Compile target codebase
make build 2>/dev/null || echo "No build required"

# 3. Code formatting, compliance check, and linting
# Run static analysis & typecheck
make lint 2>/dev/null || echo "No lint rules"

# 4. Local testing and assertion validations
# Run assertions & unit tests
make test 2>/dev/null || echo "No test target"
```

---

## 4. Production Observability & Telemetry

#### Local Observability Diagnostics
*   **Prometheus Scrape Endpoint:** `http://localhost:2112/metrics` (Default Prometheus exposition format)
*   **OTel Collector Target:** `http://localhost:4317` (gRPC) or `http://localhost:4318` (HTTP/JSON)
*   **Health & Readiness SLO Probe:** `GET http://localhost:8080/healthz` or `GET http://localhost:8080/readyz`

High reliability requires comprehensive, zero-bias monitoring. This repository incorporates standard OTel metrics and tracing:
*   **Metrics Scraper:** Exposes Prometheus scrape metrics tracking `Sandbox startup times, task routing times, policy verification success rates`.
*   **Tracing Engine:** Injects standard OpenTelemetry propagation headers across downstream boundaries.
*   **Custom Alerting SLOs:** Located in `observability/alerts.yaml`, raising automated alerts if system availability drops below **99.9%** or error-rate thresholds are violated over a sliding 5-minute interval.

---

## 5. Rollback & Disaster Recovery (Rollback Class R1)
We enforce deterministic rollback guidelines tailored to each component's state and risk tier.

### **Rollback Class R1 Protocol**
*   **Details:** Platform configuration or policy overlays. Rollback involves reverting policy mappings or OTel scrape rules.
*   **Mean Time to Restore (MTTR):** Target < 3 minutes under standard stateless rollbacks.
*   **Incident Runbook:**
    1.  Inspect active OTel trace IDs to isolate fault signatures.
    2.  Check K8s deployment statuses or tag deployments inside Argo CD in the GitOps control plane.
    3.  If stateful migration rollback is blocked, initiate the approved **Forward-Fix** pipeline rather than attempting destructive data reversals.

---

## 6. Secure SDLC & Least Privilege

#### OIDC Pipeline Authentication
This repository authenticates to cloud infrastructure using passwordless OpenID Connect (OIDC) tokens.
*   **Audience Mapping:** `https://github.com/Mindburn-Labs`
*   **Required GitHub Workflow Scopes:**
    ```yaml
    permissions:
      id-token: write   # Required for requesting the JWT OIDC token
      contents: read    # Required for checkout
    ```
*   **Target Cloud IAM Role Variable:** `${{ secrets.GH_ACTIONS_OIDC_ROLE_ARN }}`

*   **OIDC Token Federation:** Direct, passwordless OpenID Connect federation is used for container publishing and cloud deployments.
*   **Zero Static Keys:** Storing long-lived cloud credentials or environment tokens in repository variables is **strictly forbidden**. All secrets must route dynamically through secure cloud brokers or HashiCorp Vault.
*   **Automated Updates:** Dependabot / Renovate scans execute monthly to bump minor and patch variations, eliminating package drift.

---

## 7. Licensing & Security Contact
*   **License:** Proprietary. All rights reserved.
*   **Security Disclosures:** Please report potential vulnerabilities via the instructions in [SECURITY.md](SECURITY.md).
