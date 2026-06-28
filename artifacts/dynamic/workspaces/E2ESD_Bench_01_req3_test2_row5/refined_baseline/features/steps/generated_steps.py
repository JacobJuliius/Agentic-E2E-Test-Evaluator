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

def drag_and_drop_html5(driver, source, target):
    driver.execute_script("""
        function triggerDragAndDrop(source, target) {
            const dataTransfer = new DataTransfer();
            ['dragstart', 'dragenter', 'dragover', 'drop', 'dragend'].forEach(type => {
                const event = new DragEvent(type, { dataTransfer, bubbles: true });
                (type === 'drop' || type === 'dragover' || type === 'dragenter') ? target.dispatchEvent(event) : source.dispatchEvent(event);
            });
        }
        triggerDragAndDrop(arguments[0], arguments[1]);
    """, source, target)

@given('the webpage is loaded')
def step_given_webpage_is_loaded(context):
    context.driver = webdriver.Chrome()
    context.driver.get(f"file://index.html")

@given('the product items with data-testid "product-item-1" and "product-item-2" are visible')
def step_given_product_items_visible(context):
    wait = WebDriverWait(context.driver, 10)
    assert wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, "[data-testid='product-item-1']")))
    assert wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, "[data-testid='product-item-2']")))

@when('the user drags the product with data-testid "{product_item}" and drops it into the cart container with data-testid "{cart_id}"')
def step_when_drag_and_drop_product(context, product_item, cart_id):
    product = context.driver.find_element(By.CSS_SELECTOR, f"[data-testid='{product_item}']")
    cart = context.driver.find_element(By.CSS_SELECTOR, f"[data-testid='{cart_id}']")
    drag_and_drop_html5(context.driver, product, cart)

@then('the cart display should show a product with title "{title}"')
def step_then_cart_display_title(context, title):
    titles = [item.text for item in context.driver.find_elements(By.CSS_SELECTOR, "#div1 .box2")]
    assert title in titles, f"Title '{title}' not found in cart"

@then('the price "{price}"')
def step_then_cart_display_price(context, price):
    prices = [item.text for item in context.driver.find_elements(By.CSS_SELECTOR, "#div1 .box3")]
    # Validate that the specific price exists in the list of cart prices
    assert price in prices or f"{price}.00" in prices, f"Price '{price}' not found in cart"

@then('the quantity "{quantity}"')
def step_then_cart_display_quantity(context, quantity):
    quantities = [item.text for item in context.driver.find_elements(By.CSS_SELECTOR, "#div1 .box1")]
    assert quantity in quantities, f"Quantity '{quantity}' not found in cart"

@then('the quantity "1" for the second product')
def step_then_cart_display_quantity_2(context):
    quantities = [item.text for item in context.driver.find_elements(By.CSS_SELECTOR, "#div1 .box1")]
    assert quantities.count("1") >= 2, "Quantity '1' not found twice in cart"

def after_scenario(context, scenario):
    context.driver.quit()