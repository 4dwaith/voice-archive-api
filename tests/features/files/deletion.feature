Feature: Delete prepared files that are not linked to a book
  Deletion removes the file record immediately and queues its bytes for maintenance.
  Covers and PDFs without imported content can be removed through book save.
  Imported book PDFs require conditional content purge or whole-book deletion.

  Scenario Outline: Delete an unlinked prepared file
    Given I have uploaded a prepared <kind>
    When I delete the prepared file
    Then the prepared file is removed and its bytes are queued for deletion
    And the stored bytes remain until maintenance runs

    Examples:
      | kind       |
      | cover      |
      | source PDF |

  Scenario Outline: Protect a linked file
    Given I have uploaded a prepared <kind>
    And the prepared file is linked to a book
    When I delete the prepared file
    Then I am told that the prepared file is in use by a book
    And the prepared file and its book link remain available

    Examples:
      | kind       |
      | cover      |
      | source PDF |

  Scenario Outline: A missing file cannot be deleted
    Given the prepared <kind> is missing
    When I delete the prepared file
    Then I am told that the <kind> cannot be found

    Examples:
      | kind       |
      | cover      |
      | source PDF |

  Scenario Outline: Delete a file whose stored bytes are already missing
    Given I have uploaded a prepared <kind>
    And the stored file has been removed
    When I delete the prepared file
    Then the prepared file is removed and its bytes are queued for deletion

    Examples:
      | kind       |
      | cover      |
      | source PDF |

  Scenario Outline: A deleted file cannot be deleted again
    Given I have uploaded a prepared <kind>
    And I have deleted the prepared file
    When I delete the prepared file
    Then I am told that the <kind> cannot be found

    Examples:
      | kind       |
      | cover      |
      | source PDF |
