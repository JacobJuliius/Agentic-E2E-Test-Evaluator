"""
test_single.py
Description: A single-case mock execution script to test the V2 Agentic Workflow.
"""

from graph import app
import json

def run_single_test():
    print("🚀 [Init] Preparing mock test data for V2 Workflow...")
    
    # 1. 模拟业务需求 (Mock Requirements)
    mock_reqs = """
    1. Navigate to the login page at 'http://example.com/login'.
    2. Enter username 'admin'.
    3. Enter password 'password'.
    4. Click the login button.
    5. Verify that the 'Dashboard' heading is visible.
    """
    
    # 2. 模拟包含瑕疵的可执行代码 (Mock Flawed Code)
    mock_code = """
from playwright.sync_api import sync_playwright
import time

def test_login():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto("http://example.com/login")
        page.fill("#user", "admin")
        page.fill("#pass", "password")
        page.click("#login-btn")
        
        time.sleep(5)  
        
        page.click("#upgrade-account")  
        
        browser.close()
"""

    # 3. 构建初始状态 (注意：必须初始化 revision_count 为 0)
    initial_state = {
        "fine_grained_reqs": mock_reqs.strip(),
        "executable_test_code": mock_code.strip(),
        "revision_count": 0
    }

    print("\n🧠 [Execution] Invoking LangGraph Multi-Agent Workflow... (This may take 15-30 seconds)\n")
    
    try:
        # 4. 调用状态图
        final_state = app.invoke(initial_state)
        
        # 5. 打印评估结果，重点展示 V2 升级的核心字段
        print("="*60)
        print("🎯 AGENTIC JURY EVALUATION RESULTS")
        print("="*60)
        
        print(f"✅ Syntax Gatekeeper : {'PASSED' if final_state.get('syntax_passed') else 'FAILED'}")
        print(f"🔄 Reflection Loops  : {final_state.get('revision_count')} (How many times Critic fired)")
        print(f"⚔️ Conflict Detected : {final_state.get('has_conflict')}")
        
        if final_state.get('has_conflict') and final_state.get('critic_feedback'):
            print(f"\n🗣️ [Critic Feedback History]:\n{final_state.get('critic_feedback')}")
        
        print(f"\n📊 Consensus Score   : {final_state.get('overall_score')}/100")
        print(f"📝 Reasoning         : {final_state.get('final_reasoning')}")
        
        print("\n" + "="*60)
        print("🛠️ REFINER AGENT OUTPUT (SELF-HEALING)")
        print("="*60)
        
        print("📑 [Improvement Report]:")
        print(final_state.get('improvement_report', 'No report generated.'))
        
        print("\n💻 [Fixed Code]:")
        print(final_state.get('fixed_code', 'No code fixed.'))
        print("="*60)

    except Exception as e:
        print(f"\n❌ [Error] Graph execution failed: {str(e)}")

if __name__ == "__main__":
    run_single_test()