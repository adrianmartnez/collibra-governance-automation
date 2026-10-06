# Packaging and install

## Entry point

```toml
[project.entry-points."governance.providers"]
catalog = "my_package.provider:register"
```

## Host dependency before v2.0 PyPI release

Published `collibra-governance-automation==1.4.0` does **not** include the Provider SDK.
Until v2.0 is released, install a core build that contains Provider SDK API `1` from an
exact Git commit SHA (companion CI pins an immutable SHA). Do not declare a false
`>=1.4` requirement.

After v2.0 publication (PR6), prefer a published range such as
`collibra-governance-automation>=2.0,<3` if that is the chosen SemVer policy.

## Local proof pattern

```text
build core wheel
build provider wheel
clean venv: core only → provider absent
clean venv: core + provider → discovery + conformance + impact
uninstall provider → absent
```

No `PYTHONPATH` shortcuts to the source tree.
