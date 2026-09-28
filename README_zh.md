# DataVoyager

**一句需求，生成可用于微调的 QA 数据集。**

告诉 DataVoyager 你想让模型学什么。它会从网络寻找资料、提取和筛选正文，再生成问答。你拿到的是 JSONL 数据文件、每条问答的来源，以及本次运行的模型 API 用量。

[English](README.md) · [使用说明](docs/quickstart.md) · [架构](docs/architecture.md)

## 从一句话开始

> 为 Python 初学者制作一份关于生成器的问答数据集。用中文回答，附简短代码示例，优先参考 Python 官方文档。

```bash
datavoyager build "为 Python 初学者制作一份关于生成器的问答数据集。用中文回答，附简短代码示例，优先参考 Python 官方文档。" \
  --output data/python-qa.jsonl
```

导出为 Alpaca 格式。下面是一条格式示例：

```json
{
  "instruction": "调用 Python 生成器函数时，会立刻执行函数体吗？",
  "input": "",
  "output": "不会。调用时先返回一个生成器迭代器；请求下一个值时，函数体才开始执行，运行到 yield 处暂停。例如：\n\ndef numbers():\n    yield 1\n\ng = numbers()\nprint(next(g))  # 执行到 yield，输出 1"
}
```

也可以把需求换成产品文档问答、课程练习或某个专业领域的知识问答。在需求里写清主题、面向谁、用什么语言、回答要多详细。

## 配置你的 API

需要 Python 3.10+，以及支持 Chat Completions 和 JSON 输出的模型接口。

```bash
git clone https://github.com/haolpku/DataVoyager.git
cd DataVoyager
python -m venv .venv
source .venv/bin/activate
pip install -e .

export DATAVOYAGER_BASE_URL="https://your-provider.example/v1"
export DATAVOYAGER_MODEL="your-model-name"
export DATAVOYAGER_API_KEY="your-api-key"
```

然后运行上面的需求。默认最多采集 20 个页面，可用 `--max-pages` 调整。这条流程无需另装浏览器、Node.js 或 DataFlow。使用 Responses API 时，增加 `DATAVOYAGER_API_FORMAT=responses`。

## 看得到进度和消耗

```text
自然语言需求 → 寻找资料 → 提取和筛选 → 生成问答 → 导出 JSONL
```

终端每两秒更新一次：当前在做什么、已采集多少页面、筛选后保留多少资料、生成多少条 QA、调用模型多少次、消耗多少输入和输出 token。采集和生成可以同时进行。

进度显示示意，数字仅用于演示：

```text
[42s] generating QA | pages 8 | accepted sources 5 | QA 3 | API calls 14 (0 failed, 1 active) | tokens 18200 in / 2100 out (partial; some usage unavailable or pending)
```

也可以从另一个终端查看最新进度：

```bash
datavoyager status --run data/python-qa.jsonl.run
```

## 拿走数据

| 文件 | 内容 |
|---|---|
| `data/python-qa.jsonl` | 问答数据，字段为 `instruction`、`input`、`output` |
| `data/python-qa.jsonl.sources.jsonl` | 每条导出问答的来源链接和元信息 |
| `data/python-qa.jsonl.run/report.json` | 最终条数、耗时和模型 API 用量 |

流程会筛选资料、检查问答结构，并去除完全相同的问答对。训练前仍需抽检生成答案。Token 用量采用接口返回值，缺失时会明确标注；金额不做推算。详见[用量与质量说明](docs/quickstart.md#what-the-numbers-mean)。

---

[高级采集与 DataFlow 流水线](docs/runtime.md) · [路线图](docs/roadmap.md) · [来源与许可](THIRD_PARTY_NOTICES.md)

当前为开发预览，尚未指定项目级开源许可证。
