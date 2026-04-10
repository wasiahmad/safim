# SAFIM

Official repository: https://github.com/gonglinyuan/safim/tree/main

This is an unofficial fork focused on **execution-based evaluation** of SAFIM tasks with a small codebase: Hugging Face dataset loading, HTTP calls to a code-execution service, optional **parallel scoring** (`ProcessPoolExecutor`), and optional **post-processing** of completions before scoring.

## Install

```bash
pip install git+https://github.com/wasiahmad/safim.git
# or from a clone:
pip install -e .
```

Dependencies are listed in `requirements.txt` (`tqdm`, `datasets`, `tree-sitter`, `requests`).

## Usage

Use this package as a **library**: pass a JSONL file of completions and a path for aggregated JSON results. Tasks that include **unit tests** send code to an HTTP execution API at `http://localhost:{port}` (default **5000**). You must run a compatible execution server yourself; this repo only ships the client (`safim.exec_utils`).

### `evaluate()`

```python
from safim.evaluate import evaluate

evaluate(
    completion_type,   # dataset config name, e.g. "block", "control", "api"
    completion_path,   # JSONL: one object per line
    output_path,       # aggregated metrics + per-task results (JSON)
    post_process=False,
    language=None,     # if set, only rows where problem["lang"] matches
    port=5000,
    max_workers=1,     # 1 = sequential; >1 = process pool (see below)
)
```

**Completion JSONL** each line should include at least:

- `task_id` — must match the SAFIM test split
- `completion` — model output inserted into `eval_prompt` at `{{completion}}`

**Post-processing** (`post_process=True`):

- `completion_type` must be one of **`api`**, **`control`**, or **`block`** (same family as the dataset you load).
- Before scoring, the completion string is trimmed with the matching rule from upstream SAFIM (`safim.postprocess_utils`).
- For **`block`**, every JSONL object must also include **`prefix`** and **`suffix`** (code before and after the completion; equivalent to splitting `eval_prompt` on `{{completion}}`). Values must not be JSON `null`.
- **`block`** post-processing uses **tree-sitter**; you need a built grammar shared library (see [Tree-sitter](#tree-sitter) below).
- When **`post_process=True`**, each entry under **`eval`** in the output JSON also includes **`completion_before`** and **`completion_after`** (JSON `null` if there was no matching completion row for that task).

**Parallel evaluation** (`max_workers > 1`):

- Uses **`ProcessPoolExecutor`**: one worker process runs HTTP requests with its own client (similar in spirit to LiveCodeBench’s process pool).
- On **Windows**, invoke evaluation from a script guarded with `if __name__ == "__main__":` so worker processes can import the package.
- If the OS refuses new processes or threads, the code may **halve** the pool size and retry, or **fall back to sequential** with a warning.

### Environment variables

| Variable | Purpose |
|----------|---------|
| `SAFIM_MAX_WORKERS_CAP` | Upper bound on process pool size (default `32`) when `max_workers` is large. |
| `SAFIM_TREE_SITTER_SO` | Path to the **tree-sitter** grammar `.so` used for `block` post-processing (preferred). |
| `SAFIM_TREE_SITTER_LIB` | Same as above (alternate env name, e.g. NeMo execeval Docker). Checked only if `SAFIM_TREE_SITTER_SO` is unset. |
| *(default)* | If neither env var is set, `safim/tree_sitter.so` next to the installed package is tried. |
| `SAFIM_HTTP_TIMEOUT_SEC` | Optional HTTP timeout (seconds) for execution API `GET`/`POST`. If unset, no timeout (previous behavior). |

### Tree-sitter

Upstream SAFIM builds a single shared library containing Python, Java, C++, and C# grammars (`Language.build_library` in their `ast_utils`). Set **`SAFIM_TREE_SITTER_SO`** or **`SAFIM_TREE_SITTER_LIB`** to that file, or place it as `tree_sitter.so` beside `safim/ast_utils.py` after install. Without it, **`block`** post-processing will fail with a clear `FileNotFoundError`.

### Example driver

```python
import subprocess
import sys

completion_path = "path/to/your_completions.jsonl"
eval_output_path = "path/to/eval_results.json"


def install_from_git(git_url):
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", git_url])
        print("Package installed successfully!")
    except subprocess.CalledProcessError as e:
        print(f"Error during installation: {e}")


if __name__ == "__main__":
    try:
        from safim.evaluate import evaluate
    except ImportError:
        print("Package 'safim' not found. Attempting to install...")
        install_from_git("git+https://github.com/wasiahmad/safim.git")
        try:
            from safim.evaluate import evaluate
        except ImportError:
            print("Failed to install 'safim'. Please install it manually.")
            raise

    evaluate("block", completion_path, eval_output_path)
    # With post-processing and parallelism, e.g.:
    # evaluate("block", completion_path, eval_output_path, post_process=True, max_workers=8)
```

For **generation**, prompts, and the full upstream toolchain, see the [official SAFIM repository](https://github.com/gonglinyuan/safim/tree/main).
