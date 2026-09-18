# Dependency review for M1–M5 integration

Review date: 18 September 2026. Scope: the published manifests and visible branch
diffs, not M2's unpublished laptop environment. The user authorizes necessary
`pyproject.toml` repairs; changes still need a reproduced failure and a compatible
solution rather than speculative upgrades.

## Findings and action

| Finding | Evidence / action |
|---|---|
| No root manifest divergence in inspected M1/M3/M4/M5 builds | Compared their manifests with fetched `origin/main` (`41ee8c7`). The root, API and worker manifests agree. M2's files remain unseen. |
| Current root requirements can resolve in a Windows-targeted dry run | All 28 runtime/dev declarations resolved to 96 packages with exit 0. This did not reproduce a package-version conflict. No package installed. |
| Full frozen runtime remains unverified | No committed `uv.lock`; no available approved Python 3.13.15 interpreter or discovered usable `uv` executable in this session. The bundled resolver host was Python 3.12.14. |
| The pip check does not enforce the uv freeze | Root cutoff remains `2026-09-12T00:00:00Z`. Pip did not read/enforce it; the proposed solution is diagnostic, not an approved new lock or upgrade. |
| AX tracing dependencies are not selected/declared yet | M1 specifies the minimal implementation-compatible set; M2 resolves it under shared constraints. M4 AX experiment SDK and GPU environments stay isolated. |
| Ragas remains an architectural/dependency exclusion | Do not add it to fix a missing evaluator; use the approved repository-owned workflow and external Prometheus judge. |
| LangSmith package presence is not a second tracing destination | `langchain-core==1.6.2` requires `langsmith>=0.3.45,<1.0.0`. Keep required transitives; disable LangSmith tracing/export and verify AX-only emission. |

**No `pyproject.toml` change was justified by this review, so its pins and cutoff
were preserved.** This does not certify every environment conflict resolved.
M2 must supply its exact manifest/lock and failing resolver output; the next
dependency slice repairs confirmed conflicts and produces the shared lock.

## Verification performed

1. Fetched origin and compared the root/API/worker manifests across the visible
   implementation branches and main. No manifest changes were found there.
2. Queried official PyPI JSON for all 28 declared versions/minimum versions,
   including Python requirements, dependencies and upload metadata. Examples:
   [Google GenAI 2.21.0](https://pypi.org/pypi/google-genai/2.21.0/json),
   [LangChain Core 1.6.2](https://pypi.org/pypi/langchain-core/1.6.2/json),
   [Llama Cloud 2.14.1](https://pypi.org/pypi/llama-cloud/2.14.1/json).
   Checking minimum-version metadata alone is not a transitive solve.
3. Used an already available bundled Python 3.12.14 and pip 26.2.1 to extract
   `[project].dependencies` and `[dependency-groups].dev` into a temporary
   requirements file. Ran the following diagnostic command, with no installation:

   ```powershell
   python -m pip --disable-pip-version-check install --dry-run --ignore-installed --only-binary=:all: --python-version 3.13.15 --index-url https://pypi.org/simple --report "$env:TEMP/netra-root-resolution-20260918.json" -r "$env:TEMP/netra-root-dependency-review.txt"
   ```

   Here `python` denotes that existing bundled interpreter, not a newly installed
   runtime. Result: exit 0, 96 proposed packages, with declared provider pins
   retained. The temporary JSON report is local diagnostic output, not a committed
   lockfile. Metadata was downloaded; no packages, interpreters or providers were
   installed/executed by this check.

Limitations: this selected Python 3.13.15-compatible wheels on Windows using a
3.12 host. It does not prove all target environment-marker branches, Linux wheels,
actual imports, application correctness, security review, the uv cutoff, or new
AX/Modal packages. No `uv lock`, installed-environment `pip check`, approved-runtime
pytest, database integration or Windows/NVDA application run was performed here.

## Required next dependency slice — M2 with affected owners

- Collect each member's exact failure, OS/interpreter/SDK, package index, selected
  extras, manifest/lock diff and commit. Do not include credentials or full env dumps.
- Classify solver conflicts separately from missing runtimes/packages, unsupported
  wheels, wrong Python/.NET version, import failures and unmerged API changes.
- Reproduce against the agreed merged manifest. Make the smallest justified change;
  preserve selected models/framework boundaries. Review new OTel/OpenInference pins
  with M1 and isolated AX/GPU pins with M4. Do not install broad vendor examples.
- M2 owns one shared API/worker lock. Generate it with an authorized resolver under
  the recorded cutoff; verify direct and transitive versions. If a required release
  falls after the cutoff, document that exact conflict and review the narrow
  adjustment rather than deleting the cutoff or claiming a pip solve proves it.
- Test the approved Python/deployment platform and real imports/affected adapters
  when setup is authorized. Keep environment setup/download authorization explicit;
  dependency-file changes alone do not grant credentials, deployment or spending.
- All consumers adopt the resulting reviewed lock. Do not resolve competing locks
  or fix an import mismatch by installing another owner's unpublished code globally.

Use the [dependency task prompt](integration-playbook.md#task-prompt-dependency-and-shared-foundation-review)
and [handoff fields](handoff-template.md) to report the result and remaining gates.
