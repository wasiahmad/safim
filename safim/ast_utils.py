import os
from typing import Dict

from tree_sitter import Language, Parser

_TS_LANG: Dict[str, Language] = {}
_PARSERS: Dict[str, Parser] = {}
_TS_LANG_NAME = {
    "python": "python",
    "java": "java",
    "cpp": "cpp",
    "csharp": "c_sharp",
}


def _tree_sitter_lib_path() -> str:
    """Path to the combined grammar ``.so`` (``Language.build_library`` output).

    Resolution order:

    1. ``SAFIM_TREE_SITTER_SO`` — preferred name.
    2. ``SAFIM_TREE_SITTER_LIB`` — legacy / NeMo execeval Docker (same file).
    3. ``<package>/tree_sitter.so`` next to this module (optional vendored build).
    """
    default_pkg = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tree_sitter.so")
    return (
        os.environ.get("SAFIM_TREE_SITTER_SO")
        or os.environ.get("SAFIM_TREE_SITTER_LIB")
        or default_pkg
    )


def _attach_language(parser: Parser, language: Language) -> None:
    """``tree-sitter`` 0.20.x uses ``set_language``; 0.22+ uses ``parser.language = …``."""
    set_lang = getattr(parser, "set_language", None)
    if callable(set_lang):
        set_lang(language)
    else:
        parser.language = language


def get_parser(lang: str) -> Parser:
    """Return a tree-sitter Parser for ``lang`` (requires built combined grammar ``.so``)."""
    if lang not in _TS_LANG_NAME:
        raise KeyError(f"unsupported language for tree-sitter: {lang!r}")
    if lang not in _TS_LANG:
        lib_path = _tree_sitter_lib_path()
        if not os.path.isfile(lib_path):
            raise FileNotFoundError(
                f"Tree-sitter grammar library not found at {lib_path!r}. "
                "Build it from the official SAFIM repo (see Language.build_library in your setup) "
                "and set SAFIM_TREE_SITTER_SO or SAFIM_TREE_SITTER_LIB to that ``.so`` path."
            )
        _TS_LANG[lang] = Language(lib_path, _TS_LANG_NAME[lang])
    if lang not in _PARSERS:
        parser = Parser()
        _attach_language(parser, _TS_LANG[lang])
        _PARSERS[lang] = parser
    return _PARSERS[lang]


class ASTVisitor:

    def __init__(self, with_ndtypes=False, print_debug_outputs=False):
        self.with_ndtypes = with_ndtypes
        self.print_debug_outputs = print_debug_outputs
        self.stack = []
        self.ndtypes = []

    def enter(self, node) -> bool:
        return True

    def leave(self, node):
        pass

    def enter_leaf(self, node):
        pass

    def print_stack(self, node):
        depth = len(self.stack)
        print(" " * depth * 2 + node.type)

    def on_enter(self, node) -> bool:
        if self.print_debug_outputs:
            self.print_stack(node)
        if self.with_ndtypes:
            self.ndtypes.append((node.start_byte, True, node.type))
        enter_fn = getattr(self, "enter_%s" % node.type, self.enter)
        r = enter_fn(node)
        if node.child_count == 0:
            self.enter_leaf(node)
        self.stack.append(node.type)
        return r

    def on_leave(self, node):
        assert self.stack.pop() == node.type
        leave_fn = getattr(self, "leave_%s" % node.type, self.leave)
        r = leave_fn(node)
        # print("on leave ", node.type)
        if self.with_ndtypes:
            self.ndtypes.append((node.end_byte, False, node.type))
        return r

    def walk(self, root_node):
        if root_node is None:
            return

        cursor = root_node.walk()
        has_next = True

        while has_next:
            current_node = cursor.node

            # Step 1: Try to go to next child if we continue the subtree
            if self.on_enter(current_node):
                has_next = cursor.goto_first_child()
            else:
                has_next = False

            # Step 2: Try to go to next sibling
            if not has_next:
                self.on_leave(current_node)
                has_next = cursor.goto_next_sibling()

            # Step 3: Go up until sibling exists
            while not has_next and cursor.goto_parent():
                self.on_leave(cursor.node)  # We will never return to this specific parent
                has_next = cursor.goto_next_sibling()

    def __call__(self, root_node):
        return self.walk(root_node)


class ErrorCheckVisitor(ASTVisitor):
    def __init__(self, with_ndtypes=False):
        super().__init__(with_ndtypes)
        self.error_cnt = 0

    def enter_ERROR(self, node):
        if node.text.decode("utf-8") != ";":
            self.error_cnt += 1
