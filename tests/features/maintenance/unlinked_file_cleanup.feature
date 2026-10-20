Feature: Unlinked file cleanup
  Abandoned uploads expire after seven days. Linked files remain available.
  Files removed from a book are queued for deletion regardless of their age.

  Background:
    Given the promoted file database is ready

  Scenario Outline: Expire an abandoned upload
    Given an unlinked <kind> uploaded 8 days ago
    When I run promoted file maintenance once
    Then maintenance succeeds
    And the uploaded file is removed

    Examples:
      | kind       |
      | cover      |
      | source PDF |

  Scenario Outline: Preserve an upload that may still be used
    Given an unlinked <kind> uploaded 6 days ago
    When I run promoted file maintenance once
    Then maintenance succeeds
    And the uploaded file remains available

    Examples:
      | kind       |
      | cover      |
      | source PDF |

  Scenario Outline: Preserve an old file linked to a book
    Given an unlinked <kind> uploaded 30 days ago
    And the uploaded file is linked to a book
    When I run promoted file maintenance once
    Then maintenance succeeds
    And the uploaded file remains available

    Examples:
      | kind       |
      | cover      |
      | source PDF |

  Scenario Outline: Clean up a file removed from its book after the scan boundary passed
    Given an unlinked <kind> uploaded 30 days ago
    And the uploaded file is linked to a book
    And maintenance has already scanned that upload date
    And the book's file is <action>
    When I run promoted file maintenance once
    Then maintenance succeeds
    And the uploaded file is removed

    Examples:
      | kind       | action                       |
      | cover      | replaced                     |
      | source PDF | replaced                     |
      | cover      | cleared                      |
      | source PDF | cleared                      |
      | cover      | removed by deleting the book |
      | source PDF | removed by deleting the book |

  Scenario: Complete a pending deletion left by an earlier run
    Given an unlinked cover uploaded 30 days ago
    And deletion of the uploaded file is pending from an earlier run
    When I run promoted file maintenance once
    Then maintenance succeeds
    And the uploaded file is removed

  Scenario: Unlinked file cleanup preserves temporary uploads
    Given a temporary upload inactive for 25 hours
    When I run promoted file maintenance once
    Then maintenance succeeds
    And the temporary upload remains available
