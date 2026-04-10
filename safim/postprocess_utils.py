"""
Minimal completion post-processing for evaluation (upstream SAFIM logic subset).

``truncate_line_until_block`` needs a built tree-sitter grammar library; set
``SAFIM_TREE_SITTER_SO`` or place ``tree_sitter.so`` next to ``ast_utils.py``.
See ``safim.ast_utils.get_parser``.
"""

from typing import Optional

from safim.ast_utils import ErrorCheckVisitor, get_parser

# Dataset / eval task names that use post-processing in ``evaluate(..., post_process=True)``.
POST_PROCESS_COMPLETION_TYPES = frozenset({"api", "control", "block"})


def _truncate_to_first_line(code: str) -> str:
    for line in code.splitlines():
        if line.strip():
            return line
    return ""


def _match_prefix_and_suffix(l1, l2):
    p = 0
    while p < len(l1) and p < len(l2):
        if l1[p] == l2[p]:
            p += 1
        else:
            break
    q = 0
    while -q < len(l1) and -q < len(l2):
        if l1[q - 1] == l2[q - 1]:
            q -= 1
        else:
            break
    return p, q


def truncate_line_until_block(
    lang: str, prefix: str, suffix: str, completion: str
) -> str:
    """
    ``prefix`` and ``suffix`` are the code before and after the model completion
    (same idea as splitting ``eval_prompt`` on ``{{completion}}`` in upstream SAFIM).
    """
    parser = get_parser(lang)
    eval_prefix_b = prefix.encode("utf-8")
    eval_suffix_b = suffix.encode("utf-8")
    lines = completion.splitlines(keepends=True)
    while lines:
        completion_b = "".join(lines).encode("utf-8")
        if lang == "python":
            code_bytes_0 = eval_prefix_b + b"pass" + eval_suffix_b
        else:
            code_bytes_0 = eval_prefix_b + eval_suffix_b
        code_bytes_1 = eval_prefix_b + completion_b + eval_suffix_b

        visitor = ErrorCheckVisitor(with_ndtypes=True)
        tree = parser.parse(code_bytes_1)
        visitor(tree.root_node)
        if visitor.error_cnt > 0:
            lines.pop()
            continue
        visitor_trace_1 = [(x, y) for _, x, y in visitor.ndtypes]

        visitor = ErrorCheckVisitor(with_ndtypes=True)
        tree = parser.parse(code_bytes_0)
        visitor(tree.root_node)
        assert visitor.error_cnt == 0
        visitor_trace_0 = [(x, y) for _, x, y in visitor.ndtypes]
        if len(visitor_trace_0) > len(visitor_trace_1):
            lines.pop()
            continue

        prefix_matched, suffix_matched = _match_prefix_and_suffix(
            visitor_trace_0, visitor_trace_1
        )
        matched_diff = len(visitor_trace_0) - (prefix_matched - suffix_matched)
        if lang == "python":
            matched_diff -= 4
        if matched_diff == 0:
            break
        lines.pop()
    return "".join(lines)


def truncate_control(lang: str, completion: str) -> str:
    if lang == "python":
        return _truncate_to_first_line(completion)
    depth = 0
    for i, ch in enumerate(completion):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if depth == -1:
            return completion[:i]
    return completion


def truncate_api_call(completion: str) -> str:
    depth = 0
    for i, ch in enumerate(completion):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth <= 0:
                return completion[: i + 1]
    return completion


def postprocess_completion(
    completion_type: str,
    lang: str,
    text: str,
    *,
    prefix: Optional[str] = None,
    suffix: Optional[str] = None,
) -> str:
    """
    Apply the post-processor for ``completion_type`` (``api`` | ``control`` | ``block``).

    For ``block``, ``prefix`` and ``suffix`` must be provided (e.g. from each JSONL record).
    """
    if completion_type == "block":
        if prefix is None or suffix is None:
            raise ValueError(
                "block post-processing requires non-None prefix and suffix strings"
            )
        return truncate_line_until_block(lang, prefix, suffix, text)
    if completion_type == "control":
        return truncate_control(lang, text)
    if completion_type == "api":
        return truncate_api_call(text)
    raise ValueError(
        f"completion_type must be one of {sorted(POST_PROCESS_COMPLETION_TYPES)}; "
        f"got {completion_type!r}"
    )
