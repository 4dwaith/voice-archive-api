Feature: Duplicate book detection
  Equivalent Unicode text, letter case, and whitespace do not distinguish books.
  Language does not distinguish books with the same title and author.

  Scenario Outline: Equivalent titles and authors are duplicates
    Given my library contains "Café Book" by "Some Author"
    And I am adding a new book
    And I have entered the title <title>
    And I have entered the author <author>
    And I have selected the language code "kn"
    When I save the book
    Then I am told that this book is already in my library
    And my library remains unchanged
    Examples: Representative equivalent titles and authors
      | title             | author            |
      | "Café Book"       | "Some Author"     |
      | "  CAFÉ  BOOK  "  | " some  AUTHOR "  |
      | "Café Book"      | "Some Author"     |

  Scenario Outline: Missing and blank authors share the same identity
    Given my library contains "Anonymous"
    And I am adding a new book
    And I have entered the title "Anonymous"
    And I have selected the language code "en"
    And I have left the author <entry>
    When I save the book
    Then I am told that this book is already in my library
    And my library remains unchanged
    Examples: Representative absent and blank author entries
      | entry       |
      | unentered   |
      | cleared     |
      | empty       |
      | spaces only |

  Scenario: Tabs, line breaks and Unicode spaces do not distinguish books
    Given my library contains "A Book"
    And I am adding a new book
    And I have entered the title "A Book" with surrounding Unicode spaces and internal tabs and line breaks
    And I have left the author as tabs and line breaks
    And I have selected the language code "en"
    When I save the book
    Then I am told that this book is already in my library
    And my library remains unchanged

  Scenario: Same title by different authors is allowed
    Given my library contains "Shared" by "First"
    And I am adding a new book
    And I have entered the title "Shared"
    And I have entered the author "Second"
    And I have selected the language code "en"
    When I save the book
    Then the book is added to my library
    And the book has the author "Second"

  Scenario: Editing a book cannot take another book's identity
    Given my library contains "First"
    And my library contains "Second"
    And I am editing the book "Second"
    And I have entered the title " FIRST "
    And I have entered the description "Must not be saved"
    When I save my changes
    Then I am told that this book is already in my library
    And the book remains unchanged
    And my library remains unchanged
