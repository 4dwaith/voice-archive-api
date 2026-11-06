Feature: Manage ordered book series
  Books in a series have consecutive positions starting at one.
  I can append or remove the last book individually, or replace the complete
  reading order without changing the books themselves.

  Background:
    Given series "Adventures" contains "Beginning, Journey, Return" in that order
    And series "Collected Essays" contains "Essays" in that order
    And my library contains the standalone book "New Tale"

  Scenario: Append a standalone book to a series
    Given I am editing book "New Tale"
    And I have selected series "Adventures" for the book
    When I save the book's changes
    Then my book changes are saved
    And series "Adventures" contains "Beginning, Journey, Return, New Tale" in that order

  Scenario: Editing a description preserves series membership and position
    Given I am editing book "Journey"
    And I have changed the book's description to "A revised description"
    When I save the book's changes
    Then my book changes are saved
    And the book's description is "A revised description"
    And series "Adventures" contains "Beginning, Journey, Return" in that order

  Scenario: Remove the last book from its series
    Given I am editing book "Return"
    And I have cleared the book's series
    When I save the book's changes
    Then my book changes are saved
    And "Return" is a standalone book
    And series "Adventures" contains "Beginning, Journey" in that order

  Scenario: Move the last book to the end of another series
    Given I am editing book "Return"
    And I have selected series "Collected Essays" for the book
    And I have entered series position 2
    When I save the book's changes
    Then my book changes are saved
    And series "Adventures" contains "Beginning, Journey" in that order
    And series "Collected Essays" contains "Essays, Return" in that order

  Scenario: Removing a middle book rejects all accompanying changes
    Given I am editing book "Journey"
    And I have cleared the book's series
    And I have changed the book's description to "This must not be saved"
    When I save the book's changes
    Then I am told that series positions must remain consecutive
    And my catalog remains unchanged

  Scenario: Moving a middle book cannot leave a gap in its original series
    Given I am editing book "Journey"
    And I have selected series "Collected Essays" for the book
    When I save the book's changes
    Then I am told that series positions must remain consecutive
    And my catalog remains unchanged

  Scenario: A book cannot take an occupied position
    Given I am editing book "Journey"
    And I have entered series position 1
    When I save the book's changes
    Then I am told that series positions must remain consecutive
    And my catalog remains unchanged

  Scenario: A series member cannot have its position cleared
    Given I am editing book "Journey"
    And I have cleared the book's series position
    When I save the book's changes
    Then I am told that series positions must remain consecutive
    And my catalog remains unchanged

  Scenario: Joining a series cannot skip a position
    Given I am editing book "New Tale"
    And I have selected series "Adventures" for the book
    And I have entered series position 5
    When I save the book's changes
    Then I am told that series positions must remain consecutive
    And my catalog remains unchanged

  Scenario: A standalone book cannot have a series position
    Given I am editing book "New Tale"
    And I have entered series position 1
    When I save the book's changes
    Then I am told that series positions must remain consecutive
    And my catalog remains unchanged

  Scenario: Deleting a middle book cannot leave a gap
    Given I am editing book "Journey"
    When I delete the book
    Then I am told that series positions must remain consecutive
    And my catalog remains unchanged

  Scenario: Delete the last book in a series
    Given I am editing book "Return"
    When I delete the book
    Then the book is removed from my library
    And series "Adventures" contains "Beginning, Journey" in that order

  Scenario: Replace the reading order and membership together
    When I replace the books in series "Adventures" with "Return, Beginning, New Tale"
    Then my series changes are saved
    And series "Adventures" contains "Return, Beginning, New Tale" in that order
    And "Journey" is a standalone book

  Scenario: Replacing a series cannot take a book from another series
    When I replace the books in series "Adventures" with "Return, Beginning, Essays"
    Then I am told that a series cannot contain missing, repeated or another series' books
    And my catalog remains unchanged

  Scenario: Replacing a series cannot repeat a book
    When I replace the books in series "Adventures" with "Return, Beginning, Beginning"
    Then I am told that a series cannot contain missing, repeated or another series' books
    And my catalog remains unchanged

  Scenario: A missing book rejects the entire replacement
    Given book "Lost Book" is no longer in my library
    When I replace the books in series "Adventures" with "Return, Lost Book"
    Then I am told that a series cannot contain missing, repeated or another series' books
    And my catalog remains unchanged

  Scenario: Remove every book from a series
    When I remove all books from series "Adventures"
    Then my series changes are saved
    And series "Adventures" has no books
    And "Beginning" is a standalone book
    And "Journey" is a standalone book
    And "Return" is a standalone book

  Scenario: Renaming a series updates its members' catalog details
    When I rename series "Adventures" to "Collected Adventures"
    Then the series is named "Collected Adventures"
    And its member books show the series name "Collected Adventures"

  Scenario: Browse series in the order they were added
    When I browse series with a page size of 1 after skipping 1 series
    Then I see series "Collected Essays" out of 2 series

  Scenario: Deleting a series preserves its books and reading material
    Given series "Adventures" also contains an imported book with media
    When I delete series "Adventures"
    Then the series is removed from my catalog
    And "Beginning" is a standalone book
    And the imported book and its media are preserved

  Scenario: A missing series rejects a book's membership change
    Given series "Forgotten" is no longer in my catalog
    And I am editing book "New Tale"
    And I have selected series "Forgotten" for the book
    When I save the book's changes
    Then I am told that the series cannot be found
    And my catalog remains unchanged

  Scenario Outline: A new series needs a nonblank title
    Given I am adding a series with its title <entry>
    When I save the new series
    Then I am told that the series title is invalid
    And my catalog remains unchanged

    Examples:
      | entry       |
      | cleared     |
      | empty       |
      | spaces only |

  Scenario: Adding a new book cannot skip a series position
    Given I am adding book "Next Adventure"
    And I have selected series "Adventures" for the book
    And I have entered series position 5
    When I save the new book
    Then I am told that series positions must remain consecutive
    And my catalog remains unchanged
