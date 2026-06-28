Feature: Drag and Drop Product Item
  The system should initiate a drag event when a user drags a product item from the product list, capturing the product's title and price for transfer.


  Scenario: [Error] Attempting to drag a non-draggable element
    Given the webpage is loaded with a product list
    When the user attempts to drag the non-draggable element with data-testid "drop-area"
    Then no drag event should be initiated
    And no product title should be captured
    And no product price should be captured