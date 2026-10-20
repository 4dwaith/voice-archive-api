Feature: Clean up orphaned files
  Old files without a cover or PDF record are removed after seven days.

  Background:
    Given the promoted file database is ready

  Scenario: Remove an old orphan left by an interrupted upload
    Given a file without a database record inactive for 8 days
    When I run orphan file maintenance once
    Then maintenance succeeds
    And the orphaned file is removed

  Scenario: Preserve a recent file that could still be saved
    Given a file without a database record inactive for 6 days
    When I run orphan file maintenance once
    Then maintenance succeeds
    And the orphaned file remains available

  Scenario Outline: Preserve an existing cover or source PDF file
    Given an unlinked <kind> uploaded 30 days ago
    And the uploaded file is inactive for 30 days
    When I run orphan file maintenance once
    Then maintenance succeeds
    And the uploaded file remains available

    Examples:
      | kind       |
      | cover      |
      | source PDF |

