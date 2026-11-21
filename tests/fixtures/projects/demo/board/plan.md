# 整体方案

用 **小模型** 验证预热的作用。参考 [论文](https://example.org/paper) 和 [危险链接](javascript:alert(1))。

<script>alert("xss in plan")</script>

| 版本 | 变化 |
|---|---|
| v1 | 基线 |
| v2 | 加预热 |

```python
lr = base_lr * min(1, step / warmup)  # <b>not bold</b>
```
