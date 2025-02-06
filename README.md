# SAFIM

Official repository: https://github.com/gonglinyuan/safim/tree/main

This is an unofficial modification of SAFIM official repository to support execution-based evaluation of SAFIM tasks. The goal is to keep a bare minimum code in this repository such that following style of evaluation are possible.

```python
import subprocess
import sys

completion_path = "path_to_custom_model_completions"
eval_output_path = "path_to_custom_model_completions"


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

    evaluate("block", sample_path, eval_output_path)
```