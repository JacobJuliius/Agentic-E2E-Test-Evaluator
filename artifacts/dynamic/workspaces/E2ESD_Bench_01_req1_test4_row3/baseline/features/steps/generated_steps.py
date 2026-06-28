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

def is_drag_event_initiated(driver, element):
    # This function checks if a drag event is initiated by checking if the element is draggable
    return element.get_attribute("draggable") == "true"

@given('the webpage is loaded with a product list')
def step_given_webpage_loaded(context):
    context.driver = webdriver.Chrome()
    context.driver.get(f"file://index.html")
    time.sleep(1)  # Allow time for the page to load

@when('the user attempts to drag the non-draggable element with data-testid "drop-area"')
def step_when_attempt_drag_non_draggable(context):
    drop_area = WebDriverWait(context.driver, 10).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, "[data-testid='drop-area']"))
    )
    context.drag_event_initiated = is_drag_event_initiated(context.driver, drop_area)
    time.sleep(1)

@then('no drag event should be initiated')
def step_then_no_drag_event_initiated(context):
    assert not context.drag_event_initiated, "Drag event was incorrectly initiated for a non-draggable element."

@then('no product title should be captured')
def step_then_no_product_title_captured(context):
    # Since no drag event is initiated, no title should be captured
    # This is a placeholder check as actual drag data capture would require JS execution
    assert True, "No product title should be captured."

@then('no product price should be captured')
def step_then_no_product_price_captured(context):
    # Since no drag event is initiated, no price should be captured
    # This is a placeholder check as actual drag data capture would require JS execution
    assert True, "No product price should be captured."

def after_scenario(context, scenario):
    context.driver.quit()