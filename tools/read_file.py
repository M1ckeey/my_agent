"""读文件工具：把磁盘上的文本读进上下文。

存在的理由和上下文压缩绑在一起——压缩会把大的工具结果转存到磁盘，在 messages
里只留一个路径，模型得能凭那个路径把内容取回来。没有这个工具，那些路径就是
死信息：模型看得见、够不着（见 README「已知限制」）。所以它是两个用途共用的
入口：读项目源码，以及回收被压缩掉的工具结果。

六条边界，都是刻意的：

  1. **只能读项目目录里的文件**。不设边界的话，「读什么」就完全由模型决定了，
     读 ~/.ssh/id_rsa 和读 README.md 对它来说是同一件事。相对路径按项目根解析，
     绝对路径也收，但一样要落在项目内；resolve() 会展开符号链接，所以指向
     项目外的软链接同样拦得住。
  2. **`.env` 和 `.git/` 直接拒绝**，`.env.*` 这种变体一起拦掉。.env 里是 API key，
     读进上下文等于把密钥打包发给模型服务商——README 里那句「永远不要提交」
     管的是 git，这里管的是上下文，两个都是出口。.git/ 里是对象库，读了没用
     还占地方。判据是路径的**任意一段**，不是只看文件名，所以子目录里的
     .env 也拦得住。
  3. **输出有上限，而且是三层**：单次 200 行（可调到 1000）、单行 2000 字符、
     总量 8000 字符。这个工具是上下文的主要入口，不能让一次调用把窗口撑爆。
     行数和字符是两把不同的锁——200 行中文和一行的 2000 字符差一个数量级，
     光靠行数管不住总量。字符上限和压缩管线的「大结果」阈值同量级：超过它
     就该轮到压缩出手，而不是靠 read_file 一次全塞进来。
  4. **行号是带上的**（`     1\t内容`）。这个项目的文档和讨论全靠 `loop.py:250`
     这种引用，模型看到行号才说得出「哪一行」。代价是每行多几个字符，值。
  5. **读不完要给出续读指令**。表头写明这是哪一段、因为什么停下、下一次该用
     哪个 offset——模型据此自己决定要不要接着读，而不是默认文件就这么长。
  6. **目录返回一层清单**。为压缩准备的：.task_outputs/ 里会攒下一堆结果文件，
     模型得先看看有什么，再决定取哪一个。二进制文件按 NUL 字节挡掉，免得
     一张 PNG 把乱码灌进上下文。
"""

from __future__ import annotations

import io
from pathlib import Path

# 项目根目录。路径按它解析，也只有它里面的文件读得到。
# 和 agent/loop.py 锚定 .env 用的是同一个办法：__file__ 往上两级。
_ROOT = Path(__file__).resolve().parent.parent

# 这些名字出现在路径的任何一段上就拒绝。.env.xxx 这种变体一起拦掉，
# 别留给「换个后缀就绕过去了」这种口子。
_DENIED = frozenset({".git", ".env"})

# 单行超过这么多字符就截断。压缩后的代码、minify 过的 JSON 都可能整文件一行，
# 不拦的话一次读一行就把预算吃光。
_LINE_LIMIT = 2000

# 一次调用的字符总预算，和行数上限是两把不同的锁：
# 200 行中文和一行的 2000 字符差一个数量级，光靠行数管不住总量。
# 这个值和压缩管线的「大结果」阈值同量级——再大就该轮到压缩出手了。
_MAX_CHARS = 8000

# limit 的上下界。**默认值 200 不在这里**，它写在函数签名上——签名和 docstring
# 是给模型的唯一真相来源（见 tools/base.py 开头），在这儿再写一遍就是第二处真相。
_MAX_LIMIT = 1000
_MIN_LIMIT = 1


def _display(path: Path) -> str:
    """给模型看的路径：相对项目根，用正斜杠（Windows 上也一样）。"""
    return path.relative_to(_ROOT).as_posix()


def _resolve(path: str) -> Path:
    """把模型给的路径解析成项目内的绝对路径，越界就报错。

    报错信息是提示词的一部分——模型看到「在项目目录之外」才知道该换个路径，
    而不是原样重试。
    """
    raw = str(path).strip()
    if not raw:
        raise ValueError("路径为空")

    # 相对路径按项目根解析；绝对路径也收，但一样要落在项目内
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = _ROOT / candidate

    # resolve() 会展开符号链接，所以指向项目外的软链接在这里也会被抓到
    resolved = candidate.resolve()

    if resolved != _ROOT and _ROOT not in resolved.parents:
        raise ValueError(
            f"{raw!r} 在项目目录之外。这个工具只读得到 {_ROOT} 里的文件。"
        )

    # 逐段检查，不是只看文件名：子目录里的 .env、.git/hooks 都要拦得住
    for part in resolved.relative_to(_ROOT).parts:
        if part in _DENIED or part.startswith(".env"):
            raise ValueError(
                f"拒绝读取 {part}：这里放的是密钥或版本库内部数据，"
                f"内容不适合进入对话上下文。"
            )
    return resolved


def _open_text(path: Path) -> io.TextIOWrapper:
    """以文本方式打开；二进制文件在这里就挡掉。

    不挡的话，读一张 PNG 会把一堆乱码灌进上下文——白花钱，还会让模型
    对着一堆 `\\x89PNG` 猜半天。判据用 NUL 字节：文本文件（UTF-8/GBK 都好）
    里不会出现它，二进制文件里几乎必然有。
    """
    handle = path.open("rb")
    head = handle.read(4096)
    if b"\x00" in head:
        handle.close()
        raise ValueError(f"{_display(path)} 看起来是二进制文件，读不了。")
    handle.seek(0)
    # errors="replace"：编码不对时用问号顶掉，而不是抛 UnicodeDecodeError
    return io.TextIOWrapper(handle, encoding="utf-8", errors="replace")


def _list_dir(target: Path) -> str:
    """目录返回一层清单。

    留这一手是因为压缩转存的 .task_outputs/ 里会攒下一堆结果文件，
    模型需要先看看有什么，再决定取哪一个。
    """
    entries = sorted(
        target.iterdir(),
        key=lambda item: (item.is_file(), item.name.lower()),
    )
    if not entries:
        return f"{_display(target)} 是空目录。"

    lines = [
        f"  {item.name}/" if item.is_dir() else f"  {item.name}（{item.stat().st_size} 字节）"
        for item in entries
    ]
    return f"{_display(target)} 下有 {len(entries)} 项：\n" + "\n".join(lines)


def read_file(path: str, offset: int = 1, limit: int = 200) -> str:
    """读取项目目录内的文本文件或列目录，按行返回带行号的内容。

    Args:
        path: 文件路径。相对路径按项目根目录解析，例如 "README.md"、
            "agent/loop.py"、"tools/"；也可以给绝对路径，但必须落在项目目录内
        offset: 从第几行开始读，行号从 1 开始，默认 1。文件没读完时，返回的
            开头会告诉你下一次该用哪个 offset 接着读
        limit: 最多读多少行，默认 200，上限 1000
    """
    target = _resolve(path)

    if target.is_dir():
        return _list_dir(target)
    if not target.is_file():
        raise FileNotFoundError(f"没有这个文件：{_display(target)}")

    start = max(1, int(offset))
    count = max(_MIN_LIMIT, min(int(limit), _MAX_LIMIT))

    rendered: list[str] = []
    chars = 0
    last = start - 1
    more = False     # 后面还有内容没显示
    capped = False   # 且是因为字符预算见底，不是行数满了

    with _open_text(target) as handle:
        for index, raw in enumerate(handle, 1):
            if index < start:
                continue  # 跳过的行不计数，offset 是给模型续读用的
            if len(rendered) >= count:
                more = True
                break

            line = raw.rstrip("\n")
            if len(line) > _LINE_LIMIT:
                line = line[:_LINE_LIMIT] + " …（本行过长，已截断）"

            # 行号右对齐到 6 位 + Tab：Tab 让终端和模型都容易把行号切出去，
            # 右对齐则让列不随行号位数跳动
            numbered = f"{index:>6}\t{line}"
            if chars + len(numbered) > _MAX_CHARS:
                more = capped = True
                break

            chars += len(numbered) + 1
            rendered.append(numbered)
            last = index

    if not rendered:
        if start > 1:
            return f"{_display(target)} 没有第 {start} 行——文件比这短。"
        return f"{_display(target)} 是空文件。"

    # 表头先说清楚「这是哪一段、后面还有没有」，模型据此决定要不要续读
    header = f"{_display(target)} 第 {start}-{last} 行"
    if more:
        header += (
            f"（本次已达 {_MAX_CHARS} 字符上限）" if capped else "（已达 limit）"
        )
        header += f"，后面还有内容：用 offset={last + 1} 接着读"

    return f"{header}\n" + "\n".join(rendered)
