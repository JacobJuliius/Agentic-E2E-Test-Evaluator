### Multi-agent Langchain pipeline

1. Syntax \& Linter Agent: 负责检查基础语法错误和缺失的 Playwright 依赖。

2. Requirement Alignment Agent: 只负责拆解原子需求，并输出 covered, partial, missing 的列表。

3. Assertion Quality Agent: 专门扫描代码中的 expect() 语句，判断是强断言（验证业务结果）还是弱断言（如仅验证页面未崩溃）。

4. Hallucination \& Smell Agent: 识别不属于需求范围的业务操作（如自动忽略 page.wait\_for\_load\_state() 等合理辅助代码）。

5. Consensus Agent (核心): 收集上述所有 JSON 数据，执行数学公式，计算出最终的业务覆盖率（Requirement Coverage）和整体健康度得分（Overall Score）。

