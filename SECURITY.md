# Security Policy

*Italian version: [SECURITY.it.md](SECURITY.it.md).*

## Supported Versions

| Version | Supported |
|---------|-----------|
| latest  | Yes       |
| < latest | No — please upgrade |

## Reporting a Vulnerability

If you discover a security vulnerability in Spendif.ai, **please do not open a public issue.**

Instead, report it privately:

1. **GitHub Security Advisories** (preferred): go to the [Security tab](https://github.com/drake69/spendify/security/advisories) and click **"Report a vulnerability"**
2. **Email**: send details to the repository owner via the email listed on the [GitHub profile](https://github.com/drake69)

### What to include

- Description of the vulnerability
- Steps to reproduce
- Affected version(s)
- Potential impact
- Suggested fix (if any)

### Response timeline

| Step | Timeline |
|------|----------|
| Acknowledgment | Within 48 hours |
| Initial assessment | Within 7 days |
| Patch release | Within 30 days (critical: within 7 days) |
| Public disclosure | After patch is released |

## Scope

The following are **in scope**:

- Application code in `core/`, `services/`, `ui/`, `db/`, `api/`
- GitHub Actions workflows (`.github/workflows/`)
- Configuration, secret handling, and the PII-redaction layer applied before remote LLM calls

The following are **out of scope**:

- Vulnerabilities in upstream dependencies (report to the respective project; we will track via Dependabot / pip-audit)
- Social engineering attacks
- Denial of service attacks

## Security Measures

Spendif.ai employs the following security practices:

- **Static analysis** — Bandit and forbidden-pattern guards run on every PR (CI workflow `security.yml`)
- **CodeQL** — semantic analysis with the `security-and-quality` query suite (planned, tracked in backlog)
- **Input validation** — Pydantic v2 schemas at API boundaries; explicit field-level checks in the service layer
- **No dangerous functions** — `eval()`, `exec()`, `subprocess shell=True`, `pickle.load()`, `yaml.load()` without `SafeLoader` are forbidden and CI-blocked
- **Ruff linting** — enforced in CI with the bandit-style security rules (`S`) enabled
- **Dependency management** — `uv.lock` provides reproducible installs; `pip-audit` flags known CVEs
- **PII redaction** — IBAN, card number, fiscal code and account-holder name are automatically replaced with placeholders before any remote LLM call. The service layer rejects unsanitised input. See `core/pii_redactor.py` and the privacy section of the landing page for the full list of handled identifiers
- **Local-first AI** — the default LLM backend (`local_llama_cpp`) keeps all data on the user's machine; remote backends are opt-in
- **Prompt integrity** — prompt files have SHA-256 hashes verified at startup so unauthorised modifications are detected (planned, tracked in backlog)

## Acknowledgments

We appreciate responsible disclosure. Security researchers who report valid vulnerabilities will be credited in the release notes (unless they prefer anonymity).
