Feature: Add product to cart display when dropped into the drop area
  The system should add the product to the cart display, showing the product's title, price, and quantity when a product item is dropped into the drop area.


Scenario: [Normal] Add the same product multiple times to the cart
    Given the webpage is loaded
    And the product item with data-testid "product-item-3" is visible
    When the user drags the product item with data-testid "product-item-3" and drops it into the drop area with data-testid "drop-area"
    And the user drags the product item with data-testid "product-item-3" and drops it into the drop area with data-testid "drop-area"
    Then the cart display should show a product with title "Mastering JavaScript"
    And the price "$35"
    And the quantity "2"