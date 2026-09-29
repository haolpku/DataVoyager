# 金融／医疗 QA 事实与来源复核

现有数据中可以找到有据的候选，但不足以直接宣称“高质量可训练”。主要问题包括超出来源的扩写、关键限定丢失、标准语境缺失和来源间冲突；并非所有未通过项都是事实错误。检查结果支持增加事实验证环节，不能用更换模型或补齐条数代替它。

## 自动审核结果

下表是模型辅助的严格来源审核，保留原始模型判定，未经专家校准。未通过可能只是证据不足、语气或边界差异；不应计算为真实错误率。

| 样本组 | 审核条数 | 来源审核通过 | 比例 |
| --- | ---: | ---: | ---: |
| 金融端到端 | 96 | 50 | 52.1% |
| 医疗端到端 | 23 | 6 | 26.1% |
| 金融固定资料 | 12 | 8 | 66.7% |
| 医疗固定资料 | 12 | 5 | 41.7% |

合计 69/143 条通过。72 条至少包含一个资料不足项，3 条被标为与原文冲突，两者有 1 条重叠；全部请求完成，支持／冲突引文均通过文本匹配检查。三条冲突分别是区域名称翻译、SEC 评论公开例外、邮箱用途条件；这不是对其余条目事实正确性的保证。

评审存在明确边界问题：将 WHO 研发活动概括为加速研发可能被判得过严；高血压危险因素列表仅因“超过65岁”与“65岁以上”之差未通过；另有一条因未复述就医提醒而未通过，混入了完整性判断。原始判定未静默改写，另存二次审查备注。固定资料结果也不能与上一轮不同量表下的综合通过率直接比较。

共完成 12 组定向一手来源核查，涉及 16 条不同 QA，仅对明确列出的主张负责，不代表这 16 条每个句子均获外部验证。生成复审清单 76 条，其中 `medical-fixed:12` 标记为隔离；其余需要复核不等于已证伪。

## 核查范围和口径

复核日期：2026-09-29。检查的是生成基线 `eda3b41` 留下的输出，没有重新生成样本。后续数量补齐及确认机制提交 `6816f37` 不属于本轮事实质量的对照实验。

- 端到端输出 119 条：金融 96 条、医疗 23 条；医疗 100 条任务仅有 9 条中断恢复样本。
- 固定资料对照 24 条：每个领域 12 条，同一组 4 份资料各生成三种格式。
- 共 143 条，按完整 QA、来源 URL、输入正文去重后有 141 个审核请求。不同批次复用页面与主题，这不是 143 个独立抽样的领域测试。
- 第三方接口提供的 `gpt-6-astra` 对实际送入生成器的前 12,000 字符逐项检查；不以常识补齐证据，不因偏题、格式或数量而扣分。模型名称来自服务商返回的接口配置，未独立验证其底层实现。
- 每个答案拆成若干事实主张，分别标为有据、资料不足、与原文冲突。判为有据还要求支持引文能在原文中匹配。引用能匹配只证明引文存在，不证明评审的推理必然正确。
- Codex 另做定向外部核查，选择数字、阈值、限定词、疑似错误及引用缺口，查阅当前一手页面。这不是随机抽样的真实准确率估计，也不是临床／金融专家给出的金标。

**以下来源支撑率不等于事实正确率，也不等于可训练样本合格率。** 上一轮 7.1% 等结果包含主题、格式、证据等多个维度，不能解释成只有 7.1% 的医学事实正确。

## 重点核查结果

### 有引用仍需隔离：高血压危象条件

`medical-fixed:12` 将危象阈值写为收缩压 >180 **且**舒张压 >120。[MedlinePlus 原表](https://medlineplus.gov/highbloodpressure.html)也使用“and”，因此不是生成器凭空把连接词改错；但 [AHA 2026-05-28 的说明](https://newsroom.heart.org/news/the-lowdown-on-high-blood-pressure-what-you-need-to-know)使用“and/or”。该条应隔离，补充适用标准并经临床审查，不能仅因忠实引用而直接放行。

`medical-fixed:2/10/12` 还混用了 WHO 与美国页面中的不同分类／诊断语境。[WHO](https://www.who.int/news-room/fact-sheets/detail/hypertension)与上述 MedlinePlus 页各自有来源，直接组成不注明标准的通用问答会造成回答不一致。问题或答案应写明机构、适用人群和参考时间。

### 限定词被削弱：糖尿病风险和预防

`medical-fixed:9` 把 WHO 对妊娠糖尿病妇女子女风险的可能性表述改成确定结论，并将尚不清楚 1 型糖尿病预防手段写成绝对无法预防。后一说法可在其他医学科普页面找到近似表述，不能简单算作已证伪，但不忠实于这条指定的 [WHO 来源](https://www.who.int/news-room/fact-sheets/detail/diabetes)。

`medical-fixed:11` 合并生活方式预防／延缓 2 型和妊娠糖尿病时，没有保留 [MedlinePlus](https://medlineplus.gov/diabetes.html)的可能性限定；“糖尿病 mellitus”也是应修正的术语表达。应恢复原文语气，避免读成效果保证。

### 无来源支持的扩写：金融法律保护

`finance-1000:19` 添加了“只有注册或持牌的专业人士才受到相关法律和监管保护”。[Investor.gov 的原文](https://www.investor.gov/introduction-investing/getting-started/working-investment-professional)强调核查注册状态和未持牌投资风险，并不支持这个排他性法律结论。该句应删除或另行提供明确法律依据，不能把风险提示扩大为法律保护规则。

`finance-100:30` 按配偶小十岁以上概括 RMD 特殊计算提示。原 [Investor.gov 页面](https://www.investor.gov/financial-tools-calculators/calculators/required-minimum-distribution-calculator)本身只是提示读者查阅 IRS；[IRS FAQ](https://www.irs.gov/retirement-plans/retirement-plan-and-ira-required-minimum-distributions-faqs)使用相应表格还要求配偶是唯一受益人。用于实际计算的问答必须补齐条件；不能把概要页面的提示直接当作完整计算规则，也不能将此误归为生成器删掉了原文已有条件。

### 确认的翻译错误

`medical-100:3` 把 South-East Asia 区域写成“南亚”，应为“东南亚”。[WHO 官方区域页](https://www.who.int/southeastasia)可核实。这是机构名翻译错误，与疾病知识或原需求的主题相关性是不同问题。

### 核实为有依据的例子

- `finance-100:11`、`finance-1000:28` 的本金、增长率、年限、三档费用和期末金额，与 [SEC 的费用示例](https://www.investor.gov/introduction-investing/general-resources/news-alerts/alerts-bulletins/investor-bulletins/updated)一致；保留“假设”和“约”，不能把示例误判为收益承诺。
- `medical-100:8`、`medical-1000:6` 两条相同的高血压危险因素回答，主要内容可以由 [WHO 清单](https://www.who.int/news-room/fact-sheets/detail/hypertension)支持；严格翻译应把“65岁以上”改成“超过65岁”。这并不代表其余偏题的 WHO 机构介绍也满足原始需求。
- `finance-1000:31` 的熊市核心定义既存在于生成器收到的总词典正文，也能在 [Investor.gov 专门词条](https://www.investor.gov/introduction-investing/investing-basics/glossary/bear-market)核实。不能仅因 URL 是目录形式，就断言输入没有正文证据。
- `finance-100:3`、`finance-1000:36` 的资产配置、分散投资核心定义能在[同机构专题页](https://www.investor.gov/introduction-investing/getting-started/asset-allocation)找到支持；原引用的目录页不足，附加效果判断仍需核查。
- `medical-100:9` 的部分 WHO 职责可在其 [About 页面](https://www.who.int/about)核实。这里只核对核心主张，没有给整条答案所有职责背书；它仍偏离高血压／糖尿病选题。

## 对产品的影响

现有输出包含事实有据的可用候选，但不能把“来自权威域名”或“能找到一段引用”作为训练集放行条件。需要依次检查具体主题、正文是否足够、每个事实的证据、关键限定和标准冲突。质量未过关的条目不应计入用户要求的合格 QA 数量；数量不足时继续补采集或触发已有的确认机制。

本轮只做验证与复审清单，没有改写原始数据、自动放行高风险样本或实现新的事实质量门禁。也没有进行领域专家审查、下游微调或临床效果验证。

## 用量与证据文件

本次新审核耗时 825.9 秒，完成 141 次模型请求，无失败和未知用量。输入 915,894 tokens、输出 98,255 tokens，合计 1,014,149 tokens；第三方价格未知，不推算金额。上述不含网页访问，也不包含上一轮生成消耗。

本地证据目录为 `runs/factual-review-20260929/`（被 Git 忽略）：

- `items.jsonl`：143 条 QA 与生成输入快照，原始 QA 文件未覆盖。
- `rubric.txt`、`review.py`：本轮审核量表与执行代码；脚本运行会产生 API 消耗，连接通过不回显输入传入，不保存在文件。
- `unique-judgments.jsonl`、`judgments.jsonl`：141 个唯一结果及映射回 143 行的原始判定。
- `external-checks.json`、`secondary-review.json`：一手来源交叉核查及评审边界说明。
- `review-queue.jsonl`：待复核条目、问题与外部来源；隔离标记仅在此审查清单中，不代表已修改产品导出流程。
- `summary.json`、`combined-summary.json`、`api_calls.jsonl`：汇总与请求用量。

报告不附完整采集网页或长篇原文，引文与原始数据保留在本地审核文件中。复现模型判定会有非确定性；应保留每轮原始记录，不据此声称领域准确率或训练收益。
