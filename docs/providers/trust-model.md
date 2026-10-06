# Trust / security model

Installed providers are **trusted Python dependencies**.

They are **not**:

- sandboxed extensions
- untrusted plugins
- security-isolated
- vendor-certified
- production-certified by this project

The core discovers providers via `importlib.metadata` entry points and executes
provider Python during registration/factory/capability operations. Treat installation
like any other dependency: only install code you trust.

Conformance validates cooperative observable behavior. It does not detect arbitrary
malicious exfiltration or prove absence of side effects during `register()`.

GitHub Action never auto-installs providers. Workflow owners who install providers into
a caller-prepared runtime accept that responsibility explicitly.
