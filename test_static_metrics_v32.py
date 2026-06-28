import ast
from pathlib import Path

# Load only the static extractor section without instantiating the LLM client.
source = Path('agents.py').read_text(encoding='utf-8')
start = source.index('def extract_static_metrics(code: str) -> dict:')
end = source.index('\n\n# ==========================================\n# Agent 1:', start)
namespace = {'ast': ast}
exec(source[start:end], namespace)
extract_static_metrics = namespace['extract_static_metrics']


def test_native_python_assert_is_counted():
    metrics = extract_static_metrics("assert actual_price == '$40'")
    assert metrics['assertions_count'] == 1
    assert metrics['assertion_methods'] == ['python_assert']


def test_data_testid_is_stable_not_brittle():
    code = "driver.find_element(By.CSS_SELECTOR, \"[data-testid='product-item-1']\")"
    metrics = extract_static_metrics(code)
    assert metrics['stable_selectors'] == ["[data-testid='product-item-1']"]
    assert metrics['brittle_selectors'] == []


def test_xpath_and_positional_css_are_brittle():
    code = '''
driver.find_element(By.XPATH, "//div[3]/button")
driver.find_element(By.CSS_SELECTOR, "li:nth-child(2)")
'''
    metrics = extract_static_metrics(code)
    assert set(metrics['brittle_selectors']) == {'//div[3]/button', 'li:nth-child(2)'}


def test_css_class_is_medium_risk_not_automatically_brittle():
    code = "driver.find_element(By.CSS_SELECTOR, '.checkout-button')"
    metrics = extract_static_metrics(code)
    assert metrics['medium_risk_selectors'] == ['.checkout-button']
    assert metrics['brittle_selectors'] == []


def test_coverage_score_is_capped_in_source():
    assert 'req_coverage_score = max(0.0, min(100.0, raw_req_coverage_score))' in source
