# SAFIM

Official repository: https://github.com/gonglinyuan/safim/tree/main

This is an unofficial modification of SAFIM official repository to support execution-based evaluation of SAFIM tasks. The goal is to keep a bare minimum code in this repository such that following style of evaluation are possible.

## Usage

Use this package as a **library** from your own script: point it at a JSONL file of model completions and an output path for aggregated results. Tasks with unit tests call an HTTP execution API at `http://localhost:{port}` (default port `5000`); you need that service running separately—this repo only contains the client (`safim.exec_utils`).

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
```