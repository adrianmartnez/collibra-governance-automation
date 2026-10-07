# Packaging and install

## Entry point

```toml
[project.entry-points."governance.providers"]
catalog = "my_package.provider:register"
```

## Host dependency (Provider SDK API `1`)

Install a core distribution that provides Provider SDK API `1`
(`governance.providers`, `governance.domain`, `governance.conformance`).

### Guaranteed path after the `v2.0.0` tag (GitHub-only)

```text
pip install "collibra-governance-automation @ git+https://github.com/adrianmartnez/collibra-governance-automation.git@v2.0.0"
```

This uses the annotated Git tag. Do not assume wheel/sdist assets are attached to the
GitHub Release unless that publication step is performed and verified separately.

### PyPI

Document `collibra-governance-automation>=2.0,<3` **only if** that version is actually
published to PyPI. Do not imply PyPI availability from a GitHub tag alone.

### Historical note

Published `collibra-governance-automation==1.4.0` (where present on an index) does **not**
include the Provider SDK. Pre-v2 development installs pinned an exact core Git commit SHA.

## Local proof pattern

```text
build core wheel
build provider wheel
clean venv: core only → provider absent
clean venv: core + provider → discovery + conformance + impact
uninstall provider → absent
```

No `PYTHONPATH` shortcuts to the source tree.
