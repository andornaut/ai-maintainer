# Container tooling

Run these commands from the repository root. The launchers need Bash, Git,
Docker and GNU core utilities on the host. Make targets also need GNU Make.

| Command | Checks |
| --- | --- |
| `make check` | Shell syntax, Ruff lint and formatting, pytest, byte-compilation, Markdown and ShellCheck |
| `make lint` | Shell syntax, Ruff lint and formatting, Markdown and ShellCheck |
| `make test` | Full pytest suite |
| `make build` | Byte-compile the executable and test module without producing a release artifact |

CI runs the same native checks in stock containers, with separate Python,
Markdown and ShellCheck phases.
Python's image version comes from `.python-version`. The other image versions
are pinned in `Makefile` and the workflows. Python development dependencies are
pinned in `requirements-dev.txt`, and Markdown tooling uses this directory's npm
manifest and lockfile.

The launcher copies tracked and non-ignored source files into temporary storage,
excluding Git metadata and credential-shaped paths. Containers receive this
copy read-only and run as an unprivileged user, with a read-only root filesystem,
writable temporary directories and resource limits. Checks that need to write
source use a disposable copy inside the container. Dependency-install phases
have network access. ShellCheck and standalone `make build` run without it.

## Focused tests and other commands

With no arguments, `scripts/test-in-container` runs `make check`. With arguments,
it builds the compatibility image from `Dockerfile` in this directory and runs
the supplied command. That image includes Python development tools, Node,
Markdown tooling and ShellCheck. The build uses the filtered source copy and
the root `.dockerignore`.

```bash
./scripts/test-in-container python -m pytest test_ai_maintainer.py::TestGitClient -v
./scripts/test-in-container ruff check .
```

Arguments are passed directly to the command. Shell syntax needs an explicit
shell, for example `./scripts/test-in-container sh -c 'ruff check . && ruff format --check .'`.
These commands have network access. Changes made inside the container are
discarded when it exits.

## Markdown dependency overrides

WORKAROUND: Scoped npm overrides keep Markdown lint dependencies patched for
[re2js](https://github.com/advisories/GHSA-gg6h-894w-4m9c) and
[KaTeX](https://github.com/advisories/GHSA-238p-pmpm-9mq7).
Remove the overrides when the upstream CLI dependency graph resolves patched
versions without them.
