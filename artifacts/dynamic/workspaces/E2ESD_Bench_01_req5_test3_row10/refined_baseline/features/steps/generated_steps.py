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
import os

@given('the webpage is loaded with a list of products')
def step_given_webpage_loaded(context):
    context.driver = webdriver.Chrome()
    file_url = f"file://{os.path.abspath('index.html')}"
    context.driver.get(file_url)

@when('the user drags the product with data-testid "{product_testid}" to the drop area with data-testid "{drop_area_testid}"')
def step_when_drag_product_to_drop_area(context, product_testid, drop_area_testid):
    product = WebDriverWait(context.driver, 10).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, f"[data-testid='{product_testid}']"))
    )
    drop_area = WebDriverWait(context.driver, 10).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, f"[data-testid='{drop_area_testid}']"))
    )
    
    # Simulate drag and drop using JS
    context.driver.execute_script("""
        var product = arguments[0];
        var dropArea = arguments[1];
        var dataTransfer = new DataTransfer();
        product.dispatchEvent(new DragEvent('dragstart', {dataTransfer: dataTransfer}));
        dropArea.dispatchEvent(new DragEvent('drop', {dataTransfer: dataTransfer}));
    """, product, drop_area)

@then('the total price displayed in the element with id "{total_price_id}" should be "{expected_price}"')
def step_then_verify_total_price(context, total_price_id, expected_price):
    total_price_element = WebDriverWait(context.driver, 10).until(
        EC.visibility_of_element_located((By.ID, total_price_id))
    )
    # Wait for the text to match the expected price to handle potential async updates
    WebDriverWait(context.driver, 5).until(
        lambda d: expected_price in total_price_element.text.strip()
    )
    actual_price = total_price_element.text.strip()
    assert expected_price in actual_price, f"Expected price '{expected_price}', but got '{actual_price}'"

def after_scenario(context, scenario):
    if hasattr(context, 'driver'):
        context.driver.quit()