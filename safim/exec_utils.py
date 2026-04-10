import os
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Tuple, Union

import requests

_HTTP_TIMEOUT = os.environ.get("SAFIM_HTTP_TIMEOUT_SEC")
_REQUEST_TIMEOUT = float(_HTTP_TIMEOUT) if _HTTP_TIMEOUT else None


class ExecOutcome(Enum):
    PASSED = "PASSED"  # code executes and output matches expected output
    WRONG_ANSWER = "WRONG_ANSWER"  # code executes and output does NOT matches expected output
    TIME_LIMIT_EXCEEDED = "TIME_LIMIT_EXCEEDED"  # code executes and didn't exit in time, output is ignored in this case
    RUNTIME_ERROR = "RUNTIME_ERROR"  # code failed to execute (crashed)
    COMPILATION_ERROR = "COMPILATION_ERROR"  # code failed to compile
    MEMORY_LIMIT_EXCEEDED = "MEMORY_LIMIT_EXCEEDED"  # code exceeded memory limit during execution


@dataclass
class ExtendedUnittest:
    input: str
    output: List[str] = field(default_factory=list)
    result: Optional[str] = None
    exec_outcome: Optional[ExecOutcome] = None

    def json(self):
        _json = self.__dict__
        if self.exec_outcome is not None:
            _json["exec_outcome"] = self.exec_outcome.name

        return _json

    @classmethod
    def from_json(cls, _json):
        return cls(
            input=_json.get("input", ""),
            output=_json.get("output", list()),
            result=_json.get("result", None),
            exec_outcome=_json.get("exec_outcome", None), )


class APICommunication:
    _session: requests.Session

    def __init__(self, server_url: str = "http://localhost:5000"):
        self._session = requests.Session()
        self.execute_code_url = f"{server_url}/api/execute_code"
        self.get_runtimes_url = f"{server_url}/api/all_runtimes"

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self._session.close()

    def get_runtimes(self):
        return self._session.get(
            self.get_runtimes_url, timeout=_REQUEST_TIMEOUT
        ).json()

    def execute_code(
        self,
        language: str,
        source_code: str,
        unittests: List[dict],
        limits: Optional[dict] = None,
        block_network: bool = True,
        stop_on_first_fail: bool = True,
        use_sanitizer: bool = False,
        compiler_program_name: Optional[str] = None,
        compiler_flags: Optional[str] = None,
        interpreter_cmd: Optional[str] = None,
        interpreter_flags: Optional[str] = None,
        sample_id: Optional[int] = None,
        task_id: Union[str, int, None] = None, ) -> Tuple[List[ExtendedUnittest], Optional[int], Union[str, int, None]]:
        if language is None:
            raise ValueError("EmptyLanguage")

        if source_code is None:
            raise ValueError("EmptySourceCode")

        if unittests is None or len(unittests) == 0:
            raise ValueError("EmptyUnittest")

        request_body = dict(
            language=language,
            source_code=source_code,
            unittests=unittests,
            limits=limits if isinstance(limits, dict) else None,
            compile_cmd=compiler_program_name,
            compile_flags=compiler_flags,
            execute_cmd=interpreter_cmd,
            execute_flags=interpreter_flags,
            block_network=block_network,
            stop_on_first_fail=stop_on_first_fail,
            use_sanitizer=use_sanitizer, )
        try:
            json_response = self._session.post(
                self.execute_code_url,
                json=request_body,
                headers={"Content-Type": "application/json"},
                timeout=_REQUEST_TIMEOUT,
            ).json()
        except requests.exceptions.JSONDecodeError:
            json_response = {
                "task_id": task_id,
                "data": [{"exec_outcome": "COMPILATION_ERROR", "result": "", "passed": False}]
            }

        if "data" not in json_response:
            return json_response, sample_id, task_id

        return (json_response["data"], sample_id, task_id,)


LANG_TO_COMPILER = {
    "cpp": "GNU C++17", "csharp": "Mono C#", "java": "Java 17", "python": "PyPy 3"
}

execeval: APICommunication = None


def run_test(problem, completion, client: Optional[APICommunication] = None):
    global execeval
    api = client if client is not None else execeval
    if api is None:
        raise RuntimeError(
            "No execution API client; call build_execeval(port) or pass client= to run_test."
        )
    assert problem['task_id'] == completion['task_id']
    compiler = LANG_TO_COMPILER.get(problem["lang"])
    if compiler is None:
        raise KeyError(
            f"Unsupported problem language {problem['lang']!r}; "
            f"expected one of {sorted(LANG_TO_COMPILER)}"
        )
    code = problem['eval_prompt'].replace("{{completion}}", completion['completion'])
    result = api.execute_code(
        compiler, code, problem['unit_tests'], task_id=problem['task_id']
    )[0]
    if not (isinstance(result, list) and isinstance(result[0], dict)):
        print(result)
        return "COMPILATION_ERROR", False
    for o in result:
        if o['result'] is not None and len(o['result']) > 1000:
            o['result'] = o['result'][:1000]
    return result, all(o['exec_outcome'] == 'PASSED' for o in result)


def build_execeval(port):
    global execeval
    execeval = APICommunication(server_url=f"http://localhost:{port}")
