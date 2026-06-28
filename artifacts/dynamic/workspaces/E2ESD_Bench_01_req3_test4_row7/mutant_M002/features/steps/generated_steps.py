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

def is_visible(element):
    return element.is_displayed()

@given('the webpage is loaded')
def step_given_webpage_loaded(context):
    context.driver = webdriver.Chrome()
    context.driver.get(f"file://index.html")
    time.sleep(1)

@given('the product item with data-testid "{data_testid}" is visible')
def step_given_product_item_visible(context, data_testid):
    product_item = WebDriverWait(context.driver, 10).until(
        EC.visibility_of_element_located((By.CSS_SELECTOR, f"[data-testid='{data_testid}']"))
    )
    assert is_visible(product_item), f"Product item with data-testid '{data_testid}' is not visible"
    time.sleep(1)

@when('the user drags the product item with data-testid "{data_testid}" and drops it into the drop area with data-testid "drop-area"')
def step_when_drag_and_drop_product(context, data_testid):
    product_item = context.driver.find_element(By.CSS_SELECTOR, f"[data-testid='{data_testid}']")
    drop_area = context.driver.find_element(By.CSS_SELECTOR, "[data-testid='drop-area']")

    # Simulate drag and drop using JavaScript
    context.driver.execute_script("""
        function createEvent(typeOfEvent) {
            var event = document.createEvent("CustomEvent");
            event.initCustomEvent(typeOfEvent, true, true, null);
            event.dataTransfer = {
                data: {},
                setData: function(key, value) {
                    this.data[key] = value;
                },
                getData: function(key) {
                    return this.data[key];
                }
            };
            return event;
        }

        function dispatchEvent(element, event, transferData) {
            if (transferData !== undefined) {
                event.dataTransfer = transferData;
            }
            if (element.dispatchEvent) {
                element.dispatchEvent(event);
            } else if (element.fireEvent) {
                element.fireEvent("on" + event.type, event);
            }
        }

        function simulateHTML5DragAndDrop(element, target) {
            var dragStartEvent = createEvent('dragstart');
            dispatchEvent(element, dragStartEvent);
            var dropEvent = createEvent('drop');
            dispatchEvent(target, dropEvent, dragStartEvent.dataTransfer);
            var dragEndEvent = createEvent('dragend');
            dispatchEvent(element, dragEndEvent, dropEvent.dataTransfer);
        }

        var source = arguments[0];
        var target = arguments[1];
        simulateHTML5DragAndDrop(source, target);
    """, product_item, drop_area)
    time.sleep(1)

@then('the cart display should show a product with title "{expected_title}"')
def step_then_cart_display_title(context, expected_title):
    cart_items = context.driver.find_elements(By.CSS_SELECTOR, "#div1 .box2")
    titles = [item.text for item in cart_items]
    assert any(expected_title in title for title in titles), f"Expected title '{expected_title}' not found in cart display"
    time.sleep(1)

@then('the price "{expected_price}"')
def step_then_cart_display_price(context, expected_price):
    cart_prices = context.driver.find_elements(By.CSS_SELECTOR, "#div1 .box3")
    prices = [price.text for price in cart_prices]
    assert any(expected_price in price for price in prices), f"Expected price '{expected_price}' not found in cart display"
    time.sleep(1)

@then('the unit price "{expected_price}"')
def step_then_cart_display_price(context, expected_price):
    cart_prices = context.driver.find_elements(By.CSS_SELECTOR, "#div1 .box3")
    prices = [price.text.strip() for price in cart_prices]

    def normalize(price_str):
        """Remove currency symbol and convert to float rounded to 2 decimals"""
        return round(float(price_str.replace("$", "").strip()), 2)

    expected_value = normalize(expected_price)
    normalized_prices = [normalize(p) for p in prices]

    assert expected_value in normalized_prices, f"Expected unit price '{expected_price}' not found in cart display"
    time.sleep(1)

@then('the quantity "1"')
def step_then_cart_display_shows_quantity(context):
    cart_quantities = context.driver.find_elements(By.CSS_SELECTOR, "#div1 .box1")
    quantities = [quantity.text for quantity in cart_quantities]
    assert "1" in quantities, "Product quantity not found in cart display"
    time.sleep(1)

def after_scenario(context, scenario):
    context.driver.quit()