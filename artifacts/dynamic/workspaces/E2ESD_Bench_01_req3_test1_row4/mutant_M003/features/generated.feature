Feature: Add product to cart display when dropped into the drop area
  The system should add the product to the cart display, showing the product's title, price, and quantity when a product item is dropped into the drop area.


  Scenario: [Normal] Add a single product to the cart
    Given the webpage is loaded
    And the product item with data-testid "product-item-1" is visible
    When the user drags the product with data-testid "product-item-1" and drops it into the cart container with data-testid "drop-area"
    Then the cart display should show a product with title "The Essence of JavaScript"
    And the price "$40"
    And the quantity "1"
