# `dataflowwebagent.common`

`dataflowwebagent.common` 是公共工具集合目录。

## 导入约定

推荐直接从子模块导入，不要依赖 `dataflowwebagent.common.__init__` 转发符号。

推荐写法：

```python
from dataflowwebagent.common.prompts import PromptLoader
from dataflowwebagent.common.exception import emit_success, emit_error, ErrorCode
from dataflowwebagent.common.db_tool import sqlite_db_session
from dataflowwebagent.common.event_tool import StreamEvent, get_event_writer
```

## 为什么这样设计

- 避免 `import dataflowwebagent.common` 时连带加载可选依赖
- 避免因为某个子模块缺依赖，导致整个 `common` 包无法导入
- 每个工具的依赖边界更清晰，调试时更容易定位问题

## 可用子模块

- `dataflowwebagent.common.prompts`: Prompt 加载工具
- `dataflowwebagent.common.exception`: 统一 success/error JSON 返回
- `dataflowwebagent.common.db_tool`: SQLite 与配置表读写
- `dataflowwebagent.common.event_tool`: 按 `context_id/agent_name.pkl` 写 pickle 事件数组
- `dataflowwebagent.common.i18n`: 国际化工具

## 子模块文档

- [db_tool/README.md](db_tool/README.md)
- [exception/README.md](exception/README.md)
- [event_tool/README.md](event_tool/README.md)
