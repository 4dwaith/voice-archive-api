Feature: Prepare English summaries for a published book
  Summaries become visible together through the normal book and content reads.
  Failed replacements preserve the summaries readers already have.

  Background:
    Given a published Kannada book is ready for summarization
    And the summary provider is available

  Scenario: Generate and publish summaries
    When I generate summaries with the local command
    Then English summaries are available for the book, sections and passages
    And the previous content version is rejected by paginated reads
    And generation uses the complete canonical source outside publication locks

  Scenario: Repeating an unchanged book avoids provider work
    Given summaries have already been generated
    When I generate summaries with the local command
    Then the command skips generation and preserves the published version

  Scenario: Clear accepted summaries
    Given summaries have already been generated
    When I clear summaries with the local command
    Then all summary reads are empty under a new content version
    And the previous content version is rejected by paginated reads

  Scenario: A failed replacement preserves accepted summaries
    Given summaries have already been generated
    And the book title changes before a rejected replacement
    When I generate summaries with the local command
    Then the command reports failure and preserves the accepted summary reads
