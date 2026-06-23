import json
from dotenv import load_dotenv

# 确保加载 .env 文件中的 API Key
load_dotenv()

from agents import (
    syntax_linter_agent,
    requirement_alignment_agent,
    assertion_quality_agent,
    hallucination_smell_agent,
    consensus_agent
)

def run_single_test():
    # 1. 伪造一个极简的输入状态 (Mock State)
    
    mock_state = {
        "executable_test_code": "def test_login():\n    username='admin'\n    assert True",
        "fine_grained_reqs": "User should be able to login with valid credentials."
    }

    print("🚀 开始独立测试各个 Agent 节点...\n")

    # 2. 测试语法守门人
    print("--- 1. Syntax & Linter Agent ---")
    syntax_res = syntax_linter_agent(mock_state)
    mock_state.update(syntax_res) # 模拟 LangGraph 的行为：把当前输出合并回全局状态字典
    print(json.dumps(syntax_res, indent=2))

    # 3. 测试需求对齐大模型
    print("\n--- 2. Requirement Alignment Agent ---")
    req_res = requirement_alignment_agent(mock_state)
    mock_state.update(req_res)
    print(json.dumps(req_res, indent=2))

    # 4. 测试断言质量大模型
    print("\n--- 3. Assertion Quality Agent ---")
    ast_res = assertion_quality_agent(mock_state)
    mock_state.update(ast_res)
    print(json.dumps(ast_res, indent=2))

    # 5. 测试异味与幻觉大模型
    print("\n--- 4. Hallucination & Smell Agent ---")
    smell_res = hallucination_smell_agent(mock_state)
    mock_state.update(smell_res)
    print(json.dumps(smell_res, indent=2))

    # 6. 测试最终打分机器
    print("\n--- 5. Consensus Agent (最终汇总计算) ---")
    final_res = consensus_agent(mock_state)
    print(json.dumps(final_res, indent=2, ensure_ascii=False))

if __name__ == "__main__":
    run_single_test()