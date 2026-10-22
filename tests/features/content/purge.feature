Feature: Conditionally purge imported book content
  Content purge removes text, PDF, and all section audio together while preserving
  the book and its cover. The caller must identify the state it intends to purge.

  Background:
    Given a book and valid prepared text, PDF, and chapter audio

  Scenario: Purge removes all imported content and durably queues its media
    Given the prepared content has already been imported
    When I purge content using its current state tag
    Then I was told the content was purged successfully
    And the book content is empty and its owned media are unavailable
    And the cover and descriptive book details are preserved

  Scenario Outline: Missing or unsuitable state tags cannot purge content
    Given the prepared content has already been imported
    When I purge content using a <tag> state tag
    Then I was told <reason>
    And the book and prepared files are unchanged

    Examples:
      | tag      | reason                                                  |
      | missing  | the content state must be specified before purging       |
      | stale    | the supplied content state cannot authorize this purge  |
      | weak     | the supplied content state cannot authorize this purge  |
      | wildcard | the supplied content state cannot authorize this purge  |

  Scenario: A delayed purge cannot remove a fresh import
    Given the prepared content was purged and imported again
    When I purge content using the old import state tag
    Then I was told the supplied content state cannot authorize this purge
    And the fresh import has a different content version
    And the book and prepared files are unchanged

  Scenario: Purge also supports a PDF-only book
    Given the prepared PDF is already attached to the book
    When I purge content using its current state tag
    Then I was told the content was purged successfully
    And the book content is empty and its owned media are unavailable
    And the unused chapter audio remain unlinked
    And the cover and descriptive book details are preserved

  Scenario: Purging the current empty state is harmless
    When I purge content using its current state tag
    Then I was told the content was purged successfully
    And the book and prepared files are unchanged

  Scenario: An old empty-state tag cannot remove a newly attached PDF
    Given the prepared PDF is already attached to the book
    When I purge content using a stale state tag
    Then I was told the supplied content state cannot authorize this purge
    And the book and prepared files are unchanged
