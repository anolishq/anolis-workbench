# PyPI Trusted Publishing Handoff

Audience: repository maintainer with admin access to GitHub repo settings and
PyPI project settings.

Purpose: complete the manual configuration required for Python package release
automation (`.github/workflows/release.yml`).

---

## Manual setup checklist

### 1) PyPI project setup

1. Create or claim the `anolis-workbench` project on PyPI.
2. Confirm the project name matches `pyproject.toml` exactly:
   `name = "anolis-workbench"`.
3. Confirm at least one owner/maintainer account is assigned.

### 2) Configure PyPI trusted publisher (OIDC)

In PyPI project settings, add a trusted publisher with:

1. Owner: `anolishq`
2. Repository: `anolis-workbench`
3. Workflow name: `release.yml`
4. Environment name: `pypi`

Notes:

1. No API token is required when trusted publishing is configured correctly.
2. Publish will fail with an OIDC error until this step is complete.

### 3) Configure GitHub environment

In GitHub repo settings, create environment `pypi`:

1. Name: `pypi`
2. Optional protection rules:
   - required reviewers
   - branch restrictions
3. No PyPI token secret should be added for trusted publishing.

### 4) Create/update `uv.lock`

`uv.lock` is expected by CI/release cache settings.

Run locally in repo root:

```bash
uv lock
```

Then commit and push `uv.lock`.

Fallback if `uv` is not yet installed locally:

```bash
python -m pip install uv
uv lock
```

### 5) First release dry run

Before publishing a production version:

1. Bump `pyproject.toml` version and push to `main`.
2. Open Actions → `Release` workflow.
3. Run `workflow_dispatch` with matching `version`.
4. Confirm sequence:
   - validate passes
   - CI gate passes
   - build artifacts created
   - PyPI publish succeeds
   - tag + GitHub release created

---

## Common failure modes

1. `Version mismatch`:
   - Input version does not match `pyproject.toml`.
2. `Tag already exists`:
   - Version was already released/tagged.
3. PyPI OIDC rejection:
   - trusted publisher repo/workflow/environment mismatch.
4. Missing `uv.lock`:
   - cache config references lock file not committed yet.

---

## Operational notes

1. Release workflow is intentionally `workflow_dispatch` only.
2. Public GitHub release is created only after successful PyPI publish.
3. Clean-install smoke checks should be run post-release:

```bash
python -m venv /tmp/smoke-venv
/tmp/smoke-venv/bin/pip install anolis-workbench
/tmp/smoke-venv/bin/anolis-workbench --help
/tmp/smoke-venv/bin/anolis-package --help
/tmp/smoke-venv/bin/anolis-validate --help
```
