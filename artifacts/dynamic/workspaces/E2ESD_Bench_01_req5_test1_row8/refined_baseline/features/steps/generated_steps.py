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

@given('the webpage is loaded with a list of products')
def step_given_webpage_loaded(context):
    context.driver = webdriver.Chrome()
    context.driver.get(f"file://index.html")

@when('the user drags the product with data-testid "product-item-1" to the drop area with data-testid "drop-area"')
def step_when_drag_product_to_drop_area(context):
    driver = context.driver
    product = WebDriverWait(driver, 10).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, "[data-testid='product-item-1']"))
    )
    drop_area = WebDriverWait(driver, 10).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, "[data-testid='drop-area']"))
    )

    driver.execute_script("""
        function simulateDragAndDrop(source, target) {
            const dataTransfer = new DataTransfer();
            const dragStart = new DragEvent('dragstart', { dataTransfer, bubbles: true });
            const drop = new DragEvent('drop', { dataTransfer, bubbles: true });
            const dragEnd = new DragEvent('dragend', { dataTransfer, bubbles: true });
            source.dispatchEvent(dragStart);
            target.dispatchEvent(drop);
            source.dispatchEvent(dragEnd);
        }
        simulateDragAndDrop(arguments[0], arguments[1]);
    """, product, drop_area)

@then('the total price displayed in the element with id "allMoney" should be "$40.00"')
def step_then_verify_total_price(context):
    try:
        driver = context.driver
        total_price_element = WebDriverWait(driver, 10).until(
            EC.visibility_of_element_located((By.ID, "allMoney"))
        )
        expected_price = "$40.00"
        actual_price = total_price_element.text.strip()
        assert expected_price in actual_price, f"Expected total price '{expected_price}', but got '{actual_price}'"
    finally:
        context.driver.quit()