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

def get_cart_row_by_title(driver, title):
    rows = driver.find_elements(By.CSS_SELECTOR, "#div1 .cart-item")
    for row in rows:
        if title in row.find_element(By.CLASS_NAME, "box2").text:
            return row
    return None

@given('the webpage is loaded')
def step_given_webpage_loaded(context):
    context.driver = webdriver.Chrome()
    context.driver.get(f"file://index.html")

@given('the product item with data-testid "{data_testid}" is visible')
def step_given_product_item_visible(context, data_testid):
    WebDriverWait(context.driver, 10).until(
        EC.visibility_of_element_located((By.CSS_SELECTOR, f"[data-testid='{data_testid}']"))
    )

@when('the user drags the product item with data-testid "{data_testid}" and drops it into the drop area with data-testid "drop-area"')
def step_when_drag_and_drop_product(context, data_testid):
    product_item = context.driver.find_element(By.CSS_SELECTOR, f"[data-testid='{data_testid}']")
    drop_area = context.driver.find_element(By.CSS_SELECTOR, "[data-testid='drop-area']")
    context.driver.execute_script("""
        var source = arguments[0]; var target = arguments[1];
        function createEvent(type) {
            var e = document.createEvent("CustomEvent");
            e.initCustomEvent(type, true, true, null);
            e.dataTransfer = { data: {}, setData: function(k, v) { this.data[k] = v; }, getData: function(k) { return this.data[k]; } };
            return e;
        }
        var start = createEvent('dragstart'); source.dispatchEvent(start);
        var drop = createEvent('drop'); drop.dataTransfer = start.dataTransfer; target.dispatchEvent(drop);
    """, product_item, drop_area)

@then('the cart display should show a product with title "{expected_title}"')
def step_then_cart_display_title(context, expected_title):
    row = WebDriverWait(context.driver, 5).until(lambda d: get_cart_row_by_title(d, expected_title))
    assert row is not None, f"Product '{expected_title}' not found in cart"

@then('the price "{expected_price}"')
@then('the unit price "{expected_price}"')
def step_then_cart_display_price(context, expected_price):
    # Context is inferred from the last title checked or current scenario state
    # Assuming the title was checked previously, we look for the row again
    row = get_cart_row_by_title(context.driver, context.table.rows[0][0] if hasattr(context, 'table') else "")
    # Fallback to finding any row if title context is missing
    if not row: row = context.driver.find_element(By.CSS_SELECTOR, "#div1")
    price_text = row.find_element(By.CLASS_NAME, "box3").text
    assert expected_price in price_text, f"Expected {expected_price}, found {price_text}"

@then('the quantity "1"')
def step_then_cart_display_shows_quantity(context):
    row = context.driver.find_element(By.CSS_SELECTOR, "#div1")
    assert "1" in row.find_element(By.CLASS_NAME, "box1").text

def after_scenario(context, scenario):
    context.driver.quit()