Feature: Book details lifecycle
  Saved book details can be viewed and edited without changing their display text.

  Scenario: Save and retrieve Kannada book details exactly as entered
    Given I am adding a new book
    And I have entered the title "  ಕನ್ನಡ ಪುಸ್ತಕ  "
    And I have selected the language code "kn"
    And I have entered the author "ಲೇಖಕ"
    And I have entered the description
      """
      ಮೊದಲ ಸಾಲು
      ಎರಡನೇ ಸಾಲು
      """
    When I save the book
    Then the book is added to my library
    And the book has its own identity and creation time
    When I view the book
    Then its details match what I entered

  Scenario: Author and description are optional
    Given I am adding a new book
    And I have entered the title "A book"
    And I have selected the language code "en"
    When I save the book
    Then the book is added to my library
    And the book has no author
    And the book has no description

  Scenario: Clear optional details while changing the language
    Given my library contains "Original" by "Author" with description "Text"
    And I am editing the book "Original"
    And I have selected the language code "kn-IN"
    And I have cleared the author
    And I have cleared the description
    When I save my changes
    Then my changes are saved
    And the book has the title "Original"
    And the book has the language "kn-IN"
    And the book has no author
    And the book has no description
    And creation time is preserved and update time advances

  Scenario: Change the title while leaving other details untouched
    Given my library contains "Original" by "Author" with description "Text"
    And I am editing the book "Original"
    And I have entered the title "Changed"
    When I save my changes
    Then my changes are saved
    And the book has the title "Changed"
    And the book has the author "Author"
    And the book has the description "Text"
    And the book has the language "en"

  Scenario: Save without making changes
    Given my library contains "Original"
    And I am editing the book "Original"
    When I save my changes
    Then my changes are saved
    And the book remains unchanged

  Scenario: Re-entering the same details does not change timestamps
    Given my library contains "Original"
    And I am editing the book "Original"
    And I have entered the title "Original"
    When I save my changes
    Then my changes are saved
    And the book remains unchanged

  Scenario: Remove a book from the library
    Given my library contains "Delete me"
    And I have selected the book "Delete me"
    When I delete the book
    Then the book is removed from my library
    When I view the book
    Then I am told that the book cannot be found
    When I delete the book
    Then I am told that the book cannot be found

  Scenario Outline: A book that is no longer in the library cannot be used
    Given the selected book is no longer in my library
    When I try to <action> the book
    Then I am told that the book cannot be found
    Scenarios: All operations on an existing book
      | action |
      | view   |
      | edit   |
      | delete |
