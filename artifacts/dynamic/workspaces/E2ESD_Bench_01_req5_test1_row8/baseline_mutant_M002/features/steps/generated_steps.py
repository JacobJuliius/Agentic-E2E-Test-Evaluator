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

@given('the webpage is loaded with a list of products')
def step_given_webpage_loaded(context):
    context.driver = webdriver.Chrome()
    context.driver.get(f"file://index.html")
    time.sleep(1)  # Allow time for the page to load

@when('the user drags the product with data-testid "product-item-1" to the drop area with data-testid "drop-area"')
def step_when_drag_product_to_drop_area(context):
    driver = context.driver
    product = WebDriverWait(driver, 10).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, "[data-testid='product-item-1']"))
    )
    drop_area = WebDriverWait(driver, 10).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, "[data-testid='drop-area']"))
    )

    # Perform drag and drop using JavaScript
    driver.execute_script("""
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
    """, product, drop_area)
    time.sleep(1)  # Allow time for the drop action to complete

@then('the total price displayed in the element with id "allMoney" should be "$40.00"')
def step_then_verify_total_price(context):
    driver = context.driver
    total_price_element = WebDriverWait(driver, 10).until(
        EC.presence_of_element_located((By.ID, "allMoney"))
    )
    expected_price = "$40.00"
    actual_price = total_price_element.text.strip()
    assert expected_price in actual_price, f"Expected total price '{expected_price}', but got '{actual_price}'"

    # Close the browser after the test
    context.driver.quit()