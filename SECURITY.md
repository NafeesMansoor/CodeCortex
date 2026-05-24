# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| 1.x     | ✅ Active support |
| < 1.0   | ❌ No longer supported |

## Reporting a Vulnerability

**Do not open a public GitHub issue for security vulnerabilities.**

Report security issues privately by emailing:

**nafees.mansoor@ulab.edu.bd**

Include in your report:

- A description of the vulnerability and its potential impact
- Steps to reproduce or a proof-of-concept
- Any suggested mitigations

### Response Timeline

| Step | Timeline |
|------|----------|
| Acknowledgement | Within 48 hours |
| Initial assessment | Within 5 business days |
| Fix or mitigation | Within 30 days (critical), 90 days (others) |
| Public disclosure | After fix is released and users have time to upgrade |

## Security Design Notes

- CodeCortex runs entirely locally — no data is transmitted externally unless you configure an `openai` embedding provider.
- The visualization server binds to `localhost` only and is not exposed to the network.
- No credentials are stored by the system. Use `.env` (gitignored) for secrets — never commit them.
- The `safety` and `bandit` tools run automatically on every push via GitHub Actions.

## Known Limitations

The `bandit` static analysis scanner may surface low-severity findings in the subprocess-launching code used by the LSP provider. These are intentional and reviewed — the LSP client spawns language server processes in a controlled manner.
