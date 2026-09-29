# 阶段二：统一工具系统

阶段二把工具调用改为 OpenAI 原生 tool calling，并建立了统一的工具接口。当前注册的工具有：

- `calculator`：安全计算
- `web_search`：调用 Tavily 搜索网页，需要 `TAVILY_API_KEY`
- `arxiv_search`：调用 arXiv 公共 API
- `read_file`：读取项目目录内的文件
- `plan_research`：生成研究计划
- `delegate`：并行派发子任务

## 添加工具

新增工具通常只需两步：

```python
# tools/weather.py
def weather(city: str, days: int = 1) -> str:
    """查询天气。

    Args:
        city: 城市名
        days: 查询天数
    """
    return "..."
```

然后在 `tools/__init__.py` 注册：

```python
from .weather import weather

TOOLS["weather"] = Tool.of(weather)
```

## Tool 接口

`tools.base.Tool.of()` 从函数签名和 docstring 自动生成：

- 工具名：函数名
- 工具描述：docstring 第一段
- 参数描述：`Args:` 段
- JSON Schema：类型、属性和必填参数

同一份 schema 同时用于发送给模型和处理模型参数。执行前，`Tool.run()` 会把常见的字符串参数转换为 `int`、`float` 或 `bool`。

## 错误处理

`tools.run_tool()` 是统一入口。工具不存在、参数 JSON 无效、参数类型不对或函数抛异常时，都会转换成错误文本并作为工具结果回填给模型。模型可以根据错误重试或改用其他工具。

## 外部服务

- Tavily 请求超时为 30 秒；连接错误和部分 5xx 响应会重试一次。
- arXiv 请求有 3 秒间隔限制，并对限流失败进行退避；持续限流时会暂时熔断。
- LLM 使用独立的连接和读取超时，并对连接错误、超时进行外层重试。
- 工具结果会直接进入消息历史，当前还没有通用的上下文压缩。

`agent/loop.py` 只负责循环控制，不需要知道每个工具的具体实现。

