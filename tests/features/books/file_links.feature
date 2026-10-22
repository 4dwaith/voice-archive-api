Feature: Link prepared covers and PDFs through an explicit book save
  Files are prepared independently. A book may have either file, both, or neither.
  Each prepared file can be linked to at most one book.
  Book responses include delivery URLs for linked files; absent links have no URL.

  Background:
    Given a prepared cover named "Cover" exists
    And a prepared source PDF named "PDF" exists

  Scenario: Create a book with both prepared files
    Given I am adding a book with valid required details
    And I have selected prepared cover "Cover"
    And I have selected prepared source PDF "PDF"
    When I save the book
    Then the book is added to my library
    And the book links to prepared cover "Cover"
    And the book links to prepared source PDF "PDF"

  Scenario: Link files by saving an existing book
    Given my library contains "Existing"
    And I am editing the book "Existing"
    And I have selected prepared cover "Cover"
    And I have selected prepared source PDF "PDF"
    When I save my changes
    Then my changes are saved
    And the book links to prepared cover "Cover"
    And the book links to prepared source PDF "PDF"
    And creation time is preserved and update time advances

  Scenario Outline: Either file can be linked independently
    Given I am adding a book with valid required details
    And I have selected prepared <kind> "<file>"
    When I save the book
    Then the book is added to my library
    And the book links to prepared <kind> "<file>"
    And the book has no <other_kind> link

    Examples:
      | kind       | file  | other_kind |
      | cover      | Cover | source PDF |
      | source PDF | PDF   | cover      |

  Scenario: Omitting file IDs preserves existing links
    Given my library contains "Existing" linked to prepared cover "Cover" and source PDF "PDF"
    And I am editing the book "Existing"
    And I have entered the description "Updated details"
    When I save my changes
    Then my changes are saved
    And the book links to prepared cover "Cover"
    And the book links to prepared source PDF "PDF"

  Scenario Outline: Explicit null clears only the selected link
    Given my library contains "Existing" linked to prepared cover "Cover" and source PDF "PDF"
    And I am editing the book "Existing"
    And I have cleared the <kind> link
    When I save my changes
    Then my changes are saved
    And the book has no <kind> link
    And the book links to prepared <other_kind> "<other_file>"
    And creation time is preserved and update time advances
    And the original <kind> can no longer be retrieved

    Examples:
      | kind       | other_kind | other_file |
      | cover      | source PDF | PDF        |
      | source PDF | cover      | Cover      |

  Scenario: Saving unchanged links preserves timestamps
    Given my library contains "Existing" linked to prepared cover "Cover" and source PDF "PDF"
    And I am editing the book "Existing"
    And I have selected prepared cover "Cover"
    And I have selected prepared source PDF "PDF"
    When I save my changes
    Then my changes are saved
    And the book remains unchanged

  Scenario: Replace a link through an explicit save
    Given my library contains "Existing" linked to prepared cover "Cover" and source PDF "PDF"
    And a prepared cover named "Replacement" exists
    And I am editing the book "Existing"
    And I have selected prepared cover "Replacement"
    When I save my changes
    Then my changes are saved
    And the book links to prepared cover "Replacement"
    And the book links to prepared source PDF "PDF"
    And the original cover can no longer be retrieved

  Scenario: Deleting a book also removes access to its files
    Given my library contains "Existing" linked to prepared cover "Cover" and source PDF "PDF"
    And I am editing the book "Existing"
    When I delete the book
    Then the book is removed from my library
    And the original cover can no longer be retrieved
    And the original source PDF can no longer be retrieved

  Scenario Outline: Missing files reject creation
    Given I am adding a book with valid required details
    And I have selected a missing <kind>
    When I save the book
    Then I am told that the <kind> file cannot be found
    And my library remains unchanged

    Examples:
      | kind       |
      | cover      |
      | source PDF |

  Scenario Outline: A missing file rejects the entire save
    Given my library contains "Existing" linked to prepared cover "Cover" and source PDF "PDF"
    And I am editing the book "Existing"
    And I have entered the description "Do not save"
    And I have selected a missing <kind>
    When I save my changes
    Then I am told that the <kind> file cannot be found
    And the book remains unchanged

    Examples:
      | kind       |
      | cover      |
      | source PDF |

  Scenario Outline: A file already linked to another book rejects creation
    Given my library contains "Owner" linked to prepared cover "Cover" and source PDF "PDF"
    And I am adding a book with valid required details
    And I have selected prepared <kind> "<file>"
    When I save the book
    Then I am told that the <kind> file is already linked
    And my library remains unchanged

    Examples:
      | kind       | file  |
      | cover      | Cover |
      | source PDF | PDF   |

  Scenario Outline: A file already linked to another book rejects the entire save
    Given my library contains "Owner" linked to prepared cover "Cover" and source PDF "PDF"
    And a prepared cover named "Other cover" exists
    And a prepared source PDF named "Other PDF" exists
    And my library contains "Other" linked to prepared cover "Other cover" and source PDF "Other PDF"
    And I am editing the book "Other"
    And I have entered the description "Do not save"
    And I have selected prepared <kind> "<file>"
    When I save my changes
    Then I am told that the <kind> file is already linked
    And the book remains unchanged
    And my library remains unchanged

    Examples:
      | kind       | file  |
      | cover      | Cover |
      | source PDF | PDF   |


  Scenario: An imported book PDF must be removed through conditional content purge
    Given my library contains "Existing" linked to prepared cover "Cover" and source PDF "PDF"
    And I am editing the book "Existing"
    And the book has imported text content
    And I have cleared the source PDF link
    When I save my changes
    Then I am told that content purge is required
    And the book remains unchanged

  Scenario: An imported book PDF cannot be replaced through book save
    Given my library contains "Existing" linked to prepared cover "Cover" and source PDF "PDF"
    And a prepared source PDF named "Replacement" exists
    And I am editing the book "Existing"
    And the book has imported text content
    And I have selected prepared source PDF "Replacement"
    When I save my changes
    Then I am told that content purge is required
    And the book remains unchanged

  Scenario: A PDF can be replaced before content is imported
    Given my library contains "Existing" linked to prepared cover "Cover" and source PDF "PDF"
    And a prepared source PDF named "Replacement" exists
    And I am editing the book "Existing"
    And I have selected prepared source PDF "Replacement"
    When I save my changes
    Then my changes are saved
    And the book links to prepared source PDF "Replacement"
    And the book links to prepared cover "Cover"
    And the original source PDF can no longer be retrieved
