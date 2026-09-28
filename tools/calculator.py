"""计算器工具：安全地求值数学表达式。

这里刻意不用 eval()——它会把 `__import__("os").system("rm -rf /")` 也一并执行。
改用 ast 白名单遍历：只放过算术运算和白名单函数，其他一律报错。
"""

from __future__ import annotations

import ast
import math
import operator

# `**` 是唯一能从小输入炸出巨大结果的运算：`9**9**9` 只有 6 个字符，结果却有约
# 3.7 亿位十进制数字。算大整数乘法时 CPython 不释放 GIL，所以它会连**并行的子
# agent 线程一起冻住**，而且中途不可打断——delegate 上线后这一条从「自己卡住」
# 升级成「拖死整个进程」，必须堵。
#
# 上界按结果的**位数**定，不按指数定。只看指数是不够的：`(10**100)**100000` 的
# 指数只是 100000，看着很乖，结果却是 10 的 1000 万次方。对 |base| >= 2，结果
# 的二进制位数约等于 exp * log2(|base|)，这里用 exp * base.bit_length() 估算——
# 略偏大、偏保守，对一个「防止挂死」的闸门来说正合适。
_MAX_POW_BITS = 1_000_000  # 约 30 万位十进制，实测算完是毫秒级


def _pow(base: float, exp: float, mod: float | None = None) -> float:
    """带规模闸门的幂运算，`**` 和 pow() 共用。

    三参数的 pow(base, exp, mod) 走模幂，中间结果不膨胀，不用拦。
    """
    if mod is None and isinstance(base, int) and isinstance(exp, int) and abs(base) > 1:
        estimated = abs(exp) * base.bit_length()
        if estimated > _MAX_POW_BITS:
            raise ValueError(
                f"{base} 的 {exp} 次方结果约有 {estimated // 3} 位十进制数字，算不完。"
                f"指数请控制在 {_MAX_POW_BITS // base.bit_length()} 以内，"
                f"或者改用对数、直接给估算值。"
            )
    return pow(base, exp) if mod is None else pow(base, exp, mod)


_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: _pow,
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
    "pow": _pow,
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
