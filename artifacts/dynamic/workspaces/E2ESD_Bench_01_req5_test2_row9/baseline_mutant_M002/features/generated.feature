Feature: Calculate and display the total price of products in the cart
  The system should calculate and display the total price of all products in the cart, updating the total each time a product is added.


  Scenario: [Normal] Add multiple different products to the cart and verify total price
    Given the webpage is loaded with a list of products
    When the user drags the product with data-testid "product-item-1" to the drop area with data-testid "drop-area"
    And the user drags the product with data-testid "product-item-2" to the drop area with data-testid "drop-area"
    Then the total price displayed in the element with id "allMoney" should be "$160.00"
