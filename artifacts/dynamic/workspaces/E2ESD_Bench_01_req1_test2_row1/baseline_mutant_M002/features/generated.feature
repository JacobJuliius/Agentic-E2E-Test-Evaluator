Feature: Drag and Drop Product Item
  The system should initiate a drag event when a user drags a product item from the product list, capturing the product's title and price for transfer.


  Scenario: [Edge] Dragging a product item with the highest price
    Given the webpage is loaded with a product list
    When the user drags the product item with data-testid "product-item-2"
    Then the drag event should be initiated
    And the product title "JavaScript: The Definitive Guide" should be captured
    And the product price "$120" should be captured
