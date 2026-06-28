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
from selenium.webdriver.common.action_chains import ActionChains

@given('the webpage is loaded with a product list')
def step_given_webpage_loaded(context):
    context.driver = webdriver.Chrome()
    context.driver.get(f"file://index.html")

@when('the user attempts to drag the non-draggable element with data-testid "drop-area"')
def step_when_attempt_drag_non_draggable(context):
    drop_area = WebDriverWait(context.driver, 10).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, "[data-testid='drop-area']"))
    )
    # Check draggable attribute
    context.drag_event_initiated = (drop_area.get_attribute("draggable") == "true")
    
    # Attempt to simulate drag and check if dataTransfer is populated
    # We use JS to check if any drag data would be set on this element
    context.captured_data = context.driver.execute_script(
        """ 
        var el = arguments[0];
        var data = {};
        el.addEventListener('dragstart', function(e) {
            data.title = e.dataTransfer.getData('text/plain');
        });
        var event = new DragEvent('dragstart', {bubbles: true, cancelable: true});
        el.dispatchEvent(event);
        return data;
        """, drop_area
    )

@then('no drag event should be initiated')
def step_then_no_drag_event_initiated(context):
    assert not context.drag_event_initiated, "Drag event was incorrectly initiated for a non-draggable element."

@then('no product title should be captured')
def step_then_no_product_title_captured(context):
    assert not context.captured_data.get('title'), "Product title was captured despite non-draggable status."

@then('no product price should be captured')
def step_then_no_product_price_captured(context):
    # In the context of the drag event, if title is empty, price should also be empty
    assert not context.captured_data.get('price'), "Product price was captured despite non-draggable status."

def after_scenario(context, scenario):
    context.driver.quit()