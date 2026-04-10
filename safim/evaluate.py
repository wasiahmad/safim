import ast
import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from tqdm import tqdm

from safim.data_utils import load_dataset, stream_jsonl
from safim import exec_utils
from safim.exec_utils import APICommunication, build_execeval, run_test


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
        except:
            pass  # If parsing fails, fall back to simple string comparison

    return code1 == code2


def _evaluate_one(problem, completion, client):
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
    return tid, result, passed


def _make_thread_local_client(port: int):
    tls = threading.local()

    def get_client():
        if not hasattr(tls, "api"):
            tls.api = APICommunication(server_url=f"http://localhost:{port}")
        return tls.api

    return get_client


def evaluate(
        completion_type: str,
        completion_path: str,
        output_path: str,
        language: str = None,
        port: int = 5000,
        max_workers: int = 1,
):
    if max_workers < 1:
        raise ValueError("max_workers must be >= 1")

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
    results = {}
    pass_cnt = 0

    if max_workers == 1:
        client = exec_utils.execeval
        for problem in tqdm(problems):
            completion = completions.get(problem["task_id"])
            tid, result, passed = _evaluate_one(problem, completion, client)
            pass_cnt += int(passed)
            results[tid] = [
                {"task_id": tid, "result": result, "passed": passed}
            ]
    else:
        get_client = _make_thread_local_client(port)

        def _worker(problem):
            completion = completions.get(problem["task_id"])
            return _evaluate_one(problem, completion, get_client())

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = [pool.submit(_worker, p) for p in problems]
            for fut in tqdm(as_completed(futures), total=len(futures)):
                tid, result, passed = fut.result()
                pass_cnt += int(passed)
                results[tid] = [
                    {"task_id": tid, "result": result, "passed": passed}
                ]

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
