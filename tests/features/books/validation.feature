Feature: Book details validation
  Lengths count Unicode code points, including spaces and combining marks.
  Titles allow 750, authors 450, and descriptions 15000 code points.
  A title and a recognized language code are required.

  Scenario Outline: Maximum lengths preserve Unicode text
    Given I am adding a book with valid required details
    And I have entered the <field> containing <count> copies of "<character>"
    When I save the book
    Then the book is added to my library
    And its details match what I entered
    Examples: Representative Unicode text at maximum lengths
      | field       | count | character |
      | title       | 750   | ಕ         |
      | title       | 150   | ಕನ್ನಡ     |
      | author      | 450   | ಕ         |
      | description | 15000 | ಕ         |
      | title       | 750   | 📚        |

  Scenario Outline: Over-limit details are rejected
    Given I am adding a book with valid required details
    And I have entered the <field> containing <count> copies of "ಕ"
    When I save the book
    Then I am told that the <field> is invalid
    And my library remains unchanged
    Scenarios: All length-limited details at their first rejected length
      | field       | count |
      | title       | 751   |
      | author      | 451   |
      | description | 15001 |

  Scenario Outline: Required details must be supplied
    Given I am adding a book with valid required details
    But I have not entered the <field>
    When I save the book
    Then I am told that the <field> is required
    And my library remains unchanged
    Scenarios: All required details
      | field    |
      | title    |
      | language |

  Scenario Outline: Required details cannot be cleared
    Given my library contains "Original"
    And I am editing the book "Original"
    And I have cleared the <field>
    When I save my changes
    Then I am told that the <field> is invalid
    And the book remains unchanged
    Scenarios: All required details
      | field    |
      | title    |
      | language |

  Scenario Outline: Blank titles are rejected
    Given I am adding a book with valid required details
    And I have left the title <entry>
    When I save the book
    Then I am told that the title is invalid
    And my library remains unchanged
    Examples: Representative blank titles
      | entry                   |
      | empty                   |
      | spaces only             |
      | Unicode whitespace only |

  Scenario Outline: Recognized language codes retain their spelling
    Given I am adding a new book
    And I have entered the title "Languages"
    And I have selected the language code "<language>"
    When I save the book
    Then the book is added to my library
    And the book has the language "<language>"
    Examples: Representative supported language codes
      | language   |
      | en         |
      | kn         |
      | kn-IN      |
      | EN-gb      |
      | fr         |
      | zh-Hant-TW |
      | en_US      |

  Scenario Outline: Unrecognized language codes are rejected
    Given I am adding a new book
    And I have entered the title "Languages"
    And I have selected the language code "<language>"
    When I save the book
    Then I am told that the language is invalid
    And my library remains unchanged
    Examples: Representative unrecognized language codes
      | language       |
      |                |
      | English        |
      | zz             |
      | en-INVALID     |
      | en--US         |
      | en-1234        |
      | de-1901-1901   |
      | en-a-foo-a-bar |
      | en-ಕನ್ನಡ       |
