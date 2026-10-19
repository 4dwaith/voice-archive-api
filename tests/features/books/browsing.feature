Feature: Browse the book library
  Books appear in the order they were added. Pages default to 50 books,
  with at most 100 books per page.

  Scenario: Empty library
    When I browse my library
    Then I see 0 books out of 0, skipping 0 with a page size of 50

  Scenario: Default page includes the books in addition order
    Given 3 distinct books exist
    When I browse my library
    Then I see 3 books out of 3, skipping 0 with a page size of 50

  Scenario Outline: Pages retain addition order and the full library count
    Given 3 distinct books exist
    When I browse my library with a page size of <size> after skipping <skip> books
    Then I see <count> books out of 3, skipping <skip> with a page size of <size>
    Examples: Representative page positions and sizes
      | size | skip | count |
      | 2    | 0    | 2     |
      | 2    | 2    | 1     |
      | 100  | 9    | 0     |

  Scenario Outline: Invalid page bounds are rejected
    When I browse my library with a page size of <size> after skipping <skip> books
    Then I am told that the page bounds are invalid
    Examples: Representative invalid page bounds
      | size | skip |
      | 0    | 0    |
      | 101  | 0    |
      | -1   | 0    |
      | 50   | -1   |
