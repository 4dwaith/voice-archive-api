Feature: Stale temporary file cleanup
  Temporary uploads are removed after more than 24 hours of inactivity.

  Scenario: Remove an inactive temporary upload
    Given a temporary upload inactive for 25 hours
    When I run temporary file maintenance once
    Then maintenance succeeds
    And the temporary upload is removed

  Scenario: Preserve an active temporary upload
    Given a temporary upload inactive for 1 hour
    When I run temporary file maintenance once
    Then maintenance succeeds
    And the temporary upload remains available

  Scenario: Stale temporary file cleanup preserves prepared files
    Given the promoted file database is ready
    And an unlinked cover uploaded 30 days ago
    When I run temporary file maintenance once
    Then maintenance succeeds
    And the uploaded file remains available
