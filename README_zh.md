# DataVoyager

### 从自然语言需求到可追溯的领域数据集

**描述你要的数据集，让 Agent 去发现、采集和加工。**

[English](README.md) · [系统架构](docs/architecture.md) · [运行要求与限制](docs/runtime.md) · [路线图](docs/roadmap.md)

DataVoyager 把自然语言需求、现成数据集搜索、网页采集与数据加工连接起来。
目标是让用户描述领域、数据形式和质量要求，系统构建数据集，并保留来源、加工过程和验收证据。

> 当前版本为 **`0.1.0a1` 开发者预览**。已有采集与加工实现、离线示例和回归测试；
> 尚未完成这一版本的在线端到端基准验证。默认 QA 校验检查格式，不能保证答案事实正确。

## 工作流程

```text
自然语言需求 / badcase 报告
           ↓
      获取任务 Agent
       ┌───┴────┐
  现成数据集搜索   WebAgent 搜索与网页抓取
       └───┬────┘
      DataMixer 数据仓库
           ↓
  DataFlow + 自定义算子 + 模型调用
           ↓
    领域数据集、报告与来源记录
```

- **WebAgent** 负责搜索、查看页面、提取链接和选定资源入口；爬虫负责后续 HTML 抓取。
- **DataMixer** 负责仓库、元数据、加工编排、任务队列与 lineage。
- **DataFlow** 提供部分清洗、过滤和生成算子；其余步骤由本项目算子和模型调用完成。

网页管线中，L1 是原始 HTML，L2 是处理后文本，L3 是初始 SFT 数据。
支持边采边处理，不必等全部网页抓取完成。

## 已有功能

| 功能 | 当前边界 |
|---|---|
| 自然语言入口 | 保存完整需求并交给现有获取 worker；尚无独立、强约束的 Dataset Spec 编译器 |
| 双路发现 | 搜索 Hugging Face 等现成数据集，同时由 worker 编排网页 campaign |
| 领域采集 | 子查询扩展、候选评估、有限步数探索与 HTML 抓取 |
| 数据加工 | 正文提取、主题过滤及 QA/代码/Text-to-SQL 示例管线 |
| 可追溯执行 | 来源元数据、失败记录、任务恢复、质量报告和 lineage |
| badcase 输入 | 提取领域和失败类型，并向 worker 传递完整原始报告 |

采集结果可能包含多个数据集；需要最终训练文件时，可以继续使用 DataMixer 的 recipe/export 命令。
本项目当前不承诺任意一句自然语言都能生成符合任意 schema 的最终训练文件。

## 先离线体验

Python 3.10+，从源码安装：

```bash
git clone https://github.com/haolpku/DataVoyager.git
cd DataVoyager
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'

datavoyager build "构建面向初学者的 Python 类型错误修复数据集" \
  --domain code --focus "type error repair" --target-datasets 2 --dry-run

python examples/offline_demo.py --warehouse runs/offline-demo
python -m pytest -q
```

`--dry-run` 不访问网络、不写文件。离线示例用两篇自编 HTML 运行真实的正文提取、L1/L2 存储和 lineage；
它不模拟联网采集，也不生成假冒的模型输出。

品牌及新入口为 DataVoyager / `datavoyager`，Python 包名仍为 `dataflowwebagent`，兼容已有调用。

## 使用模型进行采集

外层 worker 使用 Codex SDK，可执行 shell 命令，当前请求 `danger-full-access`。
请在专用执行环境中配置所需凭据；指定 `--run` 目录不等于进行了执行隔离。

```bash
python -m pip install -e '.[search,browser,dataflow]'
playwright install chromium

# 需要 Node.js 和 Corepack；Node 发行版未附带 Corepack 时需另行安装。
cd codex-runner
corepack yarn install --immutable
cd ..

export DATAFLOWWEBAGENT_MODEL="your-model-name"
export DATAFLOWWEBAGENT_BASE_URL="https://your-provider.example/v1"
export DATAFLOWWEBAGENT_API_KEY="your-key"

datavoyager build "收集 Python 类型错误修复样例，包含修复代码与解释" \
  --domain code --focus "type error repair" --target-datasets 2 \
  --warehouse ./runs/warehouse --run ./runs/python-repair
```

`--target-datasets` 是源数据集数，不是样本条数。每次构建使用新的 `--run` 目录。
模型提供方需要支持 runner 使用的 Responses API；现成数据集搜索助手还使用 chat 模型接口。
完整依赖、服务配置与状态查询见 [English quick start](README.md) 和 [运行说明](docs/runtime.md)。

已有 badcase 文件也可以直接使用：

```bash
datavoyager badcase --badcase configs/badcase.example.yaml \
  --warehouse ./runs/warehouse --run ./runs/badcase-repair --dry-run
```

## 后续重点

把需求解析为可执行的数据规格，加入领域事实/执行验证，统一采集费用与数量预算，
用相同预算对比基线的相关性、有效产出与单位样本成本。完整计划见 [路线图](docs/roadmap.md)。

本项目与论文 [Data-driven Discovery with Large Generative Models](https://arxiv.org/abs/2402.13610)
中的同名数据分析原型是独立项目。

当前导入包没有项目级许可证，因此尚未为整个项目指定开源许可证；公开开源前需由代码所有者确定。
原有第三方材料的说明保留在 [来源与许可说明](THIRD_PARTY_NOTICES.md) 中。
