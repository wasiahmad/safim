import ast
import json
import os
import re
import warnings
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from typing import Optional

from tqdm import tqdm

from safim.data_utils import load_dataset, stream_jsonl
from safim import exec_utils
from safim.exec_utils import APICommunication, build_execeval, run_test
from safim.postprocess_utils import (
    POST_PROCESS_COMPLETION_TYPES,
    postprocess_completion,
)

# Set in each process by ProcessPoolExecutor initializer (pickle-safe worker path).
_process_client: Optional[APICommunication] = None


def is_parsable(code: str) -> bool:
    try:
        ast.parse(code)
        return True
    except SyntaxError:
        return False


def get_function_call_params(node):
    positional_args = [ast.dump(arg) for arg in node.args]
    keyword_args = {kw.arg: ast.dump(kw.value) for kw in node.keywords}
    return positional_args, keyword_args


def function_calls_match(call1, call2):
    params1 = get_function_call_params(call1)
    params2 = get_function_call_params(call2)
    return params1 == params2


def syntax_match(code1, code2, lang):
    code1 = re.sub(r'\s+', '', code1).strip()
    code2 = re.sub(r'\s+', '', code2).strip()
    if lang == "python":
        try:
            tree1 = ast.parse(code1, mode='eval')
            tree2 = ast.parse(code2, mode='eval')

            if isinstance(tree1.body, ast.Call) and isinstance(tree2.body, ast.Call):
                return function_calls_match(tree1.body, tree2.body)
        except (SyntaxError, ValueError, TypeError):
            pass  # Fall back to normalized string comparison

    return code1 == code2


def _maybe_postprocess_completion(problem, completion, post_process, dataset_completion_type):
    if not post_process or completion is None:
        return completion
    if dataset_completion_type == "block":
        prefix = completion.get("prefix")
        suffix = completion.get("suffix")
        if prefix is None or suffix is None:
            raise ValueError(
                "completion JSONL rows must include non-null 'prefix' and 'suffix' when "
                "post_process=True and completion_type='block'"
            )
        text = postprocess_completion(
            dataset_completion_type,
            problem["lang"],
            completion["completion"],
            prefix=str(prefix),
            suffix=str(suffix),
        )
    else:
        text = postprocess_completion(
            dataset_completion_type,
            problem["lang"],
            completion["completion"],
        )
    return {**completion, "completion": text}


def _evaluate_one(problem, completion, client, post_process=False, dataset_completion_type=None):
    completion_before = (
        completion.get("completion") if completion is not None else None
    )
    completion = _maybe_postprocess_completion(
        problem, completion, post_process, dataset_completion_type
    )
    if completion is None:
        result = "EMPTY"
        passed = False
    else:
        if "unit_tests" in problem and problem["unit_tests"]:
            if completion["completion"] == problem["ground_truth"]:
                result = "PASSED"
                passed = True
            else:
                result, passed = run_test(problem, completion, client=client)
        else:
            if syntax_match(
                completion["completion"], problem["ground_truth"], problem["lang"]
            ):
                result = "EXACT_MATCH"
                passed = True
            else:
                result = "WRONG_ANSWER"
                passed = False

    if completion is not None and not completion["completion"].strip() and not passed:
        result = "EMPTY"
    if (
        completion is not None
        and problem["lang"] == "python"
        and not passed
    ):
        full_code = problem["eval_prompt"].replace(
            "{{completion}}", completion["completion"]
        )
        if "unit_tests" in problem and not is_parsable(full_code):
            result = "COMPILATION_ERROR"

    tid = problem["task_id"]
    row = {"task_id": tid, "result": result, "passed": passed}
    if post_process:
        row["completion_before"] = completion_before
        row["completion_after"] = (
            completion.get("completion") if completion is not None else None
        )
    return tid, row


def _process_pool_init(port: int) -> None:
    global _process_client
    _process_client = APICommunication(server_url=f"http://localhost:{port}")


def _process_pool_worker(task: tuple) -> tuple:
    """Top-level for pickling."""
    problem, completion, post_process, dataset_completion_type = task
    if _process_client is None:
        raise RuntimeError("process pool client not initialized")
    return _evaluate_one(
        problem,
        completion,
        _process_client,
        post_process=post_process,
        dataset_completion_type=dataset_completion_type,
    )


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _capped_pool_workers(requested: int) -> int:
    """Cap process count (pid / memory limits in containers)."""
    cap = max(1, _env_int("SAFIM_MAX_WORKERS_CAP", 32))
    return min(requested, cap)


def _run_sequential(
    problems, completions, client, post_process=False, dataset_completion_type=None
):
    results = {}
    pass_cnt = 0
    for problem in tqdm(problems):
        completion = completions.get(problem["task_id"])
        tid, row = _evaluate_one(
            problem,
            completion,
            client,
            post_process=post_process,
            dataset_completion_type=dataset_completion_type,
        )
        pass_cnt += int(row["passed"])
        results[tid] = [row]
    return results, pass_cnt


def _run_parallel(
    problems, completions, port, pool_workers, post_process, dataset_completion_type
):
    tasks = [
        (p, completions.get(p["task_id"]), post_process, dataset_completion_type)
        for p in problems
    ]
    results = {}
    pass_cnt = 0
    with ProcessPoolExecutor(
        max_workers=pool_workers,
        initializer=_process_pool_init,
        initargs=(port,),
    ) as executor:
        futures = [executor.submit(_process_pool_worker, t) for t in tasks]
        for fut in tqdm(as_completed(futures), total=len(futures)):
            tid, row = fut.result()
            pass_cnt += int(row["passed"])
            results[tid] = [row]
    return results, pass_cnt


def _is_pool_resource_failure(exc: BaseException) -> bool:
    if isinstance(exc, RuntimeError):
        msg = str(exc).lower()
        return "can't start new thread" in msg or "unable to start" in msg
    if isinstance(exc, OSError):
        # EAGAIN, ENOMEM, EMFILE — common when hitting pid / fd / memory limits
        return exc.errno in (11, 12, 24)
    return False


def _run_parallel_with_pool_backoff(
    problems, completions, port, pool_workers, post_process, dataset_completion_type
):
    """Retry with fewer worker processes when the OS/container refuses new workers."""
    w = pool_workers
    while True:
        if w <= 1:
            warnings.warn(
                "Could not create a process pool; running sequentially "
                "(tight pid/fd limits — use max_workers=1 or raise cgroup limits).",
                UserWarning,
                stacklevel=3,
            )
            return _run_sequential(
                problems,
                completions,
                exec_utils.execeval,
                post_process=post_process,
                dataset_completion_type=dataset_completion_type,
            )
        try:
            return _run_parallel(
                problems,
                completions,
                port,
                w,
                post_process,
                dataset_completion_type,
            )
        except (RuntimeError, OSError) as e:
            if not _is_pool_resource_failure(e):
                raise
            prev = w
            w = max(1, w // 2)
            warnings.warn(
                f"Process pool size {prev} failed ({e!r}); retrying with {w} workers.",
                UserWarning,
                stacklevel=3,
            )


def evaluate(
        completion_type: str,
        completion_path: str,
        output_path: str,
        post_process: bool = False,
        language: str = None,
        port: int = 5000,
        max_workers: int = 1,
):
    """
    When ``max_workers`` > 1, problems are scored with ``ProcessPoolExecutor``
    (separate processes, one HTTP client per worker — similar to LiveCodeBench).

    When ``post_process`` is True, ``completion_type`` must be ``api``, ``control``,
    or ``block``; model text is trimmed with the matching upstream SAFIM rule before
    scoring. For ``block``, each JSONL object must include string fields ``prefix`` and
    ``suffix`` (code before/after the completion); tree-sitter requires
    ``SAFIM_TREE_SITTER_SO`` / ``tree_sitter.so``.

    On Windows (spawn), run evaluation from a script guarded with
    ``if __name__ == "__main__":`` so worker processes can import this module.
    """
    if max_workers < 1:
        raise ValueError("max_workers must be >= 1")
    if post_process and completion_type not in POST_PROCESS_COMPLETION_TYPES:
        raise ValueError(
            f"post_process=True requires completion_type in "
            f"{sorted(POST_PROCESS_COMPLETION_TYPES)}; got {completion_type!r}"
        )

    build_execeval(port)

    completions = {
        completion["task_id"]: completion
        for completion in stream_jsonl(completion_path)
    }
    problems = [
        p
        for p in load_dataset(completion_type)
        if language is None or p["lang"] == language
    ]
    total = len(problems)
    if max_workers == 1:
        pool_workers = 1
    else:
        pool_workers = _capped_pool_workers(max_workers)
        if pool_workers < max_workers:
            warnings.warn(
                f"max_workers={max_workers} capped to {pool_workers} "
                f"(set SAFIM_MAX_WORKERS_CAP to raise the limit).",
                stacklevel=2,
            )
    if pool_workers <= 1:
        results, pass_cnt = _run_sequential(
            problems,
            completions,
            exec_utils.execeval,
            post_process=post_process,
            dataset_completion_type=completion_type,
        )
    else:
        results, pass_cnt = _run_parallel_with_pool_backoff(
            problems,
            completions,
            port,
            pool_workers,
            post_process,
            completion_type,
        )

    pass_at_1 = (pass_cnt / total * 100) if total else 0.0
    print(f"Pass {pass_cnt} / Total {total}")
    print(f"Pass@1: {pass_at_1 :.04f}%")

    # save_eval_results (preserve problem order in output JSON)
    output_results = dict()
    output_results["date"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    output_results["pass_at_k"] = {"pass@1": pass_at_1}
    output_results["eval"] = {
        p["task_id"]: results[p["task_id"]] for p in problems
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output_results, f, indent=4)
