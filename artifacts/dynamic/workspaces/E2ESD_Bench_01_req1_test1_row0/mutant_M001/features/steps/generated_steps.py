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
import time

file_path = "index.html"

@given('the webpage is loaded with a product list')
def step_given_webpage_loaded(context):
    context.driver = webdriver.Chrome()
    context.driver.get(f"file://index.html")
    time.sleep(1)  # Allow time for the page to load

@when('the user drags the product item with data-testid "product-item-1"')
def step_when_user_drags_product_item(context):
    product_item = WebDriverWait(context.driver, 10).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, "[data-testid='product-item-1']"))
    )
    context.driver.execute_script("arguments[0].scrollIntoView();", product_item)
    time.sleep(1)  # Allow time for the element to be interactable

    # Simulate drag and drop using JavaScript
    context.driver.execute_script("""
        const dataTransfer = new DataTransfer();
        const dragStartEvent = new DragEvent('dragstart', { dataTransfer });
        arguments[0].dispatchEvent(dragStartEvent);
    """, product_item)
    time.sleep(1)

@then('the drag event should be initiated')
def step_then_drag_event_initiated(context):
    # Verify drag event initiation by checking if dataTransfer has data
    product_item = context.driver.find_element(By.CSS_SELECTOR, "[data-testid='product-item-1']")
    drag_data = context.driver.execute_script("""
        const dataTransfer = new DataTransfer();
        const dragStartEvent = new DragEvent('dragstart', { dataTransfer });
        arguments[0].dispatchEvent(dragStartEvent);
        return dataTransfer.types.includes('title') && dataTransfer.types.includes('money');
    """, product_item)
    assert drag_data, "Drag event was not initiated properly."

@then('the product title "The Essence of JavaScript" should be captured')
def step_then_product_title_captured(context):
    product_title = context.driver.execute_script("""
        const dataTransfer = new DataTransfer();
        const dragStartEvent = new DragEvent('dragstart', { dataTransfer });
        arguments[0].dispatchEvent(dragStartEvent);
        return dataTransfer.getData('title');
    """, context.driver.find_element(By.CSS_SELECTOR, "[data-testid='product-item-1']"))
    assert product_title == "The Essence of JavaScript", f"Expected title 'The Essence of JavaScript', but got '{product_title}'"

@then('the product price "$40" should be captured')
def step_then_product_price_captured(context):
    product_price = context.driver.execute_script("""
        const dataTransfer = new DataTransfer();
        const dragStartEvent = new DragEvent('dragstart', { dataTransfer });
        arguments[0].dispatchEvent(dragStartEvent);
        return dataTransfer.getData('money');
    """, context.driver.find_element(By.CSS_SELECTOR, "[data-testid='product-item-1']"))
    assert product_price == "$40", f"Expected price '$40', but got '{product_price}'"

def after_scenario(context, scenario):
    context.driver.quit()