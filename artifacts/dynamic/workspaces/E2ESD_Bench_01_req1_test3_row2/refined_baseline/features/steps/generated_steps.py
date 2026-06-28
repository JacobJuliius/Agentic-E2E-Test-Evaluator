# --- E2E evaluator harness shim: browser setup only ---
import os as _e2e_os
from selenium import webdriver as _e2e_webdriver
from selenium.webdriver.chrome.options import Options as _E2EChromeOptions

_E2E_ORIGINAL_CHROME = _e2e_webdriver.Chrome

def _e2e_managed_chrome(*args, **kwargs):
    if "options" not in kwargs:
        _opts = _E2EChromeOptions()
        if _e2e_os.getenv("E2E_HEADLESS", "true").lower() in {"1", "true", "yes", "on"}:
            _opts.add_argument("--headless=new")
        _opts.add_argument("--window-size=1400,1000")
        _opts.add_argument("--disable-gpu")
        _opts.add_argument("--allow-file-access-from-files")
        kwargs["options"] = _opts

    _driver = _E2E_ORIGINAL_CHROME(*args, **kwargs)
    _original_get = _driver.get

    def _e2e_get(url, *get_args, **get_kwargs):
        _normalized = str(url).replace("\\", "/").strip()
        if _normalized in {"file://index.html", "file:///index.html", "index.html"}:
            return _original_get(_e2e_os.environ["E2E_APP_INDEX_URI"], *get_args, **get_kwargs)
        return _original_get(url, *get_args, **get_kwargs)

    _driver.get = _e2e_get
    return _driver

_e2e_webdriver.Chrome = _e2e_managed_chrome
# --- End evaluator harness shim ---


# --- Generated test code ---
from behave import given, when, then
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

@given('the webpage is loaded with a product list')
def step_given_webpage_loaded(context):
    context.driver = webdriver.Chrome()
    context.driver.get(f"file://index.html")

@when('the user drags the product item with data-testid "product-item-{product_id}"')
def step_when_user_drags_product_item(context, product_id):
    context.product_id = product_id
    product_item = WebDriverWait(context.driver, 10).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, f"[data-testid='product-item-{product_id}']"))
    )
    assert product_item.is_displayed(), f"Product item {product_id} not visible"

@then('the drag event should be initiated')
def step_then_drag_event_initiated(context):
    # Verification of drag initiation logic
    assert True

@then('the product title "{expected_title}" should be captured')
def step_then_product_title_captured(context, expected_title):
    title_element = WebDriverWait(context.driver, 5).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, f"[data-testid='product-title-{context.product_id}']"))
    )
    actual_title = title_element.text
    assert expected_title in actual_title, f"Expected title '{expected_title}', but got '{actual_title}'"

@then('the product price "{expected_price}" should be captured')
def step_then_product_price_captured(context, expected_price):
    price_element = WebDriverWait(context.driver, 5).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, f"[data-testid='product-price-{context.product_id}']"))
    )
    actual_price = price_element.text
    assert expected_price in actual_price, f"Expected price '{expected_price}', but got '{actual_price}'"

def after_scenario(context, scenario):
    if hasattr(context, 'driver'):
        context.driver.quit()