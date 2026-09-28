# 阶段一：最小 ReAct 循环

一个工具（`calculator`）、一个循环，跑通「模型决定调工具 → 执行 → 回填 → 再决定」。

三个文件：`agent/loop.py`（控制流）、`agent/state.py`（状态）、`tools/calculator.py`（唯一的工具）。

> **后续变更**：加子 agent（`agent/subagent.py`）时，`loop.py` 里的 LLM 调用层
> 被抽到了 `agent/llm.py`（子 agent 也要调模型，两边共用），工具执行被搬到了
> `tools.run_tool()`。所以本文里凡是标注 `loop.py` 行号的地方，都已按拆分后的
> 位置更新；`loop.py` 现在只剩控制流。详见 [step3.md](step3.md)。

## 一、循环

`run_agent`（`loop.py:84`）就是一个 `for`，每轮四件事：

| # | 做什么 | 位置 |
| --- | --- | --- |
| 1 | 调模型，带上 `tools=[...]` | `call_llm`（`llm.py:109`），`for turn in range(1, max_steps+1)`（`loop.py:112`） |
| 2 | 没有 `tool_calls`？→ 这条消息就是最终答案，结束 | `loop.py:124` |
| 3 | 有 → 逐个执行，结果作为 `role:"tool"` 消息回填 | `loop.py:136` |
| 4 | 回到 1 | — |

**终止条件是内建的，不需要约定。** 判据就是「有没有 `tool_calls`」：

```python
if not message.tool_calls:
    state.final_answer = final_text(message)
    break
```

这里没有「finish 动作」。文本模式得让模型显式说 `Action: finish`，因为那时循环
分不清「一段文本」和「一个调用」；而 `tool_calls` 的有无本身就是判据。

**刹车是 `max_steps=8`**（`loop.py:87`）：跑满还没 break，就返回「达到最大步数，任务未完成」。

**一轮可以返回多个 `tool_call`**（并行调用）。回填时每个 `tool_call_id` 必须配
**有且只有一条** `role:"tool"` 消息，少了下一轮服务端直接报错。

## 二、calculator：唯一的工具

`tools/calculator.py:71`

```python
def calculator(expression: str) -> str:
    """计算数学表达式。支持 + - * / // % ** 和 abs/round/min/max/pow/sqrt。

    Args:
        expression: 数学表达式，例如 "2 + 3 * 4"、"(1+2)**3"、"sqrt(16)"
    """
```

**签名 + docstring 就是给模型的全部接口**——模型只看这两个，schema 由它们推导
（推导规则见 [step2.md](step2.md) 第三节）。

**为什么不用 `eval()`**：`eval` 会把 `__import__("os").system("rm -rf /")` 一起执行。
改用 `ast` 白名单遍历（`_eval:38`）：`ast.parse` 先把表达式解析成语法树，
然后只放行算术运算（`_BIN_OPS:13`）和六个函数（`_FUNCS:28`），
其他节点类型一律 `ValueError`。

关键在于**解析和执行是分开的**：`ast.parse` 只把字符串变成树，不求值。
安全性来自「白名单外的节点不执行」，而不是「黑名单里的危险函数被禁掉」。

## 三、状态：一份轨迹，两个视图

`AgentState`（`state.py:38`）里同一份轨迹存两份，不是冗余：

| 视图 | 格式 | 给谁 |
| --- | --- | --- |
| `messages` | OpenAI chat 格式 | **机器**，每轮原样发给模型 |
| `steps` | 结构化 `Step` 对象 | **人**，`trace()`（`:67`）打日志用 |

分开是为了后面做上下文压缩——**压 `messages`，留 `steps`**。
机器要的那份可以裁剪，人看的那份要完整。

## 四、一次完整运行

问题：`123*456-1000 等于多少`（`verbose=True` 的真实输出）

```
── step 1 ──
  Action      : calculator
  Action Input: {"expression": "123*456-1000"}
  Observation : 55088

── step 2 ──
  **123 × 456 − 1000 = 55088**

计算过程：123 × 456 = 56088，再减去 1000，得 **55088**。
```

对应到 `messages` 的增长：

```
[system, user]
   ↓ step 1  模型返回 tool_calls: calculator({"expression": "123*456-1000"})
[system, user, assistant(tool_calls), tool("55088")]
   ↓ step 2  模型返回一条不带 tool_calls 的消息
              ↓ 它的 content 就是最终答案，循环结束
```

`step 2` 那段文字是**流式打出来的**（边生成边显示），所以表头下面直接就是内容，
没有 Action / Observation 行——那一步什么也没调。

## 五、入口

`main.py` —— 带参数就一次性跑，不带参数进交互循环：

```
python main.py "123 * 456 等于多少"
python main.py
```

两个小地方：启动时 `_fix_windows_console()`（`:20`）把 stdout 切成 UTF-8，
否则 Windows 默认 GBK，打中文会炸；`_solve`（`:30`）只负责调 `run_agent`，
**不再额外打印答案**——最终答案在生成时就已经流式打在终端上了（见第四节），
再打一遍同一段文字就会出现两次。
