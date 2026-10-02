# DataVoyager

**从 Hugging Face 发现并准备领域训练数据。**

当前主流程是 [`codex-skills/domain-training-data`](codex-skills/domain-training-data)
中的 `domain-training-data` Codex Skill。它由 Codex 对话承担多轮交互：检索真实
HF 目录、阅读数据卡、检查 config/split/字段和小样本，用户确认来源后才下载、合并、
清洗或转为 SFT/QA。找数据和确定性下载不需要 LLM API Key。

本地网页工作台与 QA 生成器保留为实验性的旧路径，不再是 Skill 的主 agent。

[English](README.md) · [使用说明](docs/quickstart.md) · [架构](docs/architecture.md)

![DataVoyager chat workspace](docs/assets/chat-workspace.png)

## 用 Codex Skill 工作

将目录安装到本机 Codex Skills（本机部署时已完成这一步）：

```powershell
Copy-Item .\codex-skills\domain-training-data "$env:USERPROFILE\.codex\skills\domain-training-data" -Recurse
```

然后可以直接对 Codex 说：

```text
找英文机器学习基础资料。先只展示候选：每个候选的数据卡、许可、config/split、字段、
样本和推荐理由；不要下载，也不要生成 QA。
```

选定来源后继续在同一个对话里决定是否合并和怎样清洗。务必说清数量口径：
“下载 10,000 行来源数据”“得到 10,000 条去重后记录”和“得到 10,000 条有效 QA”
不是一回事。对 HF Dataset Server 支持的数据集，可用随附脚本明确检查 config/split，
不依赖本机可选的 `datasets` 运行时：

```powershell
python .\codex-skills\domain-training-data\scripts\hf_dataset.py inspect owner/dataset
python .\codex-skills\domain-training-data\scripts\hf_dataset.py rows owner/dataset --config CONFIG --split SPLIT --limit 3
```

上线前按[功能与质量验收用例](docs/skill-test-cases.md)测试新领域的数据准备流程。

## 旧版网页工作台

```text
你：为 Python 初学者做一份中文生成器问答，先给我看一小批。
你：多一些容易误解的地方，少一些定义题。
你：这版可以，下载下来。
```

网页里可以多轮补充需求、查看样例，再生成新版本。它保留给旧的一体式 QA pipeline 实验；来源选择、config/split 决策和数据准备应优先使用上面的 Skill。

安装下面的 Python 项目后，再准备聊天主 agent（Node.js 22+、Corepack）：

```bash
cd codex-runner
corepack yarn install --immutable
corepack yarn build
cd ..
datavoyager chat
```

打开 **http://127.0.0.1:8765**，填写 API 地址、模型名和 Key，即可开始。聊天主 agent 使用 **Codex SDK**，需要支持 Responses API 的模型端点。详细说明见[聊天工作台](docs/chat.md)。

## 从命令行开始

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

然后运行上面的需求。默认最多下载 20 条数据集样本，可用 `--max-source-rows` 调整。这条流程无需另装浏览器、Node.js 或 DataFlow。使用 Responses API 时，增加 `DATAVOYAGER_API_FORMAT=responses`。

## 看得到进度和消耗

```text
自然语言需求 → 寻找资料 → 提取和筛选 → 生成问答 → 导出 JSONL
```

终端每两秒更新一次：当前阶段、已下载多少条来源样本、筛选后保留多少资料、生成多少条 QA、调用模型多少次、消耗多少输入和输出 token。采集和生成可以同时进行。

进度显示示意，数字仅用于演示：

```text
[42s] generating QA | source rows 80 | accepted sources 5 | QA 3 | API calls 14 (0 failed, 1 active) | tokens 18200 in / 2100 out (partial; some usage unavailable or pending)
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

从输入框的阶段选择器，用户可以先只搜索数据集目录，再逐个查看候选的来源、许可、说明、字段结构和限制，并勾选最多五个数据集。金融和医疗使用[经过数据卡与实际样本核对的 HF 清单](docs/reviewed-datasets.md)，不是直接展示任意关键词结果。确认来源后，可选择只下载、合并去重、清洗，或者运行完整 QA 流程。清洗阶段会提取可用正文并按主题筛选证据；完整流程会生成带引用的 QA 候选，再单独审核来源支撑情况。只有审核通过并去重的 QA 计入目标，模型审核不等于专家认证。明确要求许可信息时，系统会过滤掉许可未知和非商业许可标记；目录标签仍需使用者自行核验。原始数据、合并数据、清洗语料、筛选记录和 QA 候选都可独立下载，见[阶段数据与证据流程](docs/evidence-pipeline.md)。Token 用量采用接口返回值，缺失时会明确标注；金额不做推算。详见[用量与质量说明](docs/quickstart.md#what-the-numbers-mean)。

---

[高级采集与 DataFlow 流水线](docs/runtime.md) · [路线图](docs/roadmap.md) · [来源与许可](THIRD_PARTY_NOTICES.md)

当前为开发预览，尚未指定项目级开源许可证。
