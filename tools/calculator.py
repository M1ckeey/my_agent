"""计算器工具：安全地求值数学表达式。

这里刻意不用 eval()——它会把 `__import__("os").system("rm -rf /")` 也一并执行。
改用 ast 白名单遍历：只放过算术运算和白名单函数，其他一律报错。
"""

from __future__ import annotations

import ast
import math
import operator

_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}

_UNARY_OPS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}

_FUNCS = {
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
    "pow": pow,
    "sqrt": math.sqrt,
}


def _eval(node: ast.AST) -> float:
    """递归求值，只认白名单里的节点类型。"""
    if isinstance(node, ast.Expression):
        return _eval(node.body)

    if isinstance(node, ast.Constant):
        # bool 是 int 的子类，这里要排掉 True / False
        if isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return node.value
        raise ValueError(f"不支持的常量: {node.value!r}")

    if isinstance(node, ast.BinOp):
        op = _BIN_OPS.get(type(node.op))
        if op is None:
            raise ValueError(f"不支持的运算符: {type(node.op).__name__}")
        return op(_eval(node.left), _eval(node.right))

    if isinstance(node, ast.UnaryOp):
        op = _UNARY_OPS.get(type(node.op))
        if op is None:
            raise ValueError(f"不支持的一元运算符: {type(node.op).__name__}")
        return op(_eval(node.operand))

    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in _FUNCS:
            raise ValueError("只支持 abs / round / min / max / pow / sqrt 这几个函数")
        if node.keywords:
            raise ValueError("不支持关键字参数")
        return _FUNCS[node.func.id](*[_eval(arg) for arg in node.args])

    raise ValueError(f"不支持的语法: {type(node).__name__}")


def calculator(expression: str) -> str:
    """计算数学表达式。支持 + - * / // % ** 和 abs/round/min/max/pow/sqrt。

    Args:
        expression: 数学表达式，例如 "2 + 3 * 4"、"(1+2)**3"、"sqrt(16)"

    Returns:
        计算结果的字符串。整数会去掉多余的 .0。
    """
    expr = str(expression).strip()
    if not expr:
        raise ValueError("表达式为空")

    tree = ast.parse(expr, mode="eval")  # 语法错误会在这里抛 SyntaxError
    value = _eval(tree)

    # 3.0 -> "3"，但 3.5 保持原样
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value)
