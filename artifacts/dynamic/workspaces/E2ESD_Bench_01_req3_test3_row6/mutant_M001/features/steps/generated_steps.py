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

def drag_and_drop_html5(driver, source, target):
    driver.execute_script("""
        function triggerDragAndDrop(source, target) {
            const dataTransfer = new DataTransfer();
            const dragStartEvent = new DragEvent('dragstart', { dataTransfer });
            source.dispatchEvent(dragStartEvent);

            const dragEnterEvent = new DragEvent('dragenter', { dataTransfer });
            target.dispatchEvent(dragEnterEvent);

            const dragOverEvent = new DragEvent('dragover', { dataTransfer });
            target.dispatchEvent(dragOverEvent);

            const dropEvent = new DragEvent('drop', { dataTransfer });
            target.dispatchEvent(dropEvent);

            const dragEndEvent = new DragEvent('dragend', { dataTransfer });
            source.dispatchEvent(dragEndEvent);
        }
        triggerDragAndDrop(arguments[0], arguments[1]);
    """, source, target)

@given('the webpage is loaded')
def step_given_webpage_loaded(context):
    context.driver = webdriver.Chrome()
    context.driver.get(f"file://index.html")
    time.sleep(1)

@given('the product item with data-testid "product-item-3" is visible')
def step_given_product_item_visible(context):
    product_item = WebDriverWait(context.driver, 10).until(
        EC.visibility_of_element_located((By.CSS_SELECTOR, "[data-testid='product-item-3']"))
    )
    assert is_visible(product_item), "Product item is not visible"
    time.sleep(1)

@when('the user drags the product item with data-testid "{product_id}" and drops it into the drop area with data-testid "{cart_id}"')
def step_when_user_drags_and_drops_product(context, product_id, cart_id):
    product_item = context.driver.find_element(By.CSS_SELECTOR, f"[data-testid='{product_id}']")
    drop_area = context.driver.find_element(By.CSS_SELECTOR, f"[data-testid='{cart_id}']")
    drag_and_drop_html5(context.driver, product_item, drop_area)
    time.sleep(1)

@then('the cart display should show a product with title "Mastering JavaScript"')
def step_then_cart_display_shows_product_title(context):
    cart_items = context.driver.find_elements(By.CSS_SELECTOR, "#div1 .box2")
    titles = [item.text for item in cart_items]
    assert "Mastering JavaScript" in titles, "Product title not found in cart display"
    time.sleep(1)

@then('the price "$35"')
def step_then_cart_display_shows_price(context):
    cart_prices = context.driver.find_elements(By.CSS_SELECTOR, "#div1 .box3")
    prices = [price.text for price in cart_prices]
    assert "$35" in prices or "$35.00" in prices, "Product price not found in cart display"
    time.sleep(1)

@then('the quantity "2"')
def step_then_cart_display_shows_quantity(context):
    cart_quantities = context.driver.find_elements(By.CSS_SELECTOR, "#div1 .box1")
    quantities = [quantity.text for quantity in cart_quantities]
    assert "2" in quantities, "Product quantity not found in cart display"
    time.sleep(1)

def after_scenario(context, scenario):
    context.driver.quit()