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

def is_visible(element):
    return element.is_displayed()

@given('the webpage is loaded')
def step_given_webpage_is_loaded(context):
    context.driver = webdriver.Chrome()
    context.driver.get(f"file://index.html")
    time.sleep(1)

@given('the product items with data-testid "product-item-1" and "product-item-2" are visible')
def step_given_product_items_visible(context):
    product_item_1 = WebDriverWait(context.driver, 10).until(
        EC.visibility_of_element_located((By.CSS_SELECTOR, "[data-testid='product-item-1']"))
    )
    product_item_2 = WebDriverWait(context.driver, 10).until(
        EC.visibility_of_element_located((By.CSS_SELECTOR, "[data-testid='product-item-2']"))
    )
    assert is_visible(product_item_1), "Product item 1 is not visible"
    assert is_visible(product_item_2), "Product item 2 is not visible"
    time.sleep(1)

@when('the user drags the product item with data-testid "product-item-1"')
def step_when_drag_product_item_1(context):
    product_item_1 = context.driver.find_element(By.CSS_SELECTOR, "[data-testid='product-item-1']")
    action = webdriver.ActionChains(context.driver)
    action.click_and_hold(product_item_1).perform()
    time.sleep(1)

@when('drops it into the drop area with data-testid "drop-area"')
def step_when_drop_into_drop_area(context):
    drop_area = context.driver.find_element(By.CSS_SELECTOR, "[data-testid='drop-area']")
    action = webdriver.ActionChains(context.driver)
    action.move_to_element(drop_area).release().perform()
    time.sleep(1)

@when('the user drags the product item with data-testid "product-item-2"')
def step_when_drag_product_item_2(context):
    product_item_2 = context.driver.find_element(By.CSS_SELECTOR, "[data-testid='product-item-2']")
    action = webdriver.ActionChains(context.driver)
    action.click_and_hold(product_item_2).perform()
    time.sleep(1)

@when('the user drags the product with data-testid "{product_item}" and drops it into the cart container with data-testid "{cart_id}"')
def step_when_drag_and_drop_product(context, product_item, cart_id):
    product = context.driver.find_element(By.CSS_SELECTOR, f"[data-testid='{product_item}']")
    cart = context.driver.find_element(By.CSS_SELECTOR, f"[data-testid='{cart_id}']")
    drag_and_drop_html5(context.driver, product, cart)
    time.sleep(1)

@then('the cart display should show a product with title "The Essence of JavaScript"')
def step_then_cart_display_title_1(context):
    cart_items = context.driver.find_elements(By.CSS_SELECTOR, "#div1 .box2")
    titles = [item.text for item in cart_items]
    assert "The Essence of JavaScript" in titles, "Title 'The Essence of JavaScript' not found in cart"
    time.sleep(1)

@then('the price "$40"')
def step_then_cart_display_price_1(context):
    cart_prices = context.driver.find_elements(By.CSS_SELECTOR, "#div1 .box3")
    prices = [item.text for item in cart_prices]
    assert "$40" in prices or "$40.00", "Price '$40' not found in cart"
    time.sleep(1)

@then('the quantity "1"')
def step_then_cart_display_quantity_1(context):
    cart_quantities = context.driver.find_elements(By.CSS_SELECTOR, "#div1 .box1")
    quantities = [item.text for item in cart_quantities]
    assert "1" in quantities, "Quantity '1' not found in cart"
    time.sleep(1)

@then('the cart display should show a product with title "JavaScript: The Definitive Guide"')
def step_then_cart_display_title_2(context):
    cart_items = context.driver.find_elements(By.CSS_SELECTOR, "#div1 .box2")
    titles = [item.text for item in cart_items]
    assert "JavaScript: The Definitive Guide" in titles, "Title 'JavaScript: The Definitive Guide' not found in cart"
    time.sleep(1)

@then('the price "$120"')
def step_then_cart_display_price_2(context):
    cart_prices = context.driver.find_elements(By.CSS_SELECTOR, "#div1 .box3")
    prices = [item.text for item in cart_prices]
    assert "$120" in prices or "$120.00", "Price '$120' not found in cart"
    time.sleep(1)

@then('the quantity "1" for the second product')
def step_then_cart_display_quantity_2(context):
    cart_quantities = context.driver.find_elements(By.CSS_SELECTOR, "#div1 .box1")
    quantities = [item.text for item in cart_quantities]
    assert quantities.count("1") == 2, "Quantity '1' not found twice in cart"
    time.sleep(1)

def after_scenario(context, scenario):
    context.driver.quit()