Feature: Rebuild a book's whole-segment retrieval windows
  Window text and planned embeddings are stored together without changing reader content.

  Scenario: Rebuild an English book
    Given an English book is ready for windowing
    When I rebuild its windows with the local command
    Then stored windows have the assembled text and empty planned embeddings

  Scenario: Rebuild a Kannada book with a long passage
    Given a Kannada book has a long passage and an accepted translation
    When I rebuild its windows with the local command
    Then stored windows have the assembled text and empty planned embeddings

  Scenario: Inspect a dry run
    Given an English book is ready for windowing
    When I dry-run its windows with the local command
    Then assembled windows are reported without storing any rows

  Scenario: Reject an oversized replacement
    Given a Kannada book has a long passage and an accepted translation
    And its windows already exist
    When I rebuild with a budget too small for its complete passage
    Then the command reports the oversized passage and preserves existing windows

  Scenario: Rebuild windows that already have vectors
    Given an English book is ready for windowing
    And its windows already exist
    And its planned embeddings have completed vectors
    When I rebuild its windows with the local command
    Then fresh windows and empty embeddings replace the previous rows

  Scenario Outline: Remove a book's windows
    Given an English book is ready for windowing
    And its windows already exist
    When I <action>
    Then no windows or planned embeddings remain

    Examples:
      | action                                   |
      | clear its windows with the local command |
      | purge its canonical content              |
      | delete the book                          |
