import ast
import json
import re

import tqdm
from tqdm import tqdm
from datetime import datetime
from safim.data_utils import load_dataset, stream_jsonl
from safim.exec_utils import build_execeval, run_test


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


def evaluate(
        completion_type: str,
        completion_path: str,
        output_path: str,
        language: str = None,
        port: int = 5000
):
    build_execeval(port)

    completions = {completion["task_id"]: completion for completion in stream_jsonl(completion_path)}
    pass_cnt, total = 0, 0
    results = dict()
    for problem in tqdm(load_dataset(completion_type)):
        if language is not None and problem["lang"] != language:
            continue
        if problem["task_id"] not in completions:
            result = "EMPTY"
            passed = False
        else:
            completion = completions[problem["task_id"]]
            if "unit_tests" in problem and problem["unit_tests"]:
                if completion['completion'] == problem["ground_truth"]:
                    result = "PASSED"
                    passed = True
                else:
                    result, passed = run_test(problem, completion)
            else:
                if syntax_match(completion['completion'], problem["ground_truth"], problem["lang"]):
                    result = "EXACT_MATCH"
                    passed = True
                else:
                    result = "WRONG_ANSWER"
                    passed = False

        if not completion['completion'].strip() and not passed:
            result = "EMPTY"
        if problem["lang"] == "python" and not passed:
            full_code = problem['eval_prompt'].replace("{{completion}}", completion['completion'])
            if "unit_tests" in problem and not is_parsable(full_code):
                result = "COMPILATION_ERROR"

        pass_cnt += int(passed)
        total += 1
        results[problem["task_id"]] = [{
            "task_id": problem["task_id"],
            "result": result,
            "passed": passed
        }]

    pass_at_1 = pass_cnt / total * 100
    print(f"Pass {pass_cnt} / Total {total}")
    print(f"Pass@1: {pass_at_1 :.04f}%")

    # save_eval_results
    output_results = dict()
    output_results["date"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    output_results["pass_at_k"] = {"pass@1": pass_at_1}
    output_results["eval"] = results

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output_results, f, indent=4)
